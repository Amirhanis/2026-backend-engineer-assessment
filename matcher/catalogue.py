"""Catalogue loading, indexing, and lookup.

Builds in-memory indexes from CSV files for fast deterministic lookup:
- barcode → item_code (unique active items only)
- item_code → CatalogueItem
- normalised_name → [item_codes]  (for lexical matching)
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from matcher.models import CatalogueItem
from matcher.normalizer import normalise_text


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class Catalogue:
    """Tenant-scoped catalogue with multiple lookup indexes."""

    def __init__(self):
        self._items: dict[str, dict[str, CatalogueItem]] = {}   # tenant → {item_code → item}
        self._barcode_idx: dict[str, dict[str, str]] = {}        # tenant → {barcode → item_code}
        self._active_items: dict[str, list[CatalogueItem]] = {}  # tenant → [active items]
        self._brands: dict[str, set[str]] = {}                   # tenant → {brand names}
        self._item_groups: dict[str, set[str]] = {}              # tenant → {item groups}
        self._name_to_codes: dict[str, dict[str, list[str]]] = {}  # tenant → {norm_name → [codes]}

    def load(self, tenant: str, csv_path: str) -> None:
        """Load a catalogue CSV for one tenant."""
        items = {}
        barcode_idx = {}
        active = []
        brands = set()
        groups = set()
        name_to_codes = defaultdict(list)

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                item_code = row["item_code"].strip()
                disabled = row.get("disabled", "0").strip() == "1"
                is_old = "-OLD" in item_code
                is_bulk = item_code.endswith("B") and not item_code.endswith("-OLD")
                barcode = row.get("barcode", "").strip() or None
                mfr_part = row.get("manufacturer_part_no", "").strip() or None

                try:
                    price = float(row.get("list_price", "0") or "0")
                except ValueError:
                    price = 0.0

                brand = row.get("brand", "").strip()
                item_name = row.get("item_name", "").strip()
                norm_name = normalise_text(item_name)

                item = CatalogueItem(
                    item_code=item_code,
                    item_name=item_name,
                    description=row.get("description", "").strip(),
                    brand=brand,
                    item_group=row.get("item_group", "").strip(),
                    stock_uom=row.get("stock_uom", "").strip(),
                    barcode=barcode,
                    manufacturer_part_no=mfr_part,
                    disabled=disabled,
                    list_price=price,
                    is_old=is_old,
                    is_bulk=is_bulk,
                    normalised_name=norm_name,
                )
                items[item_code] = item

                if not disabled and not is_old:
                    active.append(item)
                    if brand:
                        brands.add(brand)
                    if item.item_group:
                        groups.add(item.item_group)
                    name_to_codes[norm_name].append(item_code)

                    # Index barcodes for active items only
                    if barcode:
                        # Only index if unique; if duplicate barcode, remove
                        # (ambiguous barcodes should not auto-match)
                        if barcode in barcode_idx:
                            barcode_idx[barcode] = None  # mark ambiguous
                        else:
                            barcode_idx[barcode] = item_code

        # Remove ambiguous barcodes
        barcode_idx = {k: v for k, v in barcode_idx.items() if v is not None}

        self._items[tenant] = items
        self._barcode_idx[tenant] = barcode_idx
        self._active_items[tenant] = active
        self._brands[tenant] = brands
        self._item_groups[tenant] = groups
        self._name_to_codes[tenant] = dict(name_to_codes)

    def load_defaults(self) -> None:
        """Load both default catalogues."""
        acme_path = os.path.join(DATA_DIR, "catalogue_acme.csv")
        nordic_path = os.path.join(DATA_DIR, "catalogue_nordic.csv")
        if os.path.exists(acme_path):
            self.load("acme", acme_path)
        if os.path.exists(nordic_path):
            self.load("nordic", nordic_path)

    def get_item(self, tenant: str, item_code: str) -> CatalogueItem | None:
        """Look up an item by code. Returns None if not found."""
        return self._items.get(tenant, {}).get(item_code)

    def is_active(self, tenant: str, item_code: str) -> bool:
        """Check if an item_code exists and is active (not disabled, not -OLD)."""
        item = self.get_item(tenant, item_code)
        return item is not None and not item.disabled and not item.is_old

    def lookup_barcode(self, tenant: str, barcode: str) -> str | None:
        """Look up a barcode → item_code. Returns None if not found or ambiguous."""
        return self._barcode_idx.get(tenant, {}).get(barcode)

    def lookup_item_code(self, tenant: str, code: str) -> CatalogueItem | None:
        """Direct item_code lookup, active items only."""
        item = self.get_item(tenant, code)
        if item and not item.disabled and not item.is_old:
            return item
        return None

    def get_active_items(self, tenant: str) -> list[CatalogueItem]:
        """Get all active items for a tenant."""
        return self._active_items.get(tenant, [])

    def get_brands(self, tenant: str) -> set[str]:
        """Get all known brand names for a tenant."""
        return self._brands.get(tenant, set())

    def get_items_by_name(self, tenant: str, norm_name: str) -> list[str]:
        """Get item codes matching a normalised name exactly."""
        return self._name_to_codes.get(tenant, {}).get(norm_name, [])

    def has_tenant(self, tenant: str) -> bool:
        return tenant in self._items
