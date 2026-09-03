from app import app
from app.models.endpoint import Endpoint

import json

from test.conftest import demo_config


def test_main(client):
    rv = client.get('/')
    assert rv._status_code == 200


def test_search(client):
    rv = client.get(f'/{Endpoint.search}?q=test')
    assert rv._status_code == 200


def test_feeling_lucky(client):
    # Bang at beginning of query
    rv = client.get(f'/{Endpoint.search}?q=!%20wikipedia')
    assert rv._status_code == 303
    assert rv.headers.get('Location').startswith('https://www.wikipedia.org')

    # Move bang to end of query
    rv = client.get(f'/{Endpoint.search}?q=github%20!')
    assert rv._status_code == 303
    assert rv.headers.get('Location').startswith('https://github.com')


def test_ddg_bang(client):
    # Bang at beginning of query
    rv = client.get(f'/{Endpoint.search}?q=!gh%20whoogle')
    assert rv._status_code == 302
    assert rv.headers.get('Location').startswith('https://github.com')

    # Move bang to end of query
    rv = client.get(f'/{Endpoint.search}?q=github%20!w')
    assert rv._status_code == 302
    assert rv.headers.get('Location').startswith('https://en.wikipedia.org')

    # Move bang to middle of query
    rv = client.get(f'/{Endpoint.search}?q=big%20!r%20chungus')
    assert rv._status_code == 302
    assert rv.headers.get('Location').startswith('https://www.reddit.com')

    # Ensure bang is case insensitive
    rv = client.get(f'/{Endpoint.search}?q=!GH%20whoogle')
    assert rv._status_code == 302
    assert rv.headers.get('Location').startswith('https://github.com')

    # Ensure bang without a query still redirects to the result
    rv = client.get(f'/{Endpoint.search}?q=!gh')
    assert rv._status_code == 302
    assert rv.headers.get('Location').startswith('https://github.com')


def test_custom_bang(client):
    # Bang at beginning of query
    rv = client.get(f'/{Endpoint.search}?q=!i%20whoogle')
    assert rv._status_code == 302
    assert rv.headers.get('Location').startswith('search?q=')


def test_config(client):
    rv = client.post(f'/{Endpoint.config}', data=demo_config)
    assert rv._status_code == 302

    rv = client.get(f'/{Endpoint.config}')
    assert rv._status_code == 200

    config = json.loads(rv.data)
    for key in demo_config.keys():
        assert config[key] == demo_config[key]

    # Test disabling changing config from client
    app.config['CONFIG_DISABLE'] = 1
    nojs_mod = not bool(int(demo_config['nojs']))
    demo_config['nojs'] = str(int(nojs_mod))
    rv = client.post(f'/{Endpoint.config}', data=demo_config)
    assert rv._status_code == 403

    rv = client.get(f'/{Endpoint.config}')
    config = json.loads(rv.data)
    assert config['nojs'] != nojs_mod


def test_opensearch(client):
    rv = client.get(f'/{Endpoint.opensearch}')
    assert rv._status_code == 200
    assert '<ShortName>Whoogle</ShortName>' in str(rv.data)


VIN = '1HGCM82633A004352'


def test_vehicle_search_valid_vin(client):
    rv = client.post('/vehicle-search', data={'vin': VIN})
    assert rv.status_code == 302
    assert 'search?q=' in rv.headers['Location']


def test_vehicle_search_invalid_vin(client):
    rv = client.post('/vehicle-search', data={'vin': 'NOT-A-VIN'})
    assert rv.status_code == 400


def test_vehicle_search_pass_through_make_and_year(client):
    rv = client.post('/vehicle-search', data={
        'vin': VIN, 'vin_make': 'Honda', 'vin_year': '2003'})
    assert rv.status_code == 302


def test_vehicle_decode_renders_nhtsa_data(client, monkeypatch):
    details = {
        'vehicle': {
            'make': 'HONDA', 'model': 'Accord', 'model_year': '2003',
            'body_class': 'Coupe', 'fuel_type': 'Gasoline',
        },
        'recalls': [{
            'campaign_id': '19V182000', 'manufacturer': 'Honda',
            'component': 'AIR BAGS', 'summary': 'Inflator may explode.',
            'consequence': '', 'remedy': '', 'reported': '03/06/2019',
            'url': 'https://www.nhtsa.gov/recalls?nhtsaId=19V182000',
        }],
        'warning': {'en': '', 'ro': ''},
    }
    monkeypatch.setattr('app.routes.fetch_vehicle_details',
                        lambda vin, **kw: details)
    rv = client.post('/vehicle-decode', data={'vin': VIN})
    assert rv.status_code == 200
    body = str(rv.data)
    assert 'HONDA' in body and 'Accord' in body
    assert '19V182000' in body
    assert rv.headers.get('X-Robots-Tag') == 'noindex, nofollow'
    assert rv.headers.get('Cache-Control') == 'no-store'


def test_vehicle_decode_invalid_vin(client):
    rv = client.post('/vehicle-decode', data={'vin': 'BAD'})
    assert rv.status_code == 400


def test_vehicle_decode_disabled(client, monkeypatch):
    monkeypatch.setenv('WHOOGLE_VIN_DECODER', '0')
    rv = client.post('/vehicle-decode', data={'vin': VIN})
    assert rv.status_code == 200
    assert b'WHOOGLE_VIN_DECODER=0' in rv.data


def test_vehicle_sources_page(client):
    rv = client.get('/vehicle-sources')
    assert rv.status_code == 200
    body = str(rv.data)
    assert 'NHTSA' in body and 'CARFAX' in body


def test_vehicle_sources_keeps_vin(client):
    rv = client.get(f'/vehicle-sources?vin={VIN}')
    assert rv.status_code == 200
    assert VIN in rv.data.decode('utf-8')


def test_romanian_interface_renders_vin_sections(client, monkeypatch):
    with client.session_transaction() as session:
        session['config'] = {'lang_interface': 'lang_ro'}
    rv = client.get('/')
    assert rv.status_code == 200
    page = rv.data.decode('utf-8')
    assert 'Decodifică vehiculul' in page
    assert 'Vezi sursele de verificare' in page

    rv = client.get('/vehicle-sources')
    assert rv.status_code == 200
    assert 'Surse de verificare a vehiculelor' in rv.data.decode('utf-8')

    details = {
        'vehicle': {'make': 'HONDA', 'model': 'Accord', 'model_year': '2003'},
        'recalls': [], 'warning': {'en': '', 'ro': ''},
    }
    monkeypatch.setattr('app.routes.fetch_vehicle_details',
                        lambda vin, **kw: details)
    rv = client.post('/vehicle-decode', data={'vin': VIN})
    assert rv.status_code == 200
    page = rv.data.decode('utf-8')
    assert 'Decodificare vehicul' in page
    assert 'Producător' in page
