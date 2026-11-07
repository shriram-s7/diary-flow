from datetime import datetime,timedelta,timezone
from dataclasses import replace

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,update,delete

from v2.app import create_app
from v2.auth.bootstrap import seed
from v2.auth.service import AuthService, AuthError, hash_pin, verify_pin
from v2.auth.tables import auth_audit
from v2.db import schema as s
from v2.db.engine import open_database,unit_of_work
from v2.db.migrate import migrate


@pytest.fixture
def service(tmp_path):
    engine=open_database(tmp_path/'auth.sqlite3')
    migrate(engine);seed(engine)
    now=[datetime(2026,10,11,tzinfo=timezone.utc)]
    service=AuthService(engine,'x'*48,demo=True,clock=lambda:now[0])
    service.test_now=now
    with unit_of_work(engine) as tx:
        tx.execute(update(s.users).where(s.users.c.id=='demo-booth').values(pin_hash=hash_pin('123456')))
        for nid,kind,parent,parent_kind in [('other-hub','hub','plant','plant'),('other-booth','booth','other-hub','hub')]:
            tx.execute(s.nodes.insert().values(run_id='auth-demo',id=nid,kind=kind,name=nid,
                parent_id=parent,parent_kind=parent_kind,lat_e7=1,lon_e7=1))
    yield service
    engine.dispose()


def headers(service,role):
    login=service.issue('demo-'+role)
    return {'Authorization':'Bearer '+login['token'],'X-Run-Id':'auth-demo','X-Grant-Id':role}


def test_pin_hashes_salted_no_plaintext():
    a,b=hash_pin('123456'),hash_pin('123456')
    assert a!=b and '123456' not in a
    assert verify_pin('123456',a) and not verify_pin('654321',a)
    assert not verify_pin('123456','broken')
    with pytest.raises(ValueError):hash_pin('1234')


def test_login_logout_and_replay_denied(service):
    client=TestClient(create_app(service))
    response=client.post('/auth/login',json={'phone':'+910000000003','pin':'123456'})
    assert response.status_code==200,response.text
    token=response.json()['token']
    h={'Authorization':'Bearer '+token,'X-Run-Id':'auth-demo','X-Grant-Id':'booth'}
    assert client.get('/me',headers=h).json()['scope']['booth_id']=='booth'
    assert client.post('/auth/logout',headers=h).status_code==200
    assert client.get('/network',headers=h).status_code==401
    assert client.get('/me',headers=h).headers['cache-control']=='no-store'


def test_limits_persist_and_expire_on_wall_time(service):
    for _ in range(5):
        with pytest.raises(AuthError) as e:service.login('+910000000003','000000','ip')
        assert e.value.status==401
    other=AuthService(service.engine,'x'*48,demo=True,clock=service.clock)
    with pytest.raises(AuthError) as e:other.login('+910000000003','123456','ip')
    assert e.value.status==429
    service.test_now[0]+=timedelta(minutes=16)
    assert service.login('+910000000003','123456','ip')['token']


@pytest.mark.parametrize('role,ids',[
    ('admin',{'plant','hub','booth','other-hub','other-booth'}),
    ('plant_manager',{'plant','hub','booth','other-hub','other-booth'}),
    ('hub_manager',{'hub','booth'}),('hub_ordering',{'hub','booth'}),
    ('booth',{'booth'}),('driver',set()),
])
def test_six_role_network_scope_and_read_only_routes(service,role,ids):
    client=TestClient(create_app(service));h=headers(service,role)
    response=client.get('/network',headers=h)
    assert response.status_code==200
    assert {r['id'] for r in response.json()}==ids
    for node in {'plant','hub','booth','other-hub','other-booth'}-ids:
        assert client.get('/network/'+node,headers=h).status_code==404
    assert client.post('/network',headers=h,json={}).status_code==405


def test_forged_run_and_grant_and_tampered_token(service):
    client=TestClient(create_app(service));h=headers(service,'booth')
    assert client.get('/me',headers=h|{'X-Run-Id':'another'}).status_code==403
    assert client.get('/me',headers=h|{'X-Grant-Id':'admin'}).status_code==403
    assert client.get('/me',headers=h|{'Authorization':h['Authorization']+'invalid'}).status_code==401
    claims={'sub':'demo-admin','sid':'fake','iss':'allocra','aud':'allocra-app'}
    unsigned=jwt.encode(claims,key='',algorithm='none')
    assert client.get('/me',headers=h|{'Authorization':'Bearer '+unsigned}).status_code==401


