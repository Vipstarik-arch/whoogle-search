import pytest

from app.utils.vin import (
    VERIFICATION_SOURCES, VinLookupError, build_vin_query, fetch_vehicle_details,
    is_valid_vin, normalize_vin,
)

VALID_VIN = '1HGCM82653A004352'


def test_normalize_vin_removes_formatting():
    assert normalize_vin('1hgcm-82653 a004352') == VALID_VIN


def test_vin_validation_checks_shape_and_check_digit():
    assert is_valid_vin(VALID_VIN)
    assert not is_valid_vin('1HGCM82633A00435I')
    assert not is_valid_vin('1HGCM82633A004351')
    assert is_valid_vin('1HGCM82633A004351', check_digit=False)


def test_vin_query_keeps_exact_code_and_damage_terms():
    query = build_vin_query(VALID_VIN)
    assert f'"{VALID_VIN}"' in query
    assert 'accident' in query
    assert 'salvage' in query
    assert 'service' in query
    assert 'recalls' in query
    assert 'paid' in query


def test_vin_query_can_focus_on_history():
    query = build_vin_query(VALID_VIN, include_damage=False)
    assert 'odometer' in query
    assert 'accident' not in query


def test_vin_query_supports_market_and_focuses():
    query = build_vin_query(VALID_VIN, focuses=['theft'], country='de')
    assert 'stolen' in query
    assert 'Deutschland' in query
    assert 'odometer' not in query


def test_vin_query_supports_make_and_year_filter():
    query = build_vin_query(
        VALID_VIN, focuses=['official'], make='Honda', year='2003')
    assert f'"{VALID_VIN}"' in query
    assert '2003 Honda' in query
    # Make/year must never break out of the quoted VIN token.
    assert '"2003' not in query
    assert '"Honda' not in query


def test_vin_query_sanitizes_free_text_parameters():
    query = build_vin_query(VALID_VIN, make='Honda"; drop table',
                            year='999999')
    # Quotes and punctuation never survive into the query structure: the only
    # quotes left are the ones wrapping the exact VIN token.
    assert query.count('"') == 2
    assert '";' not in query
    assert '9999' in query
    assert f'"{VALID_VIN}"' in query


# --- NHTSA integration ----------------------------------------------------

DECODE_RESULTS = [
    {'Value': '0', 'Variable': 'Error Code'},
    {'Value': 'HONDA', 'Variable': 'Make'},
    {'Value': 'Accord', 'Variable': 'Model'},
    {'Value': '2003', 'Variable': 'Model Year'},
    {'Value': 'PASSENGER CAR', 'Variable': 'Vehicle Type'},
    {'Value': 'Coupe', 'Variable': 'Body Class'},
    {'Value': 'Three', 'Variable': 'Engine Number of Cylinders'},
    {'Value': '2.998832712', 'Variable': 'Displacement (L)'},
    {'Value': 'Gasoline', 'Variable': 'Fuel Type - Primary'},
    {'Value': None, 'Variable': 'Series'},
]

RECALL_RESULTS = [
    {
        'NHTSACampaignNumber': '19V182000',
        'Manufacturer': 'Honda',
        'Component': 'AIR BAGS',
        'Summary': 'Inflator may explode.',
        'Consequence': 'Injury risk.',
        'Remedy': 'Replace free of charge.',
        'ReportReceivedDate': '06/03/2019',
    },
    {
        'NHTSACampaignNumber': '18V268000',
        'Component': 'AIR BAGS',
        'ReportReceivedDate': '26/04/2018',
    },
]


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, decode=DECODE_RESULTS, recalls=RECALL_RESULTS):
        self.decode = decode
        self.recalls = recalls
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if 'DecodeVin' in url:
            return FakeResponse({'Results': self.decode})
        return FakeResponse({'results': self.recalls})


def test_fetch_vehicle_details_parses_nhtsa_response():
    details = fetch_vehicle_details(VALID_VIN, client=FakeClient())
    assert details['vehicle']['make'] == 'HONDA'
    assert details['vehicle']['model'] == 'Accord'
    assert details['vehicle']['model_year'] == '2003'
    assert details['vehicle']['fuel_type'] == 'Gasoline'
    # Empty/None values must not pollute the record.
    assert 'series' not in details['vehicle']
    assert details['warning'] == {'en': '', 'ro': ''}
    assert len(details['recalls']) == 2
    assert details['recalls'][0]['campaign_id'] == '19V182000'
    assert details['recalls'][0]['url'].endswith('19V182000')
    assert len(details['recalls'][0]['summary']) > 0


def test_fetch_vehicle_details_reports_unknown_vin():
    client = FakeClient(decode=[{'Value': '1', 'Variable': 'Error Code'}])
    details = fetch_vehicle_details(VALID_VIN, client=client)
    assert details['vehicle'] == {}
    assert 'en' in details['warning'] and details['warning']['en']
    assert details['recalls'] == []


def test_fetch_vehicle_details_survives_recalls_failure():
    class FlakyClient(FakeClient):
        def __call__(self, url, timeout=None):
            if 'recallsByVehicle' in url:
                raise RuntimeError('network down')
            return super().__call__(url, timeout=timeout)

    details = fetch_vehicle_details(VALID_VIN, client=FlakyClient())
    assert details['vehicle']['make'] == 'HONDA'
    assert details['recalls'] == []


def test_fetch_vehicle_details_raises_on_network_error():
    def broken(url, timeout=None):
        raise ConnectionError('no network')

    with pytest.raises(VinLookupError):
        fetch_vehicle_details(VALID_VIN, client=broken)


def test_verification_sources_are_structured():
    ids = [source['id'] for source in VERIFICATION_SOURCES]
    assert len(ids) == len(set(ids))
    assert 'nhtsa-decoder' in ids and 'rar' in ids
    for source in VERIFICATION_SOURCES:
        assert source['url'].startswith('https://')
        assert source['kind'] in ('official', 'commercial')
        assert source['description'] and source['description_ro']


def test_spec_labels_cover_spec_fields():
    from app.utils.vin import SPEC_FIELDS, SPEC_LABELS
    keys = {key for key, _ in SPEC_FIELDS}
    for lang in ('en', 'ro'):
        assert keys == set(SPEC_LABELS[lang])
