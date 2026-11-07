from datetime import datetime, timezone, date
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError

from v2.app import create_app
from v2.auth.service import AuthService
from v2.catalog.tables import revisions
from v2.catalog.service import resolve
from v2.db import schema as s
from v2.db.engine import open_database, unit_of_work
from v2.db.migrate import migrate
from v2.domain.settings import Settings

NOW = datetime(2026, 10, 11, 6, tzinfo=timezone.utc)
PRODUCT = dict(sku_id='milk', name='Milk', dimension='ml', pack_size=500, units_per_container=24,
    full_container_g=13000, container_type='crate', returnable=True, lane='chilled',
    shelf_life_minutes=1440, active=True)


@pytest.fixture
def env(tmp_path):
    engine = open_database(tmp_path / 'catalog.sqlite3')
    migrate(engine)
    with unit_of_work(engine) as tx:
        for run in ('a', 'b'):
            tx.execute(s.runs.insert().values(id=run, name=run, seed=1, settings={}))
        for role in ('admin', 'plant_manager'):
            tx.execute(s.users.insert().values(id=role, phone=role, pin_hash='!disabled', active=True))
            tx.execute(s.grants.insert().values(run_id='a', id=role, user_id=role, role=role))
        tx.execute(s.users.insert().values(id='other-admin', phone='other', pin_hash='!disabled', active=True))
        tx.execute(s.grants.insert().values(run_id='b', id='other-admin', user_id='other-admin', role='admin'))
    auth = AuthService(engine, 'x' * 48, clock=lambda: NOW)
    client = TestClient(create_app(auth))
    def headers(user='admin', run='a'):
        return {'Authorization': 'Bearer ' + auth.issue(user)['token'], 'X-Run-Id': run, 'X-Grant-Id': user}
    yield engine, auth, client, headers
    engine.dispose()


def body(revision=0, effective='2026-10-11', data=None):
    return dict(command_id=str(uuid4()), expected_revision=revision, effective_date=effective,
        occurred_at=NOW.isoformat(), reason='Test setup change', data=data or PRODUCT)


def test_create_retry_conflict_atomic_audit(env):
    engine, auth, client, headers = env
    h = headers(); payload = body()
    response = client.post('/setup/products/milk', headers=h, json=payload)
    assert response.status_code == 201, response.text
    assert client.post('/setup/products/milk', headers=h, json=payload).json() == response.json()
    payload['data'] = {**PRODUCT, 'pack_size': 1000}
    assert client.post('/setup/products/milk', headers=h, json=payload).status_code == 409
    assert client.put('/setup/products/milk', headers=h, json=body()).status_code == 409
    with engine.connect() as tx:
        for table in (s.commands, s.audit, s.outbox, revisions):
            assert tx.scalar(select(func.count()).select_from(table)) == 1
        assert tx.execute(select(s.audit.c.actor_id, s.audit.c.reason)).one() == ('admin', 'Test setup change')
    assert client.get('/products', headers=h).json()[0]['pack_size'] == 500


def test_future_creation_edit_history_and_no_early_visibility(env):
    engine, auth, client, headers = env
    h = headers()
    assert client.post('/setup/products/milk', headers=h, json=body(effective='2026-10-12')).status_code == 201
    assert client.get('/products', headers=h).json() == []
    assert client.put('/setup/products/milk', headers=h, json=body(1, '2026-10-13',
        {**PRODUCT, 'units_per_container': 12})).status_code == 200
    a = client.get('/setup/products?as_of=2026-10-12', headers=h).json()[0]
    b = client.get('/setup/products?as_of=2026-10-13', headers=h).json()[0]
    assert (a['revision'], a['head_revision'], a['data']['units_per_container']) == (1, 2, 24)
    assert (b['revision'], b['data']['units_per_container']) == (2, 12)
    assert client.put('/setup/products/milk', headers=h, json=body(2, '2026-10-12')).status_code == 409
    assert len(client.get('/setup/history/product/milk', headers=h).json()) == 2


