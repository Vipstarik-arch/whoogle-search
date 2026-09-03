"""Helpers for searching publicly indexed vehicle history and decoding VINs.

The module covers three areas:

* VIN normalization/validation (ISO 3779 check digit).
* Broad public-web queries for vehicle history (auctions, insurers, theft
  registries, odometer records, official reports).
* Lookups against free public NHTSA APIs (VPIC decode + recalls) and a
  curated list of external verification sources.

Only public, free-of-charge endpoints are used. No paid service, private
insurer, police or DMV database is queried directly.
"""

from datetime import datetime

import re

import httpx

# ---------------------------------------------------------------------------
# VIN validation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Public-web history search
# ---------------------------------------------------------------------------

COUNTRY_NAMES = {
    # Regional markets used most often by imported/exported vehicles.
    'ro': 'România Moldova', 'md': 'Moldova Romania', 'gb': 'UK Britain',
    'us': 'USA United States', 'ca': 'Canada', 'au': 'Australia',
    'de': 'Germany Deutschland', 'fr': 'France', 'it': 'Italy Italia',
    'es': 'Spain España', 'pt': 'Portugal', 'pl': 'Poland Polska',
    'nl': 'Netherlands Nederland', 'be': 'Belgium België Belgique',
    'at': 'Austria Österreich', 'ch': 'Switzerland Schweiz Suisse',
    'cz': 'Czechia Česko', 'sk': 'Slovakia Slovensko',
    'hu': 'Hungary Magyarország', 'si': 'Slovenia Slovenija',
    'hr': 'Croatia Hrvatska', 'bg': 'Bulgaria България',
    'gr': 'Greece Ελλάδα', 'dk': 'Denmark Danmark',
    'se': 'Sweden Sverige', 'no': 'Norway Norge',
    'fi': 'Finland Suomi', 'ie': 'Ireland Éire',
    'ee': 'Estonia Eesti', 'lv': 'Latvia Latvija', 'lt': 'Lithuania Lietuva',
    'ru': 'Russia Россия', 'ua': 'Ukraine Україна',
    'tr': 'Turkey Türkiye', 'jp': 'Japan 日本', 'kr': 'South Korea 한국',
}

# Common words used by auction, insurer and vehicle-history pages. Including
# several languages makes the global search useful even when a page is not in
# the user's interface language.
GLOBAL_TERMS = (
    'vehicle vehiculo fahrzeug vehículo voiture auto samochód '
    'historia fahrzeughistorie historie history raport registro'
)

# Category -> query terms. Kept provider-neutral: no single brand is assumed,
# so the same query works for vehicles from every manufacturer/market.
CATEGORY_TERMS = {
    'damage': 'accident damage damaged salvage flood fire hail',
    'auction': 'auction copart iaai salvage sale',
    'insurance': 'insurance claim total loss insurer',
    'theft': 'theft stolen recovered police',
    'mileage': 'odometer mileage kilometer kilometraj rollback',
    'service': 'service history authorized dealer maintenance repair recalls',
    'official': 'title registration recall vehicle history report paid official',
}

DEFAULT_FOCUSES = tuple(CATEGORY_TERMS.keys())


def build_vin_query(vin: str, include_damage: bool = True,
                    focuses=None, country: str = '', make: str = '',
                    year: str = '') -> str:
    """Build a broad public-web query for a VIN in the selected market.

    ``focuses`` can contain ``damage``, ``auction``, ``insurance``, ``theft``,
    ``mileage``, ``service`` or ``official``. With no focuses, all categories
    are searched for backwards compatibility.

    ``make`` and ``year`` are optional constraints (e.g. taken from a decoded
    record) that narrow the results without splitting the VIN token.
    """
    value = normalize_vin(vin)
    selected = set(focuses or DEFAULT_FOCUSES)
    categories = CATEGORY_TERMS
    if not include_damage:
        selected.discard('damage')
    terms = ' '.join(categories[key] for key in categories if key in selected)
    if not terms:
        terms = 'vehicle history report'
    market = COUNTRY_NAMES.get((country or '').lower(), '')

    # Free-text constraints are sanitized so they can never alter the query
    # structure; only word characters (incl. international letters) and a few
    # separator characters survive.
    make = re.sub(r"[^\w -]", '', make or '').strip()
    year = re.sub(r"[^0-9]", '', year or '')[:4]
    details = ' '.join(part for part in (year, make) if part)

    # Exact matching keeps a VIN from being split by Google's tokenizer.
    query = f'"{value}" ({terms} {market} {GLOBAL_TERMS})'
    if details:
        query += f' {details}'
    return query


