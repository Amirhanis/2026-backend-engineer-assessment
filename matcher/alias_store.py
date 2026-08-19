"""Customer SKU alias lookup.

Loads customer_sku_map.csv, filters out bad entries, and provides
tenant-scoped alias resolution.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from matcher.models import AliasEntry
from matcher.config import ALIAS_MIN_CONFIDENCE


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class AliasStore:
    """Tenant-scoped buyer SKU → item_code alias lookup."""

    def __init__(self):
        # (tenant, customer_id, customer_sku) → [AliasEntry]
        self._aliases: dict[tuple[str, str, str], list[AliasEntry]] = defaultdict(list)
        # (tenant, customer_sku) → [AliasEntry]  (customer-agnostic fallback)
        self._global_aliases: dict[tuple[str, str], list[AliasEntry]] = defaultdict(list)

    def load(self, csv_path: str | None = None,
             active_check: callable = None) -> None:
        """Load alias map from CSV.

        Args:
            csv_path: Path to customer_sku_map.csv.
            active_check: Optional callable(tenant, item_code) → bool
                          to filter out aliases pointing to inactive items.
        """
        if csv_path is None:
            csv_path = os.path.join(DATA_DIR, "customer_sku_map.csv")

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tenant = row["tenant"].strip()
                cust_id = row["customer_id"].strip()
                cust_sku = row["customer_sku"].strip()
                item_code = row["item_code"].strip()
                confidence = float(row.get("confidence", "1.0") or "1.0")
                valid_to = row.get("valid_to", "").strip() or None
                source = row.get("source", "").strip()

                # --- Filtering ---
                # Skip expired mappings
                if valid_to and valid_to < "2026-08-01":
                    continue

                # Skip low-confidence inferred matches
                if confidence < ALIAS_MIN_CONFIDENCE:
                    continue

                # Skip mappings to disabled/-OLD items
                if active_check and not active_check(tenant, item_code):
                    continue

                entry = AliasEntry(
                    tenant=tenant,
                    customer_id=cust_id,
                    customer_sku=cust_sku,
                    item_code=item_code,
                    description=row.get("customer_description", "").strip(),
                    valid_from=row.get("valid_from", "").strip(),
                    valid_to=valid_to,
                    source=source,
                    confidence=confidence,
                )
                self._aliases[(tenant, cust_id, cust_sku)].append(entry)
                self._global_aliases[(tenant, cust_sku)].append(entry)

    def lookup(self, tenant: str, customer_id: str,
               buyer_sku: str) -> str | None:
        """Look up a buyer SKU for a specific customer.

        Returns item_code if exactly one active, high-confidence match.
        Returns None if ambiguous or no match.
        Enforces tenant isolation: will never return a cross-tenant code.
        """
        if not buyer_sku:
            return None

        # Try customer-specific first
        entries = self._aliases.get((tenant, customer_id, buyer_sku), [])
        if not entries:
            # Fallback to any customer in same tenant
            entries = self._global_aliases.get((tenant, buyer_sku), [])

        if not entries:
            return None

        # Pick highest confidence; if tie, prefer confirmed_order over inferred
        source_priority = {"confirmed_order": 3, "manual_import": 2, "inferred_match": 1}
        entries.sort(key=lambda e: (e.confidence, source_priority.get(e.source, 0)),
                     reverse=True)

        best = entries[0]

        # If multiple entries point to different item_codes, it's ambiguous
        unique_codes = set(e.item_code for e in entries)
        if len(unique_codes) > 1:
            return None  # ambiguous

        return best.item_code

    def get_confidence(self, tenant: str, customer_id: str,
                       buyer_sku: str) -> float:
        """Get the confidence of the best alias match."""
        entries = self._aliases.get((tenant, customer_id, buyer_sku), [])
        if not entries:
            entries = self._global_aliases.get((tenant, buyer_sku), [])
        if not entries:
            return 0.0
        return max(e.confidence for e in entries)