@pytest.mark.parametrize('changes', [
    {'pack_size': 0}, {'units_per_container': -1}, {'full_container_g': 1.5},
    {'lane': 'cold'}, {'returnable': 'true'}, {'shelf_life_minutes': 0}, {'name': ' '},
    {'unit_gross_g': 1000, 'tare_g': 10}, {'unit_gross_g': 10}, {'surprise': 1},
])
def test_invalid_packaging_never_writes(env, changes):
    engine, auth, client, headers = env
    assert client.post('/setup/products/milk', headers=headers(), json=body(data={**PRODUCT, **changes})).status_code == 422
    with engine.connect() as tx:
        assert tx.scalar(select(func.count()).select_from(s.commands)) == 0


def test_authorization_and_cross_run_replay_denied(env):
    engine, auth, client, headers = env
    h = headers(); payload = body()
    assert client.post('/setup/products/milk', headers=h, json=payload).status_code == 201
    other = headers('other-admin', 'b')
    assert client.get('/setup/products', headers=other).json() == []
    assert client.put('/setup/products/milk', headers=other, json=body(1)).status_code == 404
    for path in ('/setup/products', '/setup/settings', '/setup/history/product/milk'):
        assert client.get(path, headers=headers('plant_manager')).status_code == 403
        assert client.get(path).status_code == 401
    assert client.post('/setup/products/milk', headers=headers('plant_manager'), json=payload).status_code == 403
    assert client.post('/auth/logout', headers=h).status_code == 200
    assert client.post('/setup/products/milk', headers=h, json=payload).status_code == 401


def test_settings_complete_registry_dates_and_validation(env):
    engine, auth, client, headers = env
    h = headers()
    initial = client.get('/setup/settings', headers=h).json()
    values = {**initial['data'], 'app_name': 'New Dairy', 'booth_approval_limit': '0.25'}
    assert client.put('/setup/settings', headers=h, json=body(data={'app_name': 'Oops'})).status_code == 422
    assert client.put('/setup/settings', headers=h, json=body(data={**values, 'booth_cutoff': '19:00:00'})).status_code == 422
    result = client.put('/setup/settings', headers=h, json=body(effective='2026-10-12', data=values))
    assert result.status_code == 200, result.text
    assert client.get('/setup/settings', headers=h).json()['data']['app_name'] == 'Allocra'
    assert client.get('/setup/settings?as_of=2026-10-12', headers=h).json()['data']['app_name'] == 'New Dairy'
    with engine.connect() as tx:
        assert tx.scalar(select(s.runs.c.settings).where(s.runs.c.id == 'a')) == {}


def test_existing_baseline_and_dispatched_inputs_preserved(env):
    engine, auth, client, headers = env
    saved = {'packaging': PRODUCT, 'settings': Settings().model_dump(mode='json')}
    with unit_of_work(engine) as tx:
        tx.execute(s.products.insert().values(run_id='a', id='milk', **{k:v for k,v in PRODUCT.items() if k != 'sku_id'}))
        tx.execute(s.days.insert().values(run_id='a', id='day', delivery_date=date(2026,10,11), state='open'))
        tx.execute(s.nodes.insert().values(run_id='a', id='plant', kind='plant', name='Plant', lat_e7=1, lon_e7=1))
        tx.execute(s.drivers.insert().values(run_id='a', id='driver', user_id='admin', home_id='plant', name='Driver', licence='test'))
        tx.execute(s.vehicles.insert().values(run_id='a', id='van', home_id='plant', level='plant', name='Van',
            capacity_slots=10, capacity_g=100000, lane_configurations=[['chilled']], colour='#abcdef'))
        tx.execute(s.plans.insert().values(run_id='a', id='plan', delivery_date=date(2026,10,11), source_id='plant',
            input_hash='x'*64, state='planned', snapshot=saved))
        tx.execute(s.trips.insert().values(run_id='a', id='trip', plan_id='plan', vehicle_id='van', driver_id='driver',
            state='dispatched', departure_at=NOW, return_at=NOW.replace(hour=9), distance_m=1000, manifest_snapshot=saved))
    h = headers()
    assert client.put('/setup/products/milk', headers=h, json=body()).status_code == 409
    assert client.put('/setup/products/milk', headers=h, json=body(effective='2026-10-12',
        data={**PRODUCT, 'units_per_container': 12})).status_code == 200
    assert client.put('/setup/settings', headers=h, json=body(effective='2026-10-12',
        data={**Settings().model_dump(mode='json'), 'fuel_km_per_l': '7'})).status_code == 200
    with engine.connect() as tx:
        assert resolve(tx, 'a', 'product', 'milk', date(2026,10,11))['data']['units_per_container'] == 24
        assert tx.scalar(select(s.trips.c.manifest_snapshot)) == saved
        assert tx.scalar(select(s.plans.c.snapshot)) == saved


