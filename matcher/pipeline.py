"""Main matching pipeline.

Stages (cheapest first, deterministic):
1. Not-item filter → reject non-product lines
2. Barcode exact match → high-confidence auto
3. Buyer SKU alias lookup → high-confidence auto
4. Direct item_code match → high-confidence auto
5. Lexical scoring → candidate generation
6. Twin detection → ambiguity guard
7. Confidence arbitration → auto / review / reject

Every decision carries a machine-readable reason_code.
Tenant isolation is enforced at every stage.
"""
from __future__ import annotations

import re
from matcher.models import MatchResult, Candidate
from matcher.catalogue import Catalogue
from matcher.alias_store import AliasStore
from matcher.scorer import score_candidates, detect_twins
from matcher.normalizer import normalise_text, expand_abbreviations
from matcher.config import (
    AUTO_THRESHOLD, REVIEW_THRESHOLD, TWIN_GAP_MIN,
    BARCODE_CONFIDENCE, ALIAS_CONFIDENCE, ITEM_CODE_DIRECT_CONFIDENCE,
    NOT_ITEM_PATTERNS, OUT_OF_CATALOGUE_KEYWORDS,
    calibrate_confidence,
)


class MatcherPipeline:
    """Service-shaped matcher: one order line in, one MatchResult out."""

    def __init__(self, catalogue: Catalogue, alias_store: AliasStore):
        self.catalogue = catalogue
        self.alias_store = alias_store

    def match(
        self,
        line_id: str,
        tenant: str,
        raw_text: str,
        customer_id: str = "",
        buyer_sku: str = "",
        raw_barcode: str = "",
        **kwargs,
    ) -> MatchResult:
        """Match one order line. Returns MatchResult with decision and evidence."""

        # ── Guard: tenant must exist ─────────────────────────────
        if not self.catalogue.has_tenant(tenant):
            return MatchResult(
                line_id=line_id, item_code="", confidence=0.0,
                decision="reject", reason_code="unknown_tenant",
            )

        # ── Stage 1: Not-item filter ─────────────────────────────
        result = self._check_not_item(line_id, raw_text)
        if result:
            return result

        # ── Stage 2: Barcode exact match ─────────────────────────
        result = self._check_barcode(line_id, tenant, raw_barcode)
        if result:
            return result

        # ── Stage 3: Buyer SKU alias lookup ──────────────────────
        result = self._check_alias(line_id, tenant, customer_id, buyer_sku)
        if result:
            return result

        # ── Stage 4: Direct item_code match ──────────────────────
        result = self._check_direct_code(line_id, tenant, raw_text, buyer_sku)
        if result:
            return result

        # ── Stage 5: Lexical scoring ─────────────────────────────
        active_items = self.catalogue.get_active_items(tenant)
        brands = self.catalogue.get_brands(tenant)
        candidates = score_candidates(raw_text, active_items, brands, top_n=10)

        if not candidates:
            return MatchResult(
                line_id=line_id, item_code="", confidence=0.0,
                decision="reject", reason_code="no_candidate_above_floor",
            )

        # ── Stage 6: Twin detection ──────────────────────────────
        best = candidates[0]
        is_twin = detect_twins(candidates, TWIN_GAP_MIN)

        # ── Stage 7: Confidence arbitration ──────────────────────
        confidence = calibrate_confidence(best.score)

        if best.score >= AUTO_THRESHOLD and not is_twin:
            return MatchResult(
                line_id=line_id,
                item_code=best.item_code,
                confidence=confidence,
                decision="auto",
                reason_code="lexical_unique",
                candidates=candidates[:3],
            )
        elif is_twin and best.score >= REVIEW_THRESHOLD:
            return MatchResult(
                line_id=line_id,
                item_code="",
                confidence=confidence,
                decision="review",
                reason_code="ambiguous_twins",
                candidates=candidates[:3],
            )
        elif best.score >= REVIEW_THRESHOLD:
            return MatchResult(
                line_id=line_id,
                item_code="",
                confidence=confidence,
                decision="review",
                reason_code="low_confidence",
                candidates=candidates[:3],
            )
        else:
            return MatchResult(
                line_id=line_id,
                item_code="",
                confidence=confidence,
                decision="reject",
                reason_code="no_candidate_above_floor",
                candidates=candidates[:3],
            )

    def _check_not_item(self, line_id: str, raw_text: str) -> MatchResult | None:
        """Stage 1: Detect non-product lines."""
        text_lower = raw_text.strip().lower()

        # Check regex patterns
        for pattern in NOT_ITEM_PATTERNS:
            if re.search(pattern, text_lower):
                return MatchResult(
                    line_id=line_id, item_code="", confidence=0.95,
                    decision="reject", reason_code="not_an_item",
                )

        # Check out-of-catalogue keywords
        for keyword in OUT_OF_CATALOGUE_KEYWORDS:
            if keyword in text_lower:
                return MatchResult(
                    line_id=line_id, item_code="", confidence=0.90,
                    decision="reject", reason_code="out_of_catalogue",
                )

        # Very short text (< 4 chars) is likely not an item
        if len(text_lower.replace(" ", "")) < 4:
            return MatchResult(
                line_id=line_id, item_code="", confidence=0.85,
                decision="reject", reason_code="not_an_item",
            )

        return None

    def _check_barcode(self, line_id: str, tenant: str,
                       raw_barcode: str) -> MatchResult | None:
        """Stage 2: Barcode exact match."""
        if not raw_barcode or not raw_barcode.strip():
            return None

        barcode = raw_barcode.strip()
        item_code = self.catalogue.lookup_barcode(tenant, barcode)
        if item_code:
            item = self.catalogue.get_item(tenant, item_code)
            return MatchResult(
                line_id=line_id,
                item_code=item_code,
                confidence=BARCODE_CONFIDENCE,
                decision="auto",
                reason_code="barcode_hit",
                candidates=[Candidate(item_code, 100.0, "barcode",
                                      item.item_name if item else "")],
            )
        return None

    def _check_alias(self, line_id: str, tenant: str,
                     customer_id: str, buyer_sku: str) -> MatchResult | None:
        """Stage 3: Buyer SKU alias lookup."""
        if not buyer_sku or not buyer_sku.strip():
            return None

        item_code = self.alias_store.lookup(tenant, customer_id, buyer_sku)
        if item_code and self.catalogue.is_active(tenant, item_code):
            conf = self.alias_store.get_confidence(tenant, customer_id, buyer_sku)
            item = self.catalogue.get_item(tenant, item_code)
            return MatchResult(
                line_id=line_id,
                item_code=item_code,
                confidence=min(ALIAS_CONFIDENCE, conf),
                decision="auto",
                reason_code="alias_exact",
                candidates=[Candidate(item_code, 100.0, "alias",
                                      item.item_name if item else "")],
            )
        return None

    def _check_direct_code(self, line_id: str, tenant: str,
                           raw_text: str, buyer_sku: str) -> MatchResult | None:
        """Stage 4: Check if raw_text or buyer_sku is literally an item_code."""
        # Check buyer_sku as item_code
        if buyer_sku:
            sku = buyer_sku.strip().upper()
            item = self.catalogue.lookup_item_code(tenant, sku)
            if item:
                return MatchResult(
                    line_id=line_id,
                    item_code=item.item_code,
                    confidence=ITEM_CODE_DIRECT_CONFIDENCE,
                    decision="auto",
                    reason_code="direct_code",
                    candidates=[Candidate(item.item_code, 100.0, "direct",
                                          item.item_name)],
                )

        # Check if raw_text contains an item code pattern
        text = raw_text.strip().upper()
        # ACM-XXXX#### or NRD-XXXX####
        prefix = "ACM-" if tenant == "acme" else "NRD-"
        code_pattern = re.compile(
            re.escape(prefix) + r"[A-Z]{4}\d{3,4}[B]?(?:-OLD)?",
            re.IGNORECASE,
        )
        match = code_pattern.search(text)
        if match:
            code = match.group(0).upper()
            item = self.catalogue.lookup_item_code(tenant, code)
            if item:
                return MatchResult(
                    line_id=line_id,
                    item_code=item.item_code,
                    confidence=ITEM_CODE_DIRECT_CONFIDENCE,
                    decision="auto",
                    reason_code="direct_code",
                    candidates=[Candidate(item.item_code, 100.0, "direct",
                                          item.item_name)],
                )
        return None


def build_pipeline(data_dir: str | None = None) -> MatcherPipeline:
    """Build a ready-to-use pipeline with default data."""
    import os

    if data_dir is None:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

    catalogue = Catalogue()
    catalogue.load("acme", os.path.join(data_dir, "catalogue_acme.csv"))
    catalogue.load("nordic", os.path.join(data_dir, "catalogue_nordic.csv"))

    alias_store = AliasStore()
    alias_store.load(
        os.path.join(data_dir, "customer_sku_map.csv"),
        active_check=catalogue.is_active,
    )

    return MatcherPipeline(catalogue, alias_store)
