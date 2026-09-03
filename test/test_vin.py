from app.utils.vin import build_vin_query, is_valid_vin, normalize_vin


VALID_VIN = '1HGCM82653A004352'


def test_normalize_vin_removes_formatting():
    assert normalize_vin('1hgcm-82633 a004352') == VALID_VIN


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
    assert 'Ford' in query
    assert 'paid' in query


def test_vin_query_can_focus_on_history():
    query = build_vin_query(VALID_VIN, include_damage=False)
    assert 'odometer' in query
    assert 'accident' not in query
