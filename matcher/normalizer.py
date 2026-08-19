"""Text normalisation for order line matching.

Handles the noise patterns observed in real order data:
- Prefixes: "item:", "need ", "pls send", "urgent "
- Separators: slashes, dashes used as token separators
- Double spaces (OCR/voice artefacts)
- Trade abbreviations (S/S, ZP, SDS, Malay terms)
- Inch/size marks (escaped quotes)
- Line-number prefixes ("1)", "2.")
"""
from __future__ import annotations

import re
from matcher.config import ABBREVIATIONS


def normalise_text(raw: str) -> str:
    """Normalise free-text order line to a canonical form for matching.

    Deterministic: same input always produces the same output.
    """
    text = raw.strip()

    # Strip common prefixes from various channels
    text = re.sub(r"^[\-\*•]\s*", "", text)                     # bullet points
    text = re.sub(r"^\d+[.)]\s*", "", text)                     # numbered lists "1) ", "2. "
    text = re.sub(r"^item:\s*", "", text, flags=re.IGNORECASE)  # "item: ..."
    text = re.sub(r"^need\s+", "", text, flags=re.IGNORECASE)   # "need ..."
    text = re.sub(r"^pls\s+send\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^please\s+send\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^urgent\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^order:\s*", "", text, flags=re.IGNORECASE)

    # Replace slash-separated tokens with spaces (portal_csv pattern)
    # But preserve fractions like 1/2", 3/4"
    text = re.sub(r'(\d)/(\d)', r'\1__FRAC__\2', text)  # protect fractions
    text = text.replace("/", " ")
    text = text.replace("__FRAC__", "/")

    # Normalise inch marks: ", '', ″ → "
    text = text.replace("''", '"')
    text = text.replace("\u2033", '"')

    # Collapse multiple spaces (OCR/voice artefact)
    text = re.sub(r"\s+", " ", text).strip()

    # Lowercase for matching
    text = text.lower()

    # Strip trailing pack multiplier patterns like "x24", "x6", "x48"
    # but keep them available for UOM resolution
    text = re.sub(r"\s+x\s*\d+\s*(ctn|carton|box|pkt|packet|case|nos|pcs|ea)?\s*$", "", text)

    return text


def expand_abbreviations(text: str) -> str:
    """Expand known trade abbreviations in normalised text."""
    words = text.split()
    expanded = []
    for word in words:
        clean = word.strip(".,;:()")
        if clean in ABBREVIATIONS:
            expanded.append(ABBREVIATIONS[clean])
        else:
            expanded.append(word)
    return " ".join(expanded)


def extract_brand(text: str, known_brands: set[str]) -> str | None:
    """Extract brand name from normalised text if it matches a known brand."""
    text_lower = text.lower()
    for brand in known_brands:
        brand_lower = brand.lower()
        if brand_lower in text_lower:
            return brand
    return None


def extract_size_spec(text: str) -> str:
    """Extract size/specification tokens (dimensions, gauges, etc.).

    Returns a normalised spec string for comparison.
    """
    specs = []

    # Bolt/screw sizes: M8x50, M10x75, #8 x 3/4"
    m = re.search(r'm(\d+)\s*x\s*(\d+)', text)
    if m:
        specs.append(f"m{m.group(1)}x{m.group(2)}")

    m = re.search(r'#(\d+)\s*x\s*([\d/\-]+)', text)
    if m:
        specs.append(f"#{m.group(1)}x{m.group(2)}")

    # Pipe/disc sizes: 20mm, 25mm, 4", 5", 7"
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*mm', text):
        specs.append(f"{m.group(1)}mm")

    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*"', text):
        specs.append(f'{m.group(1)}"')

    # Valve sizes: 1/2", 1", 1-1/4", 2"
    for m in re.finditer(r'(\d+(?:-\d+/\d+)?(?:/\d+)?)\s*"', text):
        specs.append(f'{m.group(1)}"')

    # Wire gauge: #16, #18, #20
    m = re.search(r'#(\d{1,2})(?:\s|$)', text)
    if m and not re.search(r'#\d+\s*x', text):  # not a screw size
        specs.append(f"#{m.group(1)}")

    # Weight: 1kg, 2.5kg, 250g
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*kg', text):
        specs.append(f"{m.group(1)}kg")
    for m in re.finditer(r'(\d+)\s*g(?:\s|$)', text):
        specs.append(f"{m.group(1)}g")

    # Pack count: 24s, 48s, 6s
    m = re.search(r'(\d+)s(?:\s|$)', text)
    if m:
        specs.append(f"{m.group(1)}s")

    # Volume: 1L, 2L
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*[lL](?:\s|$)', text):
        specs.append(f"{m.group(1)}l")

    return " ".join(specs)


def strip_brand(text: str, brand: str) -> str:
    """Remove brand name from text for brand-independent matching."""
    return re.sub(re.escape(brand.lower()), "", text.lower()).strip()
    return re.sub(r"\s+", " ", result).strip()