def test_referenced_deletion_rolls_back_then_unreferenced_tombstone(env):
    engine, auth, client, headers = env
    h = headers()
    assert client.post('/setup/products/milk', headers=h, json=body()).status_code == 201
    with unit_of_work(engine) as tx:
        tx.execute(s.lots.insert().values(run_id='a', id='lot', sku_id='milk', batch='batch', expires_at=NOW, status='usable'))
    deletion = body(1); deletion.pop('data')
    assert client.request('DELETE', '/setup/products/milk', headers=h, json=deletion).status_code == 409
    with engine.connect() as tx:
        assert tx.scalar(select(func.count()).select_from(s.commands)) == 1
    unreferenced = {**PRODUCT, 'sku_id': 'new'}
    assert client.post('/setup/products/new', headers=h, json=body(data=unreferenced)).status_code == 201
    deletion['command_id'] = str(uuid4())
    assert client.request('DELETE', '/setup/products/new', headers=h, json=deletion).status_code == 200
    assert [r['id'] for r in client.get('/products', headers=h).json()] == ['milk']
    assert client.post('/setup/products/new', headers=h, json=body(data=unreferenced)).status_code == 409


def test_revision_immutability_and_concurrent_edits(env):
    engine, auth, client, headers = env
    h = headers()
    assert client.post('/setup/products/milk', headers=h, json=body()).status_code == 201
    def edit(name):
        return client.put('/setup/products/milk', headers=h, json=body(1, data={**PRODUCT, 'name': name})).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ('First', 'Second'))) == [200, 409]
    with pytest.raises(IntegrityError):
        with unit_of_work(engine) as tx:
            tx.execute(update(revisions).values(snapshot={}))
    with engine.connect() as tx:
        assert tx.scalar(select(func.count()).select_from(revisions)) == 2


def test_business_date_uses_simulation_clock_not_client_time(env):
    engine, auth, client, headers = env
    with unit_of_work(engine) as tx:
        tx.execute(s.clocks.insert().values(run_id='a', id='clock', sim_time=NOW.replace(day=20), mode='paused', speed=1))
    assert client.post('/setup/products/milk', headers=headers(), json=body(effective='2026-10-19')).status_code == 409
    assert client.post('/setup/products/milk', headers=headers(), json=body(effective='2026-10-20')).status_code == 201


