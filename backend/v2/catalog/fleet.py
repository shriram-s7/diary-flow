"""Dated fleet configuration; original rows retain relational identity only."""
from datetime import date, datetime
from sqlalchemy import select, update, and_
from v2.db import schema as s
from v2.db.commands import Conflict
from v2.auth.service import AuthError
from v2.auth.policy import require_setup
from v2.auth.commands import execute_authenticated
from v2.domain.types import CommandContext
from .tables import fleet_revisions, node_revisions
from .network import resolve_node
from .service import business_date, simulation_time, BUSINESS_ZONE

TABLES = {'vehicle': s.vehicles, 'driver': s.drivers}


def history(tx, run_id, kind, entity_id):
    return tx.execute(select(fleet_revisions).where(fleet_revisions.c.run_id == run_id,
        fleet_revisions.c.kind == kind, fleet_revisions.c.entity_id == entity_id)
        .order_by(fleet_revisions.c.revision.desc())).mappings().all()


def baseline(tx, run_id, kind, entity_id):
    table = TABLES[kind]
    row = tx.execute(select(table).where(table.c.run_id == run_id, table.c.id == entity_id)).mappings().first()
    if row is None:
        return None
    data = {k: v for k, v in row.items() if k not in ('id', 'run_id', 'revision', 'created_at', 'attributes')}
    if kind == 'vehicle':
        # Missing legacy schedules/rates are explicit; never manufacture availability.
        data.update(shift=None, driver_id=None, fuel_km_per_l=None, cost_per_km=None)
    return {'revision': row['revision'], 'effective_date': date.min, 'snapshot': data, 'deleted': False}


def resolve_fleet(tx, run_id, kind, entity_id, as_of):
    rows = history(tx, run_id, kind, entity_id)
    chosen = next((r for r in rows if r['effective_date'] <= as_of), None) if rows else baseline(tx, run_id, kind, entity_id)
    if chosen is None or chosen['deleted']:
        return None
    return {'id': entity_id, 'revision': chosen['revision'],
        'head_revision': rows[0]['revision'] if rows else chosen['revision'],
        'effective_date': chosen['effective_date'].isoformat(), 'data': chosen['snapshot']}


def list_fleet(tx, run_id, kind, as_of):
    table = TABLES[kind]
    ids = tx.execute(select(table.c.id).where(table.c.run_id == run_id).order_by(table.c.id)).scalars()
    return [r for entity_id in ids if (r := resolve_fleet(tx, run_id, kind, entity_id, as_of))]


def unreferenced(tx, run_id, kind, entity_id):
    target = TABLES[kind]
    for table in s.metadata.tables.values():
        for fk in table.foreign_key_constraints:
            if fk.referred_table is not target:
                continue
            conditions = [e.parent == (run_id if e.column.name == 'run_id' else entity_id) for e in fk.elements]
            if tx.execute(select(table).where(and_(*conditions)).limit(1)).first():
                raise Conflict('Saved trips, grants or inventory reference this record; retain it')
    if kind == 'driver':
        for snapshot in tx.execute(select(fleet_revisions.c.snapshot).where(
            fleet_revisions.c.run_id == run_id, fleet_revisions.c.kind == 'vehicle')).scalars():
            if snapshot.get('driver_id') == entity_id:
                raise Conflict('Driver is assigned in saved vehicle history; retain it')


def validate_relations(tx, run_id, kind, entity_id, data, effective):
    boundaries = {effective}
    for table in (fleet_revisions, node_revisions):
        boundaries.update(tx.execute(select(table.c.effective_date).where(
            table.c.run_id == run_id, table.c.effective_date >= effective)).scalars())
    if kind == 'driver':
        user = tx.execute(select(s.users.c.active).where(s.users.c.id == data['user_id'])).first()
        if not user or (data['active'] and not user[0]):
            raise Conflict('Driver requires an existing active login account')
        if not tx.execute(select(s.grants.c.id).where(s.grants.c.run_id == run_id,
            s.grants.c.user_id == data['user_id'])).first():
            raise Conflict('Driver login must already belong to this run')
    for day in boundaries:
        home = resolve_node(tx, run_id, data['home_id'], day)
        if not home or not home['data']['active'] or home['data']['kind'] not in ('plant', 'hub'):
            raise Conflict('Home must be an active plant or hub at every scheduled boundary')
        if kind == 'vehicle' and home['data']['kind'] != data['level']:
            raise Conflict('Vehicle level must match its home')
        drivers = {r['id']: r['data'] for r in list_fleet(tx, run_id, 'driver', day)}
        vehicles = {r['id']: r['data'] for r in list_fleet(tx, run_id, 'vehicle', day)}
        (drivers if kind == 'driver' else vehicles)[entity_id] = data
        for vehicle_id, vehicle in vehicles.items():
            driver_id = vehicle.get('driver_id')
            if not driver_id or vehicle['status'] != 'active':
                continue
            driver = drivers.get(driver_id)
            if not driver or not driver['active'] or driver['home_id'] != vehicle['home_id']:
                raise Conflict('Assigned driver must be active and share the vehicle home')
        scheduled = [v for v in vehicles.values() if v.get('driver_id') and v.get('shift') and v['status'] == 'active']
        for i, vehicle in enumerate(scheduled):
            start, end = (datetime.fromisoformat(vehicle['shift'][k].replace('Z', '+00:00')) for k in ('start', 'end'))
            for other in scheduled[i+1:]:
                if other['driver_id'] != vehicle['driver_id']:
                    continue
                a, b = (datetime.fromisoformat(other['shift'][k].replace('Z', '+00:00')) for k in ('start', 'end'))
                if start < b and a < end:
                    raise Conflict('Driver is assigned to overlapping vehicle shifts')