# ---------------------------------------------------------------------------
# NHTSA public lookups
# ---------------------------------------------------------------------------

NHTSA_DECODE_URL = (
    'https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json'
)
NHTSA_RECALLS_URL = (
    'https://api.nhtsa.gov/recalls/recallsByVehicle'
    '?make={make}&model={model}&modelYear={year}'
)
NHTSA_RECALL_PAGE_URL = 'https://www.nhtsa.gov/recalls?nhtsaId={campaign_id}'


class VinLookupError(Exception):
    """Raised when a public VIN data provider cannot be reached or errors."""


# Variable name (as returned by VPIC) -> canonical key used by the templates.
DECODE_FIELDS = (
    ('Make', 'make'),
    ('Model', 'model'),
    ('Model Year', 'model_year'),
    ('Vehicle Type', 'vehicle_type'),
    ('Body Class', 'body_class'),
    ('Series', 'series'),
    ('Trim', 'trim'),
    ('Doors', 'doors'),
    ('Drive Type', 'drive_type'),
    ('Engine Model', 'engine_model'),
    ('Engine Number of Cylinders', 'engine_cylinders'),
    ('Displacement (L)', 'displacement_l'),
    ('Fuel Type - Primary', 'fuel_type'),
    ('Transmission Style', 'transmission'),
    ('Plant City', 'plant_city'),
    ('Plant State', 'plant_state'),
    ('Plant Country', 'plant_country'),
    ('Suggested VIN', 'suggested_vin'),
)

# Display order + localized labels for the decoded record.
SPEC_FIELDS = (
    ('make', 'make'), ('model', 'model'), ('model_year', 'model_year'),
    ('vehicle_type', 'vehicle_type'), ('body_class', 'body_class'),
    ('trim', 'trim'), ('series', 'series'), ('doors', 'doors'),
    ('drive_type', 'drive_type'), ('engine_model', 'engine_model'),
    ('engine_cylinders', 'engine_cylinders'), ('displacement_l', 'displacement_l'),
    ('fuel_type', 'fuel_type'), ('transmission', 'transmission'),
    ('plant_city', 'plant_city'), ('plant_state', 'plant_state'),
    ('plant_country', 'plant_country'),
)

SPEC_LABELS = {
    'en': {
        'make': 'Make', 'model': 'Model', 'model_year': 'Model year',
        'vehicle_type': 'Vehicle type', 'body_class': 'Body class',
        'trim': 'Trim', 'series': 'Series', 'doors': 'Doors',
        'drive_type': 'Drive type', 'engine_model': 'Engine model',
        'engine_cylinders': 'Cylinders', 'displacement_l': 'Displacement (L)',
        'fuel_type': 'Fuel type', 'transmission': 'Transmission',
        'plant_city': 'Plant city', 'plant_state': 'Plant state',
        'plant_country': 'Plant country',
    },
    'ro': {
        'make': 'Producător', 'model': 'Model',
        'model_year': 'An de fabricație', 'vehicle_type': 'Tip vehicul',
        'body_class': 'Clasă caroserie', 'trim': 'Echipare',
        'series': 'Serie', 'doors': 'Uși', 'drive_type': 'Tracțiune',
        'engine_model': 'Motor', 'engine_cylinders': 'Cilindri',
        'displacement_l': 'Cilindree (L)', 'fuel_type': 'Combustibil',
        'transmission': 'Transmisie', 'plant_city': 'Uzina – oraș',
        'plant_state': 'Uzina – stat', 'plant_country': 'Uzina – țară',
    },
}


def _decode_value(results, variable: str):
    """Return the non-empty value for a VPIC ``Variable``, if present."""
    for item in results or []:
        if item.get('Variable') == variable:
            value = item.get('Value')
            return value if value not in (None, '') else None
    return None


def _decode_vehicle(results) -> dict:
    """Turn raw VPIC results into a compact dictionary of known fields."""
    vehicle = {}
    for variable, key in DECODE_FIELDS:
        value = _decode_value(results, variable)
        if value is not None:
            vehicle[key] = value
    return vehicle


