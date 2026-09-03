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


COUNTRY_NAMES = {
    'ro': 'Romania Moldova', 'md': 'Moldova Romania', 'gb': 'UK Britain',
    'us': 'USA United States', 'de': 'Germany Deutschland',
    'fr': 'France', 'it': 'Italy Italia', 'es': 'Spain España',
    'pl': 'Poland Polska', 'ru': 'Russia Россия', 'ua': 'Ukraine Україна',
}

# Common words used by auction, insurer and vehicle-history pages. Including
# several languages makes the global search useful even when a page is not in
# the user's interface language.
GLOBAL_TERMS = (
    'vehicle vehiculo fahrzeug vehículo voiture auto samochód '
    'historia fahrzeughistorie historie history raport registro'
)


def build_vin_query(vin: str, include_damage: bool = True,
                    focuses=None, country: str = '') -> str:
    """Build a broad public-web query for a VIN in the selected market.

    ``focuses`` can contain ``damage``, ``auction``, ``insurance``, ``theft``,
    ``mileage``, ``service`` or ``official``. With no focuses, all categories
    are searched for backwards compatibility.
    """
    value = normalize_vin(vin)
    selected = set(focuses or ('damage', 'auction', 'insurance', 'theft',
                               'mileage', 'service', 'official'))
    categories = {
        'damage': 'accident damage damaged salvage flood fire hail',
        'auction': 'auction copart iaai salvage sale',
        'insurance': 'insurance claim total loss insurer',
        'theft': 'theft stolen recovered police',
        'mileage': 'odometer mileage kilometer kilometraj rollback',
        'service': 'Ford authorized service dealer maintenance repair',
        'official': 'title registration recall vehicle history report paid official',
    }
    if not include_damage:
        selected.discard('damage')
    terms = ' '.join(categories[key] for key in categories if key in selected)
    if not terms:
        terms = 'vehicle history report'
    market = COUNTRY_NAMES.get((country or '').lower(), '')
    # Exact matching keeps a VIN from being split by Google's tokenizer.
    return f'"{value}" ({terms} {market} {GLOBAL_TERMS})'
