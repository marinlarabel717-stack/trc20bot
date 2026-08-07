from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WatchAddress:
    id: int
    address: str
    remark: str
    created_at: str
    last_scan_ts: int


@dataclass(slots=True)
class TransferEvent:
    address_id: int
    owner_address: str
    remark: str
    tx_hash: str
    direction: str
    amount: float
    from_address: str
    to_address: str
    block_timestamp: int
    block_number: int
    confirmed: bool
    contract_address: str
    raw_json: str


@dataclass(slots=True)
class StatsSummary:
    count_in: int
    count_out: int
    amount_in: float
    amount_out: float

    @property
    def net(self) -> float:
        return self.amount_in - self.amount_out