@pytest.mark.parametrize('role', ['hub_manager', 'hub_ordering', 'booth', 'driver'])
def test_remaining_roles_cannot_change_or_read_admin_setup(env, role):
    engine, auth, client, headers = env
    with unit_of_work(engine) as tx:
        tx.execute(s.nodes.insert().values(run_id='a', id='plant', kind='plant', name='Plant', lat_e7=1, lon_e7=1))
        tx.execute(s.nodes.insert().values(run_id='a', id='hub', kind='hub', name='Hub', parent_id='plant', parent_kind='plant', lat_e7=1, lon_e7=1))
        tx.execute(s.nodes.insert().values(run_id='a', id='booth', kind='booth', name='Booth', parent_id='hub', parent_kind='hub', lat_e7=1, lon_e7=1))
        tx.execute(s.users.insert().values(id=role, phone=role, pin_hash='!disabled', active=True))
        if role == 'driver':
            tx.execute(s.drivers.insert().values(run_id='a', id='driver', user_id=role, home_id='hub', name='Driver', licence='test'))
        tx.execute(s.grants.insert().values(run_id='a', id=role, user_id=role, role=role,
            hub_id=None if role == 'driver' else 'hub', booth_id='booth' if role == 'booth' else None,
            driver_id='driver' if role == 'driver' else None))
    h = headers(role)
    for path in ('/setup/products', '/setup/products/milk', '/setup/settings', '/setup/history/product/milk'):
        assert client.get(path, headers=h).status_code == 403
    assert client.post('/setup/products/milk', headers=h, json=body()).status_code == 403
    assert client.put('/setup/products/milk', headers=h, json=body()).status_code == 403
    assert client.put('/setup/settings', headers=h, json=body(data=Settings().model_dump(mode='json'))).status_code == 403
    deletion = body(); deletion.pop('data')
    assert client.request('DELETE', '/setup/products/milk', headers=h, json=deletion).status_code == 403


def test_deactivation_and_measured_packaging_round_trip(env):
    engine, auth, client, headers = env
    h = headers()
    product = {**PRODUCT, 'unit_gross_g': 500, 'tare_g': 1000}
    assert client.post('/setup/products/milk', headers=h, json=body(data=product)).status_code == 201
    result = client.get('/setup/products/milk', headers=h).json()
    assert result['data']['unit_gross_g'] == 500
    assert client.put('/setup/products/milk', headers=h, json=body(1, data={**product, 'active': False})).status_code == 200
    assert client.get('/products', headers=h).json() == []
    assert client.get('/setup/products/milk', headers=h).json()['data']['active'] is False


def test_upgrade_existing_0003_preserves_product(tmp_path):
    engine = open_database(tmp_path / 'upgrade.sqlite3')
    try:
        migrate(engine, '0003_auth')
        with unit_of_work(engine) as tx:
            tx.execute(s.runs.insert().values(id='legacy', name='Legacy', seed=1, settings={}))
            tx.execute(s.products.insert().values(run_id='legacy', id='milk', **{k:v for k,v in PRODUCT.items() if k != 'sku_id'}))
        migrate(engine)
        with engine.connect() as tx:
            assert resolve(tx, 'legacy', 'product', 'milk', date(2026,10,11))['data']['pack_size'] == 500
            assert tx.scalar(select(func.count()).select_from(revisions)) == 0
    finally:
        engine.dispose()


def test_location_route_authorizes_and_releases_connection_before_provider(env, monkeypatch):
    engine, auth, client, headers = env
    calls = []
    def provider(location):
        assert engine.pool.checkedout() == 0
        calls.append(location)
        return {'validated': True}
    monkeypatch.setattr('v2.catalog.api.validate_location', provider)
    payload = {'lat': 13.148, 'lon': 80.231}
    assert client.post('/setup/locations/validate', headers=headers('plant_manager'), json=payload).status_code == 403
    assert not calls
    assert client.post('/setup/locations/validate', headers=headers(), json=payload).status_code == 200
    assert len(calls) == 1


def test_location_route_does_not_return_approval_after_revocation(env, monkeypatch):
    engine, auth, client, headers = env
    h = headers()
    def provider(location):
        with unit_of_work(engine) as tx:
            tx.execute(update(s.users).where(s.users.c.id == 'admin').values(active=False))
        return {'validated': True}
    monkeypatch.setattr('v2.catalog.api.validate_location', provider)
    assert client.post('/setup/locations/validate', headers=h, json={'lat': 13.148, 'lon': 80.231}).status_code == 401
