import pytest

from app.utils.vin import (
    VERIFICATION_SOURCES, VinLookupError, build_vin_query,
    correct_vin_check_digit, expected_check_digit, fetch_vehicle_details,
    has_valid_model_year_code, is_valid_vin, normalize_vin,
)

# Classic ISO 3779 example: 9th character '3' is the correct check digit.
VALID_VIN = '1HGCM82633A004352'


def test_normalize_vin_removes_formatting():
    assert normalize_vin('1hgcm-82633 a004352') == VALID_VIN


def test_vin_validation_checks_shape_and_check_digit():
    assert is_valid_vin(VALID_VIN)
    assert not is_valid_vin('1HGCM82633A00435I')
    # Same VIN with a wrong check digit ('5' instead of '3').
    assert not is_valid_vin('1HGCM82653A004352')
    assert is_valid_vin('1HGCM82653A004352', check_digit=False)


def test_check_digit_follows_iso_3779_weights():
    # 'WF0PXXGCHPJR71967' is a real-world Ford Europe VIN with a wrong check
    # digit: the 9th character must be 0-9 or X and here is 'H'.
    vin = 'WF0PXXGCHPJR71967'
    assert not is_valid_vin(vin)
    assert is_valid_vin(vin, check_digit=False)
    assert expected_check_digit(vin) == '5'
    assert correct_vin_check_digit(vin) == 'WF0PXXGC5PJR71967'
    assert is_valid_vin('WF0PXXGC5PJR71967')


def test_correct_vin_check_digit_leaves_valid_vins_untouched():
    assert correct_vin_check_digit(VALID_VIN) == VALID_VIN
    assert correct_vin_check_digit('not a vin') == 'NOTAVIN'


# --- Model-year code validation -------------------------------------------

def test_model_year_code_validation():
    # Position 10 of this BMW-style VIN is '0', which ISO 3779 never uses.
    vin = 'WBAJC51050WB84663'
    assert not has_valid_model_year_code(vin)
    assert not is_valid_vin(vin)
    # The lenient route path (shape only) still accepts it for display.
    assert is_valid_vin(vin, check_digit=False)
    assert has_valid_model_year_code(VALID_VIN)
    assert has_valid_model_year_code('WF0PXXGC5PJR71967')  # 'P' = 2023


def test_fetch_warns_on_invalid_model_year_code():
    vin = 'WBAJC51050WB84663'
    decode = [
        {'Value': '1,11,14,400', 'Variable': 'Error Code'},
        {'Value': '1 - Check Digit ...; 11 - Incorrect Model Year - Position '
                  '10 does not match valid model year codes; '
                  '400 - Invalid Characters Present', 'Variable': 'Error Text'},
        {'Value': 'BMW', 'Variable': 'Make'},
        {'Value': 'BMW AG', 'Variable': 'Manufacturer Name'},
        {'Value': 'PASSENGER CAR', 'Variable': 'Vehicle Type'},
        {'Value': 'GRAZ', 'Variable': 'Plant City'},
        {'Value': 'AUSTRIA', 'Variable': 'Plant Country'},
    ]
    details = fetch_vehicle_details(
        vin, client=FakeClient(decode=decode, recalls=[]))
    assert "'0'" in details['warning']['en']
    assert 'poziția 10' in details['warning']['ro']
    assert details['vehicle']['make'] == 'BMW'


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


def test_fetch_suggests_corrected_check_digit():
    vin = 'WF0PXXGCHPJR71967'
    decode = [
        {'Value': '1,8,400', 'Variable': 'Error Code'},
        {'Value': '1 - Check Digit (9th position) does not calculate properly; '
                  '8 - No detailed data available currently; '
                  '400 - Invalid Characters Present', 'Variable': 'Error Text'},
        {'Value': 'WF0PXXGC!PJR71967', 'Variable': 'Suggested VIN'},
        {'Value': 'FORD', 'Variable': 'Make'},
        {'Value': '2023', 'Variable': 'Model Year'},
    ]
    details = fetch_vehicle_details(vin, client=FakeClient(decode=decode))
    assert details['corrected_vin'] == 'WF0PXXGC5PJR71967'
    assert 'corrected_vin' not in details['vehicle']
    assert 'Check digit' in details['warning']['en']
    assert 'WF0PXXGC5PJR71967' in details['warning']['en']
    assert 'Cifra de control' in details['warning']['ro']


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
