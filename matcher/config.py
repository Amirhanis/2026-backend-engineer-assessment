"""Tunable configuration for the matcher pipeline.

All thresholds calibrated empirically on order_lines_train.csv.
See DECISIONS.md D-03 for the operating-point rationale.
"""

# ── Cost model (from §1 of the brief) ──────────────────────────────
COST_CORRECT_AUTO = 20       # seconds saved
COST_ABSTENTION = -40        # seconds cost (human review)
COST_WRONG_AUTO = -800       # 20× abstention: wrong shipment

# ── Pipeline thresholds ─────────────────────────────────────────────
# Lexical scoring
AUTO_THRESHOLD = 82.0        # minimum score to auto-accept
REVIEW_THRESHOLD = 55.0      # below this → reject
TWIN_GAP_MIN = 8.0           # minimum gap between #1 and #2 to avoid ambiguity

# Alias lookup
ALIAS_MIN_CONFIDENCE = 0.70  # ignore alias entries below this

# Barcode / exact match
BARCODE_CONFIDENCE = 0.99    # confidence assigned to barcode hits
ALIAS_CONFIDENCE = 0.97      # confidence assigned to alias exact hits
ITEM_CODE_DIRECT_CONFIDENCE = 0.99  # confidence for direct item_code match

# Lexical confidence calibration
# Maps raw fuzzy score [0,100] to calibrated confidence [0,1]
# Piecewise linear: score>=95 → 0.98, score>=85 → 0.90, score>=75 → 0.75, etc.
def calibrate_confidence(raw_score: float) -> float:
    """Convert raw fuzzy match score [0,100] to calibrated confidence [0,1].

    Calibration derived from train-set precision at each score bucket.
    """
    if raw_score >= 95:
        return 0.98
    elif raw_score >= 90:
        return 0.94
    elif raw_score >= 85:
        return 0.88
    elif raw_score >= 80:
        return 0.78
    elif raw_score >= 70:
        return 0.55
    elif raw_score >= 60:
        return 0.35
    else:
        return 0.15

# ── Not-an-item detection ───────────────────────────────────────────
NOT_ITEM_PATTERNS = [
    r"^\s*-{2,}\s*$",                    # just dashes
    r"^\s*(sub\s*)?total\s*$",
    r"^\s*delivery\b",
    r"^\s*discount\b",
    r"\bsame as last\b",
    r"\bdeliver by\b",
    r"\bthanks?\b",
    r"^\s*foc\s*$",
    r"\bcredit note\b",
    r"\bopening balance\b",
    r"\bpls confirm\b",
    r"\bconfirm stock\b",
    r"\bplease call\b",
    r"\bkindly quote\b",
    r"^\s*po attached\s*$",
    r"^\s*attn:",
    r"^\s*note:",
]

# Known out-of-catalogue brands/products that should trigger abstain
OUT_OF_CATALOGUE_KEYWORDS = [
    "cadbury", "pepsi", "coca cola", "coke", "epson", "samsung", "colgate",
    "nescafe", "milo", "maggi", "wagyu", "duracell", "makita", "3m 8210",
    "n95 respirator", "copper pipe",
]

# ── Trade abbreviations ─────────────────────────────────────────────
ABBREVIATIONS = {
    "s/s": "stainless",
    "ss": "stainless",
    "ss304": "stainless 304",
    "ss316": "stainless 316",
    "zp": "zinc plated",
    "hdg": "hdg",
    "sds": "self drilling screw",
    "gi": "gi",
    "pvc": "pvc",
    "m/s": "mild steel",
    "blk": "black",
    "wht": "white",
    "nat": "natural",
    "ctn": "carton",
    "pkt": "packet",
    "doz": "dozen",
    "skru": "screw",
    "paip": "pipe",
    "pita": "tape",
    "susu": "milk",
    "ayam": "chicken",
    "udang": "prawn",
    "daging": "beef",
    "mentega": "butter",
    "keju": "cheese",
    "ikan": "fish",
}
