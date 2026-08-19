"""Scoring and candidate generation using rapidfuzz.

Lexical matching strategy:
1. Token-sort ratio as primary score (handles word reordering)
2. Brand matching bonus/penalty
3. Spec matching bonus/penalty (sizes, dimensions must agree)
4. Twin detection (multiple candidates with near-identical scores)
"""
from __future__ import annotations

from rapidfuzz import fuzz, process
from matcher.models import CatalogueItem, Candidate
from matcher.normalizer import (
    normalise_text, expand_abbreviations, extract_brand,
    extract_size_spec, strip_brand,
)


def score_candidates(
    query: str,
    active_items: list[CatalogueItem],
    known_brands: set[str],
    top_n: int = 10,
) -> list[Candidate]:
    """Score all active catalogue items against a normalised query.

    Returns top_n candidates sorted by score descending.
    """
    norm_query = normalise_text(query)
    exp_query = expand_abbreviations(norm_query)

    # Extract structured info from query
    query_brand = extract_brand(exp_query, known_brands)
    query_spec = extract_size_spec(exp_query)

    # Build choice list: normalised item names
    choices = []
    items_map = []
    for item in active_items:
        norm_name = item.normalised_name
        exp_name = expand_abbreviations(norm_name)
        choices.append(exp_name)
        items_map.append(item)

    if not choices:
        return []

    candidates = []
    
    # Important modifiers that change the item's identity
    IMPORTANT_WORDS = {"zinc", "plated", "stainless", "304", "316", "410", "hdg", 
                       "brass", "pvc", "blue", "red", "black", "white", "natural", 
                       "brown", "yellow", "green", "bulk", "ratchet", "standard", 
                       "flap", "grinding", "cutting"}
    
    query_tokens = set(exp_query.split())

    for item in items_map:
        norm_name = item.normalised_name
        exp_name = expand_abbreviations(norm_name)
        
        raw_score = fuzz.token_sort_ratio(exp_query, exp_name)
        
        if raw_score < 40.0:
            continue

        adjusted = raw_score
        
        # Penalize if the item has an important word that the query is missing
        item_tokens = set(exp_name.split())
        unmatched_important = (item_tokens & IMPORTANT_WORDS) - query_tokens
        if unmatched_important:
            # Huge penalty for missing a key distinguisher (e.g., finish, colour, type)
            adjusted -= (30.0 * len(unmatched_important))
            
        # Bulk penalty if query didn't specify bulk
        if "bulk" in item_tokens and "bulk" not in query_tokens:
            adjusted -= 20.0

        # Brand matching: bonus if brand matches, penalty if brand present
        # in query but doesn't match
        item_brand_lower = item.brand.lower() if item.brand else ""
        if query_brand:
            query_brand_lower = query_brand.lower()
            if query_brand_lower == item_brand_lower:
                adjusted += 5.0   # brand match bonus
            else:
                adjusted -= 15.0  # brand mismatch penalty

        # Spec matching: sizes must match if both are specified
        item_spec = extract_size_spec(expand_abbreviations(item.normalised_name))
        if query_spec and item_spec:
            if query_spec == item_spec:
                adjusted += 3.0   # exact spec match
            else:
                # Check if specs partially overlap (some specs match, some don't)
                q_parts = set(query_spec.split())
                i_parts = set(item_spec.split())
                if q_parts and i_parts:
                    overlap = q_parts & i_parts
                    if not overlap:
                        adjusted -= 20.0  # complete spec mismatch
                    elif q_parts != i_parts:
                        adjusted -= 8.0   # partial spec mismatch

        # Item group matching bonus (e.g., if query says "disc" and item
        # is in Angle Grinder Disc group)
        # This is implicit through token matching

        # Clamp to [0, 100]
        adjusted = max(0.0, min(100.0, adjusted))

        candidates.append(Candidate(
            item_code=item.item_code,
            score=adjusted,
            stage="lexical",
            item_name=item.item_name,
        ))

    # Sort by adjusted score descending
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_n]


def detect_twins(candidates: list[Candidate], gap_threshold: float) -> bool:
    """Check if top candidates are 'twins' — near-identical scores.

    Returns True if the gap between #1 and #2 is less than gap_threshold,
    indicating the matcher cannot confidently distinguish between them.
    """
    if len(candidates) < 2:
        return False
    return (candidates[0].score - candidates[1].score) < gap_threshold


def get_item_group_from_name(item_name: str) -> str:
    """Extract the item group keyword from an item name."""
    # Common patterns: "Brand ItemGroup Spec"
    groups = [
        "hex bolt", "ball valve", "cable tie", "angle grinder disc",
        "self drilling screw", "pvc pipe", "safety helmet", "nitrile glove",
        "hose clip", "wall plug", "masking tape", "gi wire",
        "prawn", "salmon fillet", "chicken breast", "beef patty",
        "potato fries", "butter", "mozzarella", "vanilla ice cream",
        "full cream milk", "puff pastry", "squid ring", "whipping cream",
    ]
    name_lower = item_name.lower()
    for g in groups:
        if g in name_lower:
            return g
    return ""
