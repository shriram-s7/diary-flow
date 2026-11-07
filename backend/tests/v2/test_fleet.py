from datetime import date, datetime, timezone
import pytest
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from test_catalog import env, body, NOW
from v2.db import schema as s
from v2.db.engine import unit_of_work
from v2.catalog.tables import fleet_revisions


VEHICLE = dict(name='Van',home_id='hub',level='hub',capacity_slots=100,capacity_g=100000,
    lane_configurations=[['chilled'],['frozen']],status='active',colour='#123abc',
    fuel_km_per_l='5',cost_per_km='18',driver_id=None,
    shift={'start':'2026-10-11T23:00:00+05:30','end':'2026-10-12T06:00:00+05:30'})
DRIVER = dict(name='Driver',licence='DL-123',user_id='driver-user',home_id='hub',active=True)


@pytest.fixture
def fleet(env):
    engine,auth,client,headers=env
    with unit_of_work(engine) as tx:
        for entity_id,kind,parent,parent_kind in [('plant','plant',None,None),('hub','hub','plant','plant'),('booth','booth','hub','hub')]:
            tx.execute(s.nodes.insert().values(run_id='a',id=entity_id,name=entity_id,kind=kind,
                parent_id=parent,parent_kind=parent_kind,lat_e7=1,lon_e7=1))
        tx.execute(s.users.insert().values(id='driver-user',phone='driver-user',pin_hash='!disabled',active=True))
        tx.execute(s.grants.insert().values(run_id='a',id='membership',user_id='driver-user',role='plant_manager'))
    return env


def test_vehicle_retry_dates_and_complete_history(fleet):
    engine,auth,client,headers=fleet;h=headers();payload=body(data=VEHICLE)
    r=client.post('/setup/vehicles/van',headers=h,json=payload)
    assert r.status_code==201,r.text
    assert client.post('/setup/vehicles/van',headers=h,json=payload).json()==r.json()
    updated={**VEHICLE,'capacity_slots':120,'shift':{'start':'2026-10-12T23:00:00+05:30','end':'2026-10-13T06:00:00+05:30'}}
    assert client.put('/setup/vehicles/van',headers=h,json=body(1,'2026-10-12',updated)).status_code==200
    assert client.get('/setup/fleet/vehicle/van',headers=h).json()['data']['capacity_slots']==100
    assert client.get('/setup/fleet/vehicle/van?as_of=2026-10-12',headers=h).json()['data']['capacity_slots']==120
    assert len(client.get('/setup/history/vehicle/van',headers=h).json())==2
    with engine.connect() as tx:
        for table in (s.commands,s.audit,s.outbox,fleet_revisions):
            assert tx.scalar(select(func.count()).select_from(table))==2
    with pytest.raises(IntegrityError):
        with unit_of_work(engine) as tx:tx.execute(update(fleet_revisions).values(snapshot={}))


@pytest.mark.parametrize('changes',[
    {'capacity_slots':0},{'capacity_g':1.5},{'lane_configurations':[]},
    {'lane_configurations':[[]]},{'lane_configurations':[['hot']]},
    {'lane_configurations':[['chilled','chilled']]},{'lane_configurations':[['chilled'],['chilled']]},
    {'fuel_km_per_l':'0'},{'cost_per_km':'-1'},{'colour':'red'},
    {'shift':{'start':'2026-10-11T23:00:00','end':'2026-10-12T06:00:00'}},
    {'shift':{'start':'2026-10-11T23:00:00+05:30','end':'2026-10-11T06:00:00+05:30'}}])
def test_invalid_vehicle_never_commits(fleet,changes):
    engine,auth,client,headers=fleet
    assert client.post('/setup/vehicles/van',headers=headers(),json=body(data={**VEHICLE,**changes})).status_code==422
    with engine.connect() as tx:assert tx.scalar(select(func.count()).select_from(s.commands))==0


@pytest.mark.parametrize('changes',[{'home_id':'missing'},{'home_id':'booth'}, {'level':'plant'}, {'driver_id':'missing'}])
def test_invalid_relationships_rejected(fleet,changes):
    engine,auth,client,headers=fleet
    assert client.post('/setup/vehicles/van',headers=headers(),json=body(data={**VEHICLE,**changes})).status_code==409