def mutate_fleet(auth, token, run_id, grant_id, kind, entity_id, operation, body):
    table = TABLES[kind]
    with auth.engine.connect() as tx:
        identity = auth.identity(tx, token, run_id, grant_id)
        require_setup(identity)
        sim_time = simulation_time(tx, run_id, auth.clock())
    context = CommandContext(command_id=body.command_id, run_id=run_id, actor_id=identity.user_id,
        expected_revision=body.expected_revision, delivery_date=body.effective_date,
        occurred_at=body.occurred_at, reason=body.reason)
    def handler(effects):
        tx = effects.connection
        if body.effective_date < business_date(tx, run_id, auth.clock()):
            raise Conflict('Effective date cannot precede the current business date')
        if tx.execute(select(s.days.c.id).where(s.days.c.run_id == run_id,
            s.days.c.delivery_date >= body.effective_date).limit(1)).first():
            raise Conflict('Choose an effective date after all started days')
        rows = history(tx, run_id, kind, entity_id)
        original = baseline(tx, run_id, kind, entity_id)
        head = rows[0] if rows else original
        if operation == 'create':
            if head is not None or body.expected_revision != 0:
                raise Conflict('ID exists or creation revision is not zero')
            revision = 1
        else:
            if head is None or head['deleted']:
                raise AuthError(404, 'Fleet record not found')
            if body.expected_revision != head['revision'] or body.effective_date < head['effective_date']:
                raise Conflict('Fleet revision or date is stale')
            revision = head['revision'] + 1
        if operation == 'delete':
            unreferenced(tx, run_id, kind, entity_id)
            data = head['snapshot']
        else:
            data = body.data.model_dump(mode='json')
            if kind == 'driver' and head and data['user_id'] != head['snapshot']['user_id']:
                raise Conflict('Driver login identity cannot be reassigned; create a separate driver')
            if kind == 'vehicle':
                start_day = body.data.shift.start.astimezone(BUSINESS_ZONE).date()
                if start_day < body.effective_date:
                    raise Conflict('Shift cannot start before the configuration effective date')
            validate_relations(tx, run_id, kind, entity_id, data, body.effective_date)
        if operation == 'create':
            values = {k: v for k, v in data.items() if k in table.c}
            tx.execute(table.insert().values(run_id=run_id, id=entity_id, revision=revision, **values))
        else:
            if not rows:
                tx.execute(fleet_revisions.insert().values(run_id=run_id, kind=kind, entity_id=entity_id,
                    command_id=context.command_id, **original))
            tx.execute(update(table).where(table.c.run_id == run_id, table.c.id == entity_id).values(revision=revision))
        tx.execute(fleet_revisions.insert().values(run_id=run_id, kind=kind, entity_id=entity_id, revision=revision,
            effective_date=body.effective_date, snapshot=data, deleted=operation == 'delete', command_id=context.command_id))
        effects.notify('setup_changed', 'run:'+run_id, {'kind': kind, 'id': entity_id, 'revision': revision})
        return {'id': entity_id, 'revision': revision, 'effective_date': body.effective_date.isoformat(),
            'deleted': operation == 'delete', 'data': data}
    return execute_authenticated(auth, token, grant_id, context, f'catalog.{kind}.{operation}',
        {'id': entity_id, **body.model_dump(mode='json')}, handler, sim_time=sim_time,
        policy=lambda tx, current_identity, payload: require_setup(current_identity))
