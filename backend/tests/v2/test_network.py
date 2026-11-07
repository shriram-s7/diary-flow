from datetime import date
from uuid import uuid4
import pytest
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from test_catalog import env, body
from v2.db import schema as s
from v2.db.engine import unit_of_work
from v2.catalog.tables import node_revisions
from v2.catalog.roads import RoadError


def node(kind='plant', parent=None, **kw):
    return dict(name=kw.pop('name', kind), kind=kind, parent_id=parent, lat=13.148, lon=80.231, **kw)


@pytest.fixture
def network(env, monkeypatch):
    engine, auth, client, headers = env
    calls = []
    def snap(location):
        assert engine.pool.checkedout() == 0
        calls.append(location)
        return {'validated': True, 'raw': location.model_dump(),
            'snapped': location.model_dump(), 'snap_distance_m': 0,
            'provenance': {'provider': 'declared-test-fixture'}}
    monkeypatch.setattr('v2.catalog.network.validate_location', snap)
    return (*env, calls)


def create(client, h, entity_id, data, effective='2026-10-11'):
    return client.post('/setup/network/'+entity_id, headers=h, json=body(effective=effective, data=data))


def test_network_tree_road_provenance_and_exact_retry(network, monkeypatch):
    engine, auth, client, headers, calls = network
    h = headers(); payload = body(data=node())
    response = client.post('/setup/network/plant', headers=h, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()['data']['road_validation']['provenance']['provider'] == 'declared-test-fixture'
    def offline(location):
        raise RoadError('offline', 'Offline', 503)
    monkeypatch.setattr('v2.catalog.network.validate_location', offline)
    assert client.post('/setup/network/plant', headers=h, json=payload).json() == response.json()
    changed = {**payload, 'reason': 'Changed payload'}
    assert client.post('/setup/network/plant', headers=h, json=changed).status_code == 409
    assert len(calls) == 1
    with engine.connect() as tx:
        for table in (s.commands, s.audit, s.outbox, node_revisions):
            assert tx.scalar(select(func.count()).select_from(table)) == 1


def test_levels_unique_plant_and_home_validation(network):
    engine, auth, client, headers, calls = network
    h=headers()
    assert create(client,h,'hub',node('hub','missing')).status_code == 409
    assert create(client,h,'plant',node()).status_code == 201
    assert create(client,h,'other',node(name='Other')).status_code == 409
    assert create(client,h,'booth',node('booth','plant')).status_code == 409
    assert create(client,h,'hub',node('hub','plant')).status_code == 201
    assert create(client,h,'booth',node('booth','hub')).status_code == 201
    assert len(client.get('/network',headers=h).json()) == 3
    payload=body();payload.pop('data')
    assert client.request('DELETE','/setup/network/plant',headers=h,json={**payload,'expected_revision':1}).status_code == 409
    assert client.request('DELETE','/setup/network/hub',headers=h,json={**payload,'expected_revision':1}).status_code == 409


def test_future_network_visibility_edit_and_tombstone(network):
    engine, auth, client, headers, calls = network
    h=headers()
    assert create(client,h,'plant',node()).status_code == 201
    assert create(client,h,'hub',node('hub','plant'), '2026-10-12').status_code == 201
    assert [r['id'] for r in client.get('/network',headers=h).json()] == ['plant']
    assert client.get('/network/hub',headers=h).status_code == 404
    assert client.put('/setup/network/hub',headers=h,json=body(1,'2026-10-13',node('hub','plant',name='Renamed'))).status_code == 200
    assert client.get('/setup/network/hub?as_of=2026-10-12',headers=h).json()['data']['name'] == 'hub'
    assert client.get('/setup/network/hub?as_of=2026-10-13',headers=h).json()['data']['name'] == 'Renamed'
    payload=body(2,'2026-10-14');payload.pop('data')
    assert client.request('DELETE','/setup/network/hub',headers=h,json=payload).status_code == 200
    assert client.get('/setup/network/hub?as_of=2026-10-14',headers=h).status_code == 404
    assert len(client.get('/setup/history/node/hub',headers=h).json()) == 3
    with pytest.raises(IntegrityError):
        with unit_of_work(engine) as tx:
            tx.execute(update(node_revisions).values(snapshot={}))


def test_provider_failure_and_revocation_never_commit(network, monkeypatch):
    engine, auth, client, headers, calls = network
    h=headers()
    def fail(location):
        raise RoadError('road_too_far','Too far',422)
    monkeypatch.setattr('v2.catalog.network.validate_location',fail)
    assert create(client,h,'plant',node()).status_code == 422
    def revoke(location):
        with unit_of_work(engine) as tx:
            tx.execute(update(s.users).where(s.users.c.id=='admin').values(active=False))
        return {'validated':True}
    monkeypatch.setattr('v2.catalog.network.validate_location',revoke)
    assert create(client,h,'plant',node()).status_code == 401
    with engine.connect() as tx:
        assert tx.scalar(select(func.count()).select_from(s.nodes)) == 0
        assert tx.scalar(select(func.count()).select_from(s.commands)) == 0


def test_permissions_and_client_cannot_forge_validation(network):
    engine, auth, client, headers, calls = network
    assert create(client,headers('plant_manager'),'plant',node()).status_code == 403
    assert not calls
    assert create(client,headers(),'plant',{**node(),'road_validation':{'validated':True}}).status_code == 422
    assert not calls
    assert create(client,headers(),'plant',node()).status_code == 201
    assert client.get('/setup/network',headers=headers('other-admin','b')).json() == []
    assert client.get('/setup/network/plant',headers=headers('other-admin','b')).status_code == 404


def test_started_days_and_parent_scheduled_inactive(network):
    engine, auth, client, headers, calls = network
    h=headers()
    assert create(client,h,'plant',node()).status_code == 201
    assert create(client,h,'hub',node('hub','plant')).status_code == 201
    assert client.put('/setup/network/hub',headers=h,json=body(1,'2026-10-13',node('hub','plant',active=False))).status_code == 200
    assert create(client,h,'booth',node('booth','hub'),'2026-10-12').status_code == 409
    with unit_of_work(engine) as tx:
        tx.execute(s.days.insert().values(run_id='a',id='day',delivery_date=date(2026,10,14),state='open'))
    assert client.put('/setup/network/hub',headers=h,json=body(2,'2026-10-14',node('hub','plant'))).status_code == 409


def test_scope_follows_effective_home_and_blocks_referenced_reparent(network):
    engine, auth, client, headers, calls=network
    h=headers()
    for entity_id,data in [('plant',node()),('hub',node('hub','plant')),('hub2',node('hub','plant',name='Second hub')),('booth',node('booth','hub'))]:
        assert create(client,h,entity_id,data).status_code == 201
    with unit_of_work(engine) as tx:
        tx.execute(s.users.insert().values(id='manager',phone='manager',pin_hash='!disabled',active=True))
        tx.execute(s.grants.insert().values(run_id='a',id='manager',user_id='manager',role='hub_manager',hub_id='hub'))
    manager=headers('manager')
    assert {r['id'] for r in client.get('/network',headers=manager).json()} == {'hub','booth'}
    assert client.put('/setup/network/booth',headers=h,json=body(1,data=node('booth','hub2'))).status_code == 200
    assert {r['id'] for r in client.get('/network',headers=manager).json()} == {'hub'}
    with unit_of_work(engine) as tx:
        tx.execute(s.users.insert().values(id='boothuser',phone='boothuser',pin_hash='!disabled',active=True))
        tx.execute(s.grants.insert().values(run_id='a',id='boothuser',user_id='boothuser',role='booth',hub_id='hub2',booth_id='booth'))
    assert client.get('/me',headers=headers('boothuser')).status_code == 200
    assert client.put('/setup/network/booth',headers=h,json=body(2,data=node('booth','hub'))).status_code == 409


def test_edit_racing_road_work_rechecks_revision(network, monkeypatch):
    engine, auth, client, headers, calls = network
    h = headers()
    assert create(client,h,'plant',node()).status_code == 201
    original_snap = __import__('v2.catalog.network', fromlist=['validate_location']).validate_location
    def racing_snap(location):
        monkeypatch.setattr('v2.catalog.network.validate_location', original_snap)
        winner = client.put('/setup/network/plant',headers=h,json=body(1,data=node(name='Winner')))
        assert winner.status_code == 200
        return original_snap(location)
    monkeypatch.setattr('v2.catalog.network.validate_location', racing_snap)
    loser = client.put('/setup/network/plant',headers=h,json=body(1,data=node(name='Stale')))
    assert loser.status_code == 409
    assert client.get('/network/plant',headers=h).json()['name'] == 'Winner'
    with engine.connect() as tx:
        assert tx.scalar(select(func.count()).select_from(s.commands)) == 2


def test_existing_baseline_and_pinned_plan_unchanged(network):
    engine, auth, client, headers, calls = network
    with unit_of_work(engine) as tx:
        tx.execute(s.nodes.insert().values(run_id='a',id='plant',kind='plant',name='Original',lat_e7=131480000,lon_e7=802310000))
        tx.execute(s.days.insert().values(run_id='a',id='day',delivery_date=date(2026,10,11),state='open'))
        tx.execute(s.plans.insert().values(run_id='a',id='plan',delivery_date=date(2026,10,11),source_id='plant',
            input_hash='x'*64,state='planned',snapshot={'network':{'plant':{'name':'Original'}}}))
    h=headers()
    assert client.put('/setup/network/plant',headers=h,json=body(0,'2026-10-12',node(name='New Name'))).status_code == 200
    assert client.get('/network/plant',headers=h).json()['name'] == 'Original'
    assert client.get('/setup/network/plant?as_of=2026-10-12',headers=h).json()['data']['name'] == 'New Name'
    with engine.connect() as tx:
        assert tx.scalar(select(s.plans.c.snapshot)) == {'network':{'plant':{'name':'Original'}}}