def test_driver_identity_and_assignment_overlap(fleet):
    engine,auth,client,headers=fleet;h=headers()
    assert client.post('/setup/drivers/driver',headers=h,json=body(data=DRIVER)).status_code==201
    assigned={**VEHICLE,'driver_id':'driver'}
    assert client.post('/setup/vehicles/one',headers=h,json=body(data=assigned)).status_code==201
    assert client.post('/setup/vehicles/two',headers=h,json=body(data=assigned)).status_code==409
    assert client.put('/setup/drivers/driver',headers=h,json=body(1,data={**DRIVER,'active':False})).status_code==409
    assert client.put('/setup/drivers/driver',headers=h,json=body(1,data={**DRIVER,'home_id':'plant'})).status_code==409
    assert client.put('/setup/drivers/driver',headers=h,json=body(1,data={**DRIVER,'user_id':'admin'})).status_code==409
    deletion=body(1);deletion.pop('data')
    assert client.request('DELETE','/setup/fleet/driver/driver',headers=h,json=deletion).status_code==409


def test_driver_scope_uses_dated_active_flag(fleet):
    engine,auth,client,headers=fleet;h=headers()
    assert client.post('/setup/drivers/driver',headers=h,json=body(data=DRIVER)).status_code==201
    with unit_of_work(engine) as tx:
        tx.execute(s.grants.insert().values(run_id='a',id='driver-user',user_id='driver-user',role='driver',driver_id='driver'))
    dh=headers('driver-user')
    assert client.get('/me',headers=dh).status_code==200
    assert client.put('/setup/drivers/driver',headers=h,json=body(1,data={**DRIVER,'active':False})).status_code==200
    assert client.get('/me',headers=dh).status_code==403


def test_permissions_cross_run_and_delete(fleet):
    engine,auth,client,headers=fleet;h=headers()
    assert client.post('/setup/vehicles/van',headers=headers('plant_manager'),json=body(data=VEHICLE)).status_code==403
    assert client.post('/setup/vehicles/van',headers=h,json=body(data=VEHICLE)).status_code==201
    assert client.get('/setup/fleet/vehicle',headers=headers('other-admin','b')).json()==[]
    assert client.get('/setup/fleet/vehicle/van',headers=headers('other-admin','b')).status_code==404
    assert client.get('/setup/fleet/vehicle',headers=headers('plant_manager')).status_code==403
    deletion=body(1);deletion.pop('data')
    assert client.request('DELETE','/setup/fleet/vehicle/van',headers=h,json=deletion).status_code==200
    assert client.get('/setup/fleet/vehicle',headers=h).json()==[]
    assert client.post('/setup/vehicles/van',headers=h,json=body(data=VEHICLE)).status_code==409


def test_post_dispatch_change_preserves_trip_and_blocks_deletion(fleet):
    engine,auth,client,headers=fleet;h=headers()
    assert client.post('/setup/drivers/driver',headers=h,json=body(data=DRIVER)).status_code==201
    assert client.post('/setup/vehicles/van',headers=h,json=body(data=VEHICLE)).status_code==201
    with unit_of_work(engine) as tx:
        tx.execute(s.days.insert().values(run_id='a',id='day',delivery_date=date(2026,10,11),state='open'))
        tx.execute(s.plans.insert().values(run_id='a',id='plan',delivery_date=date(2026,10,11),source_id='hub',input_hash='x'*64,state='planned',snapshot={'vehicle':VEHICLE}))
        tx.execute(s.trips.insert().values(run_id='a',id='trip',plan_id='plan',vehicle_id='van',driver_id='driver',state='dispatched',
            departure_at=NOW,return_at=NOW.replace(hour=9),distance_m=1000,manifest_snapshot={'vehicle':VEHICLE}))
    assert client.put('/setup/vehicles/van',headers=h,json=body(1,data=VEHICLE)).status_code==409
    updated={**VEHICLE,'capacity_slots':5,'shift':{'start':'2026-10-12T23:00:00+05:30','end':'2026-10-13T06:00:00+05:30'}}
    assert client.put('/setup/vehicles/van',headers=h,json=body(1,'2026-10-12',updated)).status_code==200
    deletion=body(2,'2026-10-13');deletion.pop('data')
    assert client.request('DELETE','/setup/fleet/vehicle/van',headers=h,json=deletion).status_code==409
    with engine.connect() as tx:assert tx.scalar(select(s.trips.c.manifest_snapshot))=={'vehicle':VEHICLE}
