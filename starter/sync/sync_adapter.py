#!/usr/bin/env python3
"""Two-way sync between our local item store and the ERP in fake_erp.py.

This code is in production. It mostly works. Ops have raised three tickets:

  MAIA-812  "some items never appear on our side until someone edits them"
  MAIA-830  "price history shows two updates a second apart, we only made one"
  MAIA-844  "an edit made in the ERP was overwritten by our older value"

Task 5 is about this file. Read the tickets as symptoms, not diagnoses.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

from fake_erp import ErpConflict, ErpTimeout, FakeErp


@dataclass
class LocalRecord:
    external_id: str
    payload: dict
    remote_version: int
    updated_at_utc: str          # "YYYY-MM-DD HH:MM:SS", UTC
    dirty: bool = False


@dataclass
class LocalStore:
    """Stands in for our Postgres tables. Committed writes only."""
    records: dict = field(default_factory=dict)
    cursor: str | None = None
    applied_log: list = field(default_factory=list)

    def upsert(self, rec: LocalRecord) -> None:
        self.records[rec.external_id] = rec
        self.applied_log.append((rec.external_id, rec.remote_version))

    def set_cursor(self, cursor: str | None) -> None:
        self.cursor = cursor


def now_utc() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def idempotency_key(external_id: str, payload: dict) -> str:
    """Deterministic idempotency key per (external_id, payload) (Fix MAIA-830)."""
    blob = json.dumps({"id": external_id, "payload": payload}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def pull(erp: FakeErp, store: LocalStore, page_size: int = 50) -> int:
    """Pull remote changes since the stored cursor into the local store."""
    pulled = 0
    current_limit = page_size

    while True:
        page = erp.list_changes(since=store.cursor, limit=current_limit)
        if not page:
            break

        if len(page) < current_limit:
            # Final page of changes: process everything and advance cursor to the end
            for rec in page:
                local = store.records.get(rec.external_id)
                # Defect 4: Use version comparison instead of UTC vs Local timezone string comparison
                if local and local.dirty and local.remote_version >= rec.version:
                    continue
                store.upsert(LocalRecord(
                    external_id=rec.external_id,
                    payload=dict(rec.payload),
                    remote_version=rec.version,
                    updated_at_utc=rec.updated_at,
                    dirty=False,
                ))
                pulled += 1
            # Defect 5: Update cursor after processing
            store.set_cursor(page[-1].updated_at)
            break
        else:
            # Full page: check for timestamp ties across the page boundary (Defect 1 / MAIA-812)
            if page[0].updated_at == page[-1].updated_at:
                # All records in this page share the same timestamp. Expand fetch limit to get past the tie
                current_limit += page_size
                continue

            # Multiple timestamps in page. Find the last timestamp strictly before the boundary timestamp
            boundary_ts = page[-1].updated_at
            safe_recs = [r for r in page if r.updated_at < boundary_ts]
            last_safe_ts = safe_recs[-1].updated_at

            for rec in safe_recs:
                local = store.records.get(rec.external_id)
                if local and local.dirty and local.remote_version >= rec.version:
                    continue
                store.upsert(LocalRecord(
                    external_id=rec.external_id,
                    payload=dict(rec.payload),
                    remote_version=rec.version,
                    updated_at_utc=rec.updated_at,
                    dirty=False,
                ))
                pulled += 1

            store.set_cursor(last_safe_ts)
            current_limit = page_size

    return pulled


def push(erp: FakeErp, store: LocalStore, max_attempts: int = 3) -> int:
    """Push locally-dirty records to the ERP."""
    pushed = 0
    for rec in list(store.records.values()):
        if not rec.dirty:
            continue
        for attempt in range(max_attempts):
            try:
                remote = erp.write(
                    rec.external_id, rec.payload,
                    base_version=rec.remote_version,
                    idempotency_key=idempotency_key(rec.external_id, rec.payload)
                )
            except ErpTimeout:
                # Defect 6 / MAIA-830: Check if the write committed before retrying
                current = erp.get(rec.external_id)
                if current and current.version > rec.remote_version:
                    remote = current
                else:
                    continue  # transient timeout, retry with same deterministic key
            except ErpConflict:
                # Defect 3 / MAIA-844: Conflict means remote was edited. Accept remote to preserve ERP edits.
                current = erp.get(rec.external_id)
                if current:
                    rec.payload = dict(current.payload)
                    rec.remote_version = current.version
                    rec.updated_at_utc = current.updated_at
                    rec.dirty = False
                    store.upsert(rec)
                break

            rec.remote_version = remote.version
            rec.updated_at_utc = remote.updated_at
            rec.dirty = False
            store.upsert(rec)
            pushed += 1
            break
    return pushed


def sync(erp: FakeErp, store: LocalStore) -> dict:
    return {"pulled": pull(erp, store), "pushed": push(erp, store)}
