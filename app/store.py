from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import StatsSummary, TransferEvent, WatchAddress


class Store:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS watch_addresses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL UNIQUE,
                    remark TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_scan_ts INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS transfer_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address_id INTEGER NOT NULL,
                    owner_address TEXT NOT NULL,
                    remark TEXT NOT NULL DEFAULT '',
                    tx_hash TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    amount REAL NOT NULL,
                    from_address TEXT NOT NULL,
                    to_address TEXT NOT NULL,
                    block_timestamp INTEGER NOT NULL,
                    block_number INTEGER NOT NULL DEFAULT 0,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    contract_address TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(address_id, tx_hash, direction),
                    FOREIGN KEY(address_id) REFERENCES watch_addresses(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_transfer_events_timestamp
                ON transfer_events(block_timestamp DESC);

                CREATE INDEX IF NOT EXISTS idx_transfer_events_tx_hash
                ON transfer_events(tx_hash);
                """
            )

    def add_watch(self, address: str, remark: str) -> WatchAddress:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, created_at, last_scan_ts FROM watch_addresses WHERE address = ?",
                (address,),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO watch_addresses(address, remark, created_at, last_scan_ts)
                    VALUES(?, ?, ?, 0)
                    """,
                    (address, remark, now),
                )
                watch_id = int(cursor.lastrowid)
                created_at = now
                last_scan_ts = 0
            else:
                watch_id = int(row["id"])
                created_at = str(row["created_at"])
                last_scan_ts = int(row["last_scan_ts"] or 0)
                conn.execute(
                    "UPDATE watch_addresses SET remark = ? WHERE id = ?",
                    (remark, watch_id),
                )
        return WatchAddress(
            id=watch_id,
            address=address,
            remark=remark,
            created_at=created_at,
            last_scan_ts=last_scan_ts,
        )

    def delete_watch(self, identity: str) -> bool:
        text = identity.strip()
        with self._connect() as conn:
            if text.isdigit():
                cursor = conn.execute("DELETE FROM watch_addresses WHERE id = ?", (int(text),))
            else:
                cursor = conn.execute("DELETE FROM watch_addresses WHERE address = ?", (text,))
            return cursor.rowcount > 0

    def list_watches(self) -> list[WatchAddress]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, address, remark, created_at, last_scan_ts
                FROM watch_addresses
                ORDER BY id ASC
                """
            ).fetchall()
        return [
            WatchAddress(
                id=int(row["id"]),
                address=str(row["address"]),
                remark=str(row["remark"] or ""),
                created_at=str(row["created_at"]),
                last_scan_ts=int(row["last_scan_ts"] or 0),
            )
            for row in rows
        ]

    def get_watch(self, identity: str) -> WatchAddress | None:
        text = identity.strip()
        with self._connect() as conn:
            if text.isdigit():
                row = conn.execute(
                    """
                    SELECT id, address, remark, created_at, last_scan_ts
                    FROM watch_addresses WHERE id = ?
                    """,
                    (int(text),),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, address, remark, created_at, last_scan_ts
                    FROM watch_addresses WHERE address = ?
                    """,
                    (text,),
                ).fetchone()
        if row is None:
            return None
        return WatchAddress(
            id=int(row["id"]),
            address=str(row["address"]),
            remark=str(row["remark"] or ""),
            created_at=str(row["created_at"]),
            last_scan_ts=int(row["last_scan_ts"] or 0),
        )

    def update_watch_scan_ts(self, watch_id: int, last_scan_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE watch_addresses SET last_scan_ts = ? WHERE id = ?",
                (int(last_scan_ts), int(watch_id)),
            )

    def insert_event(self, event: TransferEvent) -> bool:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO transfer_events(
                        address_id, owner_address, remark, tx_hash, direction, amount,
                        from_address, to_address, block_timestamp, block_number, confirmed,
                        contract_address, raw_json, created_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.address_id,
                        event.owner_address,
                        event.remark,
                        event.tx_hash,
                        event.direction,
                        event.amount,
                        event.from_address,
                        event.to_address,
                        event.block_timestamp,
                        event.block_number,
                        1 if event.confirmed else 0,
                        event.contract_address,
                        event.raw_json,
                        now,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def list_events(
        self,
        *,
        since_ts: int | None = None,
        limit: int = 20,
        watch_id: int | None = None,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT id, address_id, owner_address, remark, tx_hash, direction, amount,
                   from_address, to_address, block_timestamp, block_number, confirmed,
                   contract_address
            FROM transfer_events
            WHERE 1 = 1
        """
        params: list[Any] = []
        if since_ts is not None:
            query += " AND block_timestamp >= ?"
            params.append(int(since_ts))
        if watch_id is not None:
            query += " AND address_id = ?"
            params.append(int(watch_id))
        query += " ORDER BY block_timestamp DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    def get_stats(self, since_ts: int | None = None, until_ts: int | None = None) -> StatsSummary:
        query = """
            SELECT
                SUM(CASE WHEN direction = 'in' THEN 1 ELSE 0 END) AS count_in,
                SUM(CASE WHEN direction = 'out' THEN 1 ELSE 0 END) AS count_out,
                SUM(CASE WHEN direction = 'in' THEN amount ELSE 0 END) AS amount_in,
                SUM(CASE WHEN direction = 'out' THEN amount ELSE 0 END) AS amount_out
            FROM transfer_events
            WHERE 1 = 1
        """
        params: list[Any] = []
        if since_ts is not None:
            query += " AND block_timestamp >= ?"
            params.append(int(since_ts))
        if until_ts is not None:
            query += " AND block_timestamp < ?"
            params.append(int(until_ts))
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return StatsSummary(
            count_in=int(row["count_in"] or 0),
            count_out=int(row["count_out"] or 0),
            amount_in=float(row["amount_in"] or 0),
            amount_out=float(row["amount_out"] or 0),
        )

    def find_events_by_tx_hash(self, tx_hash: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT id, address_id, owner_address, remark, tx_hash, direction, amount,
                       from_address, to_address, block_timestamp, block_number, confirmed,
                       contract_address
                FROM transfer_events
                WHERE tx_hash = ?
                ORDER BY block_timestamp DESC
                """,
                (tx_hash.strip(),),
            ).fetchall()
