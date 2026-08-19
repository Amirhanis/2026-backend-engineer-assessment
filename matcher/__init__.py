"""Order line matcher — resolves free-text order lines to catalogue item codes.

Usage:
    from matcher import build_pipeline
    pipeline = build_pipeline()
    result = pipeline.match(
        line_id="ACM-001", tenant="acme",
        raw_text="Kanto Hex Bolt M12x60 Zinc Plated",
    )
"""
from matcher.pipeline import build_pipeline, MatcherPipeline
from matcher.models import MatchResult, Candidate

__all__ = ["build_pipeline", "MatcherPipeline", "MatchResult", "Candidate"]