def fetch_vehicle_details(vin: str, *, include_recalls: bool = True,
                          timeout: float = 10.0, client=None) -> dict:
    """Query the free public NHTSA APIs for a VIN.

    Returns a dict with:

    * ``vehicle``: decoded attributes (may be empty for unknown records),
    * ``recalls``: list of recall campaigns matching the decoded make/model/
      year,
    * ``warning``: optional human readable message when the decode was not
      clean or no data was found.

    Raises:
        VinLookupError: if the NHTSA endpoints cannot be reached.
    """
    vin = normalize_vin(vin)
    request = client or httpx.get

    try:
        decode_response = request(
            NHTSA_DECODE_URL.format(vin=vin), timeout=timeout)
        decode_response.raise_for_status()
        payload = decode_response.json()
    except Exception as exc:
        raise VinLookupError(
            'NHTSA lookup failed. The public API might be temporarily '
            'unavailable or the instance may have no outbound network access.'
        ) from exc

    results = payload.get('Results') or []
    vehicle = _decode_vehicle(results)
    error_code = _decode_value(results, 'Error Code')
    error_text = _decode_value(results, 'Error Text')

    if not vehicle:
        warning = {
            'en': 'No public manufacturer record was found for this VIN. '
                  'The record may not be in the NHTSA dataset (e.g. an '
                  'older European vehicle).',
            'ro': 'Nu a fost găsită nicio înregistrare publică de producător '
                  'pentru acest VIN. Este posibil ca vehiculul să nu existe '
                  'în baza NHTSA (de exemplu, un vehicul european mai vechi).',
        }
    elif error_code not in (None, '', '0') or error_text:
        detail = (
            error_text or
            'VPIC reported an issue decoding this VIN (error code {}).'.format(
                error_code or 'unknown')
        )
        warning = {
            'en': detail + ' The record may be incomplete or unofficial.',
            'ro': detail + ' Înregistrarea poate fi incompletă sau neoficială.',
        }
    else:
        warning = {'en': '', 'ro': ''}

    if 'suggested_vin' in vehicle:
        suggested = vehicle['suggested_vin']
        warning = {
            'en': ((warning['en'] + ' ') if warning['en'] else '') +
                  f'Suggested VIN: {suggested}.',
            'ro': ((warning['ro'] + ' ') if warning['ro'] else '') +
                  f'VIN sugerat: {suggested}.',
        }

    details = {'vehicle': vehicle, 'recalls': [], 'warning': warning}

    make = vehicle.get('make')
    model = vehicle.get('model')
    model_year = str(vehicle.get('model_year', ''))
    if include_recalls and make and model and model_year:
        try:
            recalls_response = request(
                NHTSA_RECALLS_URL.format(
                    make=make.lower().replace(' ', '+'),
                    model=model.lower().replace(' ', '+'),
                    year=model_year),
                timeout=timeout)
            recalls_response.raise_for_status()
            recalls = recalls_response.json().get('results') or []
            details['recalls'] = _normalize_recalls(recalls)
        except Exception:
            # Recalls are a bonus; a failed recalls request must not turn the
            # whole page into an error when the decode itself succeeded.
            details['recalls'] = []

    return details


def _normalize_recalls(records) -> list:
    """Keep only the fields needed by the template and sort newest first."""
    recalls = []
    for record in records or []:
        campaign_id = record.get('NHTSACampaignNumber', '')
        if not campaign_id:
            continue
        recalls.append({
            'campaign_id': campaign_id,
            'manufacturer': record.get('Manufacturer', ''),
            'component': record.get('Component', ''),
            'summary': re.sub(r'\s+', ' ', record.get('Summary', '') or '').strip(),
            'consequence': re.sub(
                r'\s+', ' ', record.get('Consequence', '') or '').strip(),
            'remedy': re.sub(
                r'\s+', ' ', record.get('Remedy', '') or '').strip(),
            'reported': record.get('ReportReceivedDate', ''),
            'url': NHTSA_RECALL_PAGE_URL.format(campaign_id=campaign_id),
        })
    # NHTSA dates use DD/MM/YYYY; sort chronologically, newest first.
    recalls.sort(key=_recall_date, reverse=True)
    return recalls


def _recall_date(recall) -> tuple:
    """Return a sortable tuple for a recall date, oldest-first."""
    for fmt in ('%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d'):
        try:
            value = datetime.strptime(recall['reported'], fmt)
            return (value.year, value.month, value.day)
        except (ValueError, TypeError):
            continue
    return (0, 0, 0)


# ---------------------------------------------------------------------------
# Verification sources
# ---------------------------------------------------------------------------