@pytest.mark.parametrize('change',['user','session_version','grant','booth_parent','driver'])
def test_current_assignments_and_revocation_not_cached(service,change):
    role='driver' if change=='driver' else 'booth'
    client=TestClient(create_app(service));h=headers(service,role)
    assert client.get('/me',headers=h).status_code==200
    with unit_of_work(service.engine) as tx:
        if change=='user':tx.execute(update(s.users).where(s.users.c.id=='demo-booth').values(active=False))
        if change=='session_version':tx.execute(update(s.users).where(s.users.c.id=='demo-booth').values(session_version=1))
        if change=='grant':tx.execute(delete(s.grants).where(s.grants.c.id=='booth'))
        if change=='booth_parent':tx.execute(update(s.nodes).where(s.nodes.c.id=='booth').values(parent_id='other-hub'))
        if change=='driver':tx.execute(update(s.drivers).where(s.drivers.c.id=='driver').values(active=False))
    assert client.get('/me',headers=h).status_code in (401,403)


def test_expiry_uses_wall_time_and_rejects_future_tokens(service):
    client=TestClient(create_app(service));h=headers(service,'booth')
    service.test_now[0]+=timedelta(hours=8)
    assert client.get('/me',headers=h).status_code==401
    service.test_now[0]-=timedelta(hours=9)
    assert client.get('/me',headers=h).status_code==401


def test_demo_off_and_audited_view_as(service):
    client=TestClient(create_app(service));admin=headers(service,'admin')
    response=client.post('/auth/view-as',headers=admin,json={'user_id':'demo-booth'})
    assert response.status_code==200
    token=response.json()['token']
    with service.engine.connect() as tx:
        identity=service.identity(tx,token,'auth-demo','booth')
        audit=tx.execute(select(auth_audit).where(auth_audit.c.kind=='demo_view_as')).mappings().one()
    assert identity.real_actor_id=='demo-admin'
    assert audit['actor_id']=='demo-admin' and audit['effective_actor_id']=='demo-booth'
    assert client.post('/auth/view-as',headers=headers(service,'booth'),json={'user_id':'demo-admin'}).status_code==403
    service.demo=False
    assert client.get('/auth/demo-users').status_code==404
    assert client.post('/auth/demo-login',json={'user_id':'demo-admin'}).status_code==404
    assert client.post('/auth/view-as',headers=admin,json={'user_id':'demo-booth'}).status_code==404


def test_websocket_scope_and_revocation(service):
    client=TestClient(create_app(service));h=headers(service,'booth')
    with client.websocket_connect('/stream') as ws:
        ws.send_json({'token':h['Authorization'][7:],'run_id':'auth-demo','grant_id':'booth'})
        message=ws.receive_json()
        assert message['scope']['booth_id']=='booth' and message['trips']==[]
        client.post('/auth/logout',headers=h)
        event=ws.receive()
        assert event['type']=='websocket.close' and event['code']==4401


def test_no_credentials_fail_closed(service):
    client=TestClient(create_app(service))
    for url in ['/me','/network','/trips','/products']:
        assert client.get(url).status_code==401
    assert TestClient(create_app()).get('/status').json()['configured'] is False


def test_queued_command_replay_rechecks_authorization(service):
    from v2.auth.commands import execute_authenticated
    from v2.domain.types import CommandContext
    h=headers(service,'booth');token=h['Authorization'][7:]
    ctx=CommandContext(command_id='queued',run_id='auth-demo',actor_id='demo-booth',
        expected_revision=0,delivery_date=service.clock().date(),occurred_at=service.clock())
    policy=lambda tx,identity,payload: None
    call=lambda:execute_authenticated(service,token,'booth',ctx,'test',{},lambda effects:{'saved':True},
        sim_time=service.clock(),policy=policy)
    assert call()=={'saved':True}
    with unit_of_work(service.engine) as tx:
        identity=service.identity(tx,token,'auth-demo','booth')
        service.logout(tx,identity)
    with pytest.raises(AuthError):call()


