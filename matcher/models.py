"""Data models for the matcher pipeline."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CatalogueItem:
    """One row from a tenant catalogue."""
    item_code: str
    item_name: str
    description: str
    brand: str
    item_group: str
    stock_uom: str
    barcode: Optional[str]
    manufacturer_part_no: Optional[str]
    disabled: bool
    list_price: float
    # Derived
    is_old: bool = False
    is_bulk: bool = False
    normalised_name: str = ""


@dataclass
class AliasEntry:
    """One row from customer_sku_map, after filtering."""
    tenant: str
    customer_id: str
    customer_sku: str
    item_code: str
    description: str
    valid_from: str
    valid_to: Optional[str]
    source: str
    confidence: float


@dataclass
class Candidate:
    """A candidate item_code with its score and provenance."""
    item_code: str
    score: float
    stage: str           # barcode_hit, alias_exact, lexical, etc.
    item_name: str = ""

    def __lt__(self, other):
        return self.score < other.score


@dataclass
class MatchResult:
    """The output of matching one order line."""
    line_id: str
    item_code: str          # blank when abstaining
    confidence: float       # [0, 1]
    decision: str           # auto, review, reject
    reason_code: str        # short stable token
    candidates: list[Candidate] = field(default_factory=list)

    def candidates_str(self) -> str:
        """Format candidates as 'code:score|code:score|...' for CSV output."""
        top3 = sorted(self.candidates, key=lambda c: c.score, reverse=True)[:3]
        return "|".join(f"{c.item_code}:{c.score:.4f}" for c in top3)
