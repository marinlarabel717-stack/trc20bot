from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from itertools import cycle
from typing import Any

import aiohttp

from .config import Settings
from .models import TransferEvent, WatchAddress


logger = logging.getLogger(__name__)


class TronGridRateLimitError(RuntimeError):
    """Raised when TronGrid is temporarily rate limited."""


class TronGridClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._api_cycle = cycle(settings.trongrid_api_keys or [""])
        self._rate_limited_until = 0.0
        self._balance_cache: dict[str, tuple[float, tuple[float, float]]] = {}
        self._balance_cache_ttl_seconds = 60.0
        self._balance_cache_max_entries = 256

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if api_key:
            headers["TRON-PRO-API-KEY"] = api_key
        return headers

    def _set_rate_limit_cooldown(self, retry_after: str | None = None) -> None:
        cooldown_seconds = 30.0
        if retry_after:
            try:
                cooldown_seconds = max(cooldown_seconds, float(retry_after))
            except ValueError:
                pass
        self._rate_limited_until = max(self._rate_limited_until, time.time() + cooldown_seconds)

    def _ensure_not_rate_limited(self) -> None:
        remaining = self._rate_limited_until - time.time()
        if remaining > 0:
            raise TronGridRateLimitError(f"TronGrid rate limited, retry after {remaining:.0f}s")

    async def _request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_not_rate_limited()
        session = await self._session_or_create()
        candidates = self.settings.trongrid_api_keys or [""]
        last_error: Exception | None = None
        for _ in candidates:
            api_key = next(self._api_cycle)
            try:
                async with session.get(
                    f"{self.settings.trongrid_api_base}{path}",
                    params=params,
                    headers=self._headers(api_key),
                ) as response:
                    if response.status in {403, 429}:
                        self._set_rate_limit_cooldown(response.headers.get("Retry-After"))
                        last_error = TronGridRateLimitError(f"TronGrid rate limited: {response.status}")
                        continue
                    response.raise_for_status()
                    payload = await response.json()
                    if isinstance(payload, dict):
                        return payload
                    return {}
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("TronGrid request failed")

    def _prune_balance_cache(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        expired = [
            address
            for address, (cached_at, _) in self._balance_cache.items()
            if now - cached_at > self._balance_cache_ttl_seconds
        ]
        for address in expired:
            self._balance_cache.pop(address, None)
        overflow = len(self._balance_cache) - self._balance_cache_max_entries
        if overflow > 0:
            oldest_addresses = sorted(self._balance_cache.items(), key=lambda item: item[1][0])[:overflow]
            for address, _ in oldest_addresses:
                self._balance_cache.pop(address, None)

    async def iter_usdt_transfer_pages(self, watch: WatchAddress) -> AsyncIterator[list[dict[str, Any]]]:
        lookback_ms = self.settings.lookback_minutes * 60 * 1000
        min_timestamp = (
            max(0, int(watch.last_scan_ts) - 60_000)
            if watch.last_scan_ts > 0
            else 0
        )
        if min_timestamp == 0:
            min_timestamp = max(0, int(time.time() * 1000) - lookback_ms)

        params = {
            "limit": str(self.settings.page_limit),
            "order_by": "block_timestamp,desc",
            "min_timestamp": str(min_timestamp),
            "contract_address": self.settings.trc20_usdt_contract,
        }
        if self.settings.only_confirmed:
            params["only_confirmed"] = "true"

        fingerprint = ""
        seen: set[str] = set()
        for _ in range(self.settings.max_pages):
            request_params = dict(params)
            if fingerprint:
                if fingerprint in seen:
                    break
                seen.add(fingerprint)
                request_params["fingerprint"] = fingerprint
            try:
                payload = await self._request_json(
                    f"/accounts/{watch.address}/transactions/trc20",
                    request_params,
                )
            except TronGridRateLimitError:
                logger.warning("TronGrid rate limited while fetching transfers for %s", watch.address)
                break
            data = payload.get("data") or []
            if not isinstance(data, list):
                data = []
            page = [item for item in data if isinstance(item, dict)]
            if page:
                yield page
            fingerprint = str(((payload.get("meta") or {}).get("fingerprint")) or "").strip()
            if not fingerprint or not data:
                break

    async def fetch_account_balance(self, address: str) -> tuple[float, float]:
        self._prune_balance_cache()
        cached = self._balance_cache.get(address)
        now = time.time()
        if cached and now - cached[0] <= self._balance_cache_ttl_seconds:
            return cached[1]

        trx_balance = 0.0
        usdt_balance = 0.0

        try:
            account_payload = await self._request_json(f"/accounts/{address}")
        except TronGridRateLimitError:
            if cached:
                logger.warning("Using cached balance for %s due to TronGrid rate limit", address)
                return cached[1]
            raise
        account_rows = account_payload.get("data") or []
        if isinstance(account_rows, list) and account_rows:
            row = account_rows[0] if isinstance(account_rows[0], dict) else {}
            trx_balance = float(row.get("balance") or 0) / 1_000_000
            trc20_rows = row.get("trc20") or []
            usdt_balance = _extract_usdt_balance(trc20_rows, self.settings.trc20_usdt_contract)

        if usdt_balance <= 0:
            try:
                token_payload = await self._request_json(f"/accounts/{address}/trc20")
                token_rows = token_payload.get("data") or []
                usdt_balance = _extract_usdt_balance(token_rows, self.settings.trc20_usdt_contract)
            except TronGridRateLimitError:
                if cached:
                    logger.warning("Using cached USDT balance for %s due to TronGrid rate limit", address)
                    return cached[1]
                raise
        balances = (trx_balance, usdt_balance)
        self._balance_cache[address] = (now, balances)
        self._prune_balance_cache(now)
        return balances


def _extract_usdt_balance(rows: Any, contract_address: str) -> float:
    if not isinstance(rows, list):
        return 0.0
    for row in rows:
        if isinstance(row, dict) and contract_address in row:
            raw_balance = row.get(contract_address)
            return float(Decimal(str(raw_balance or "0")) / (Decimal(10) ** 6))
        if not isinstance(row, dict):
            continue
        contract = str(row.get("tokenId") or row.get("token_id") or row.get("contract_address") or "").strip()
        if contract != contract_address:
            continue
        raw_balance = row.get("balance")
        decimals = int(row.get("tokenDecimal") or row.get("token_decimal") or row.get("tokenInfo", {}).get("tokenDecimal") or 6)
        return float(Decimal(str(raw_balance or "0")) / (Decimal(10) ** max(0, decimals)))
    return 0.0


def normalize_transfer(item: dict[str, Any], watch: WatchAddress, contract_address: str) -> TransferEvent | None:
    tx_hash = str(item.get("transaction_id") or item.get("transactionId") or item.get("id") or "").strip()
    if not tx_hash:
        return None

    event_type = str(item.get("type") or item.get("event_type") or "").strip().lower()
    if event_type and "transfer" not in event_type:
        return None

    result = str(item.get("result") or item.get("transaction_result") or "").strip().upper()
    if result and result not in {"SUCCESS", "SUCESS"}:
        return None

    token_info = item.get("token_info") if isinstance(item.get("token_info"), dict) else {}
    token_contract = str(token_info.get("address") or item.get("contract_address") or "").strip()
    token_symbol = str(token_info.get("symbol") or item.get("tokenName") or "USDT").strip().upper()
    if contract_address and token_contract and token_contract != contract_address:
        return None
    if token_symbol and token_symbol != "USDT":
        return None

    owner_address = watch.address
    from_address = str(item.get("from") or item.get("from_address") or "").strip()
    to_address = str(item.get("to") or item.get("to_address") or "").strip()
    if not from_address or not to_address or from_address == to_address:
        return None

    if to_address == owner_address:
        direction = "in"
    elif from_address == owner_address:
        direction = "out"
    else:
        return None

    decimals = int(token_info.get("decimals") or 6)
    amount = Decimal(str(item.get("value") or "0")) / (Decimal(10) ** max(0, decimals))
    amount = amount.quantize(Decimal("0.000001"))
    if amount <= 0:
        return None

    return TransferEvent(
        address_id=watch.id,
        owner_address=watch.address,
        remark=watch.remark,
        tx_hash=tx_hash,
        direction=direction,
        amount=float(amount),
        from_address=from_address,
        to_address=to_address,
        block_timestamp=int(item.get("block_timestamp") or item.get("block_ts") or 0),
        block_number=int(item.get("block") or item.get("block_number") or 0),
        confirmed=bool(item.get("confirmed", True)),
        contract_address=token_contract or contract_address,
        raw_json=json.dumps(item, ensure_ascii=False, separators=(",", ":")),
    )