def test_delegated_command_audits_real_and_effective_actor(service):
    from v2.auth.commands import execute_authenticated
    from v2.domain.types import CommandContext
    client = TestClient(create_app(service))
    response = client.post('/auth/view-as', headers=headers(service, 'admin'),
                           json={'user_id': 'demo-booth'})
    assert response.status_code == 200
    ctx = CommandContext(command_id='delegated', run_id='auth-demo', actor_id='demo-booth',
                         expected_revision=0, delivery_date=service.clock().date(), occurred_at=service.clock())
    execute_authenticated(service, response.json()['token'], 'booth', ctx, 'test', {},
                          lambda effects: {'saved': True}, sim_time=service.clock(),
                          policy=lambda tx, identity, payload: None)
    with service.engine.connect() as tx:
        audit = tx.execute(select(auth_audit).where(
            auth_audit.c.kind == 'view_as_command_authorized')).mappings().one()
    assert audit['actor_id'] == 'demo-admin'
    assert audit['effective_actor_id'] == 'demo-booth'


def test_operation_roles_and_unknown_actions_default_deny(service):
    from v2.auth.policy import require_node_action,require_setup
    for role in ('admin','plant_manager','hub_manager','hub_ordering','booth'):
        h=headers(service,role)
        with service.engine.connect() as tx:
            identity=service.identity(tx,h['Authorization'][7:],'auth-demo',role)
            node='plant' if role in ('admin','plant_manager') else 'booth' if role=='booth' else 'hub'
            require_node_action(tx,identity,node,'read')
            if role in ('plant_manager','hub_manager'):require_node_action(tx,identity,node,'operate')
            else:
                with pytest.raises(AuthError):require_node_action(tx,identity,node,'operate')
            with pytest.raises(AuthError):require_node_action(tx,identity,node,'invented-action')
            if role=='admin':require_setup(identity)
            else:
                with pytest.raises(AuthError):require_setup(identity)


def test_driver_only_assigned_trips_and_reassignment(service):
    from v2.auth.policy import require_trip_action
    now=service.clock()
    with unit_of_work(service.engine) as tx:
        tx.execute(s.days.insert().values(run_id='auth-demo',id='day',delivery_date=now.date(),state='open'))
        tx.execute(s.users.insert().values(id='other-driver-user',phone='+910000000099',pin_hash='!disabled',active=False))
        tx.execute(s.drivers.insert().values(run_id='auth-demo',id='other-driver',user_id='other-driver-user',
            home_id='hub',name='Other driver',licence='test'))
        tx.execute(s.plans.insert().values(run_id='auth-demo',id='plan',delivery_date=now.date(),source_id='hub',
            input_hash='x'*64,state='planned',snapshot={}))
        for i,driver in enumerate(('driver','other-driver')):
            tx.execute(s.vehicles.insert().values(run_id='auth-demo',id=f'v{i}',home_id='hub',level='hub',
                name=f'v{i}',capacity_slots=100,capacity_g=100000,lane_configurations=[['chilled']],colour='#abcdef'))
            tx.execute(s.trips.insert().values(run_id='auth-demo',id=f't{i}',plan_id='plan',vehicle_id=f'v{i}',
                driver_id=driver,state='planned',departure_at=now+timedelta(hours=i*2),
                return_at=now+timedelta(hours=i*2+1),distance_m=10000,manifest_snapshot={}))
    h=headers(service,'driver');client=TestClient(create_app(service))
    assert [t['id'] for t in client.get('/trips',headers=h).json()]==['t0']
    with service.engine.connect() as tx:
        identity=service.identity(tx,h['Authorization'][7:],'auth-demo','driver')
        require_trip_action(tx,identity,'t0','drive')
        with pytest.raises(AuthError):require_trip_action(tx,identity,'t1','read')
    plant=headers(service,'plant_manager')
    with service.engine.connect() as tx:
        identity=service.identity(tx,plant['Authorization'][7:],'auth-demo','plant_manager')
        require_trip_action(tx,identity,'t0','read')
        with pytest.raises(AuthError):require_trip_action(tx,identity,'t0','operate')
    with unit_of_work(service.engine) as tx:
        tx.execute(update(s.trips).where(s.trips.c.id=='t0').values(driver_id='other-driver'))
    assert client.get('/trips',headers=h).json()==[]