VERIFICATION_SOURCES = (
    {
        'id': 'nhtsa-decoder',
        'name': 'NHTSA VIN Decoder (VPIC)',
        'url': 'https://vpic.nhtsa.dot.gov/decoder/',
        'scope': 'US / global',
        'kind': 'official',
        'description': 'Free U.S. government decoder used by this app '
                       'for make/model/year and technical attributes.',
        'description_ro': 'Decoder gratuit al guvernului american, folosit '
                          'și de această aplicație pentru marcă, model, an și '
                          'caracteristici tehnice.',
    },
    {
        'id': 'nhtsa-recalls',
        'name': 'NHTSA Recalls',
        'url': 'https://www.nhtsa.gov/recalls',
        'scope': 'US',
        'kind': 'official',
        'description': 'Official U.S. recall campaigns searchable by VIN, '
                       'make/model or campaign number.',
        'description_ro': 'Campanii oficiale americane de rechemare '
                          '(recall), căutabile după VIN, marcă/model sau '
                          'număr de campanie.',
    },
    {
        'id': 'nmvtis',
        'name': 'NMVTIS / VehicleHistory.gov',
        'url': 'https://www.vehiclehistory.gov/',
        'scope': 'US',
        'kind': 'official',
        'description': 'U.S. National Motor Vehicle Title Information '
                       'System: title, salvage and brand history.',
        'description_ro': 'Sistemul american național de informații despre '
                          'titluri de proprietate: istoric de titlu, daune '
                          'totale (salvage) și branduri.',
    },
    {
        'id': 'rar',
        'name': 'Registrul Auto Român (RAR)',
        'url': 'https://www.rarom.ro/',
        'scope': 'RO',
        'kind': 'official',
        'description': 'Official Romanian vehicle registration and technical '
                       'inspection authority.',
        'description_ro': 'Autoritatea română oficială pentru înmatriculări '
                          'și inspecții tehnice.',
    },
    {
        'id': 'asp-md',
        'name': 'Agenția Servicii Publice (Republica Moldova)',
        'url': 'https://asp.gov.md/',
        'scope': 'MD',
        'kind': 'official',
        'description': 'Public services agency that maintains the Moldovan '
                       'vehicle registration records.',
        'description_ro': 'Agenția Servicii Publice, care ține evidența '
                          'înmatriculărilor auto din Republica Moldova.',
    },
    {
        'id': 'dvla',
        'name': 'DVLA (UK)',
        'url': 'https://www.gov.uk/check-vehicle-tax',
        'scope': 'UK',
        'kind': 'official',
        'description': 'Official UK vehicle tax, MOT and registration checks.',
        'description_ro': 'Verificări oficiale britanice pentru taxă, ITP '
                          '(MOT) și înmatriculare.',
    },
    {
        'id': 'carfax',
        'name': 'CARFAX',
        'url': 'https://www.carfax.com/',
        'scope': 'US / CA',
        'kind': 'commercial',
        'description': 'Commercial vehicle history reports (paid) built from '
                       'auctions, insurers and DMV data.',
        'description_ro': 'Rapoarte comerciale de istoric (plată), construite '
                          'din licitații, asigurări și date DMV.',
    },
    {
        'id': 'autocheck',
        'name': 'AutoCheck (Experian)',
        'url': 'https://www.autocheck.com/',
        'scope': 'US',
        'kind': 'commercial',
        'description': 'Experian vehicle history reports used widely by '
                       'dealers (paid).',
        'description_ro': 'Rapoarte de istoric Experian, folosite mult de '
                          'dealeri (plată).',
    },
    {
        'id': 'hpi',
        'name': 'HPI Check',
        'url': 'https://www.hpi.co.uk/',
        'scope': 'UK',
        'kind': 'commercial',
        'description': 'UK history, finance/outstanding loan and theft checks '
                       '(paid).',
        'description_ro': 'Verificări britanice de istoric, credite rămase și '
                          'furt (plată).',
    },
    {
        'id': 'autodna',
        'name': 'autoDNA',
        'url': 'https://www.autodna.com/',
        'scope': 'EU',
        'kind': 'commercial',
        'description': 'European history reports focused on mileage and '
                       'collision records (paid).',
        'description_ro': 'Rapoarte europene axate pe kilometraj și '
                          'coliziuni (plată).',
    },
    {
        'id': 'carvertical',
        'name': 'carVertical',
        'url': 'https://carvertical.com/',
        'scope': 'EU',
        'kind': 'commercial',
        'description': 'Worldwide history reports with odometer and auction '
                       'records (paid).',
        'description_ro': 'Rapoarte mondiale cu kilometraj și date de '
                          'licitație (plată).',
    },
)

SOURCE_GROUPS = ('official', 'commercial')
SOURCE_GROUP_LABELS = {
    'en': {'official': 'Official / public sources',
           'commercial': 'Commercial report providers'},
    'ro': {'official': 'Surse oficiale / publice',
           'commercial': 'Furnizori comerciali de rapoarte'},
}
