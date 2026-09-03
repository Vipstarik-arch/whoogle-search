"""Helpers for searching publicly indexed vehicle history by VIN."""

import re

# VINs use 17 characters and deliberately omit I, O and Q.
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
_TRANSLITERATION = {
    **dict(zip("ABCDEFGH", range(1, 9))),
    **dict(zip("JKLMN", range(1, 6))),
    # P is 7 and R is 9 (the value 8 is intentionally skipped).
    **dict(zip("PR", (7, 9))),
    **dict(zip("STUVWXYZ", range(2, 10))),
}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 8, 7, 6, 5, 4, 3, 2)


def normalize_vin(vin: str) -> str:
    """Return a VIN in the canonical form used by search engines."""
    return re.sub(r"[\s-]", "", vin or "").upper()


def is_valid_vin(vin: str, *, check_digit: bool = True) -> bool:
    """Validate VIN shape and, where applicable, its ISO 3779 check digit.

    Some older/non-North-American records do not use a check digit, therefore
    callers can disable that final check while retaining the safe VIN shape
    validation.
    """
    value = normalize_vin(vin)
    if not VIN_RE.fullmatch(value):
        return False
    if not check_digit:
        return True

    total = sum(
        (int(char) if char.isdigit() else _TRANSLITERATION[char]) * weight
        for char, weight in zip(value, _WEIGHTS)
    )
    expected = "X" if total % 11 == 10 else str(total % 11)
    return value[8] == expected


def build_vin_query(vin: str, include_damage: bool = True) -> str:
    """Build a broad, useful query without contacting third-party databases."""
    value = normalize_vin(vin)
    terms = (
        'accident damage salvage auction insurance theft stolen recall '
        'odometer mileage title history report Ford authorized service '
        'paid official report'
        if include_damage else
        'vehicle history report recall theft title odometer auction'
    )
    # Exact matching keeps a VIN from being split by Google's tokenizer.
    return f'"{value}" ({terms})'
