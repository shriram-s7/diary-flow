"""Dated network setup; road calls precede the atomic authenticated command."""
from datetime import date
from math import ceil
from sqlalchemy import select, update, and_

from v2.db import schema as s
from v2.db.commands import Conflict
from v2.auth.service import AuthError
from v2.auth.policy import require_setup
from v2.auth.commands import execute_authenticated
from v2.domain.types import CommandContext
from .tables import node_revisions, fleet_revisions
from .service import business_date, simulation_time
from .roads import Location, validate_location


def history(tx, run_id, entity_id):
    return tx.execute(select(node_revisions).where(node_revisions.c.run_id == run_id,
        node_revisions.c.entity_id == entity_id).order_by(node_revisions.c.revision.desc())).mappings().all()


def baseline(tx, run_id, entity_id):
    row = tx.execute(select(s.nodes).where(s.nodes.c.run_id == run_id, s.nodes.c.id == entity_id)).mappings().first()
    if row is None:
        return None
    data = {k: row[k] for k in ('name', 'kind', 'parent_id', 'priority', 'active')}
    data.update(lat=row['lat_e7']/1e7, lon=row['lon_e7']/1e7,
                road_validation={'validated': False, 'reason': 'Imported baseline has no verified provenance'})
    return {'revision': row['revision'], 'effective_date': date.min, 'snapshot': data, 'deleted': False}


def resolve_node(tx, run_id, entity_id, as_of):
    rows = history(tx, run_id, entity_id)
    chosen = next((r for r in rows if r['effective_date'] <= as_of), None) if rows else baseline(tx, run_id, entity_id)
    if chosen is None or chosen['deleted']:
        return None
    return {'id': entity_id, 'revision': chosen['revision'],
        'head_revision': rows[0]['revision'] if rows else chosen['revision'],
        'effective_date': chosen['effective_date'].isoformat(), 'data': chosen['snapshot']}


def list_nodes(tx, run_id, as_of):
    ids = tx.execute(select(s.nodes.c.id).where(s.nodes.c.run_id == run_id).order_by(s.nodes.c.id)).scalars()
    return [row for entity_id in ids if (row := resolve_node(tx, run_id, entity_id, as_of))]


def scoped_nodes(tx, identity, as_of):
    role, scope = identity.scope.role.value, identity.scope
    rows = list_nodes(tx, identity.run_id, as_of)
    if role in ('admin', 'plant_manager'):
        return rows
    if role in ('hub_manager', 'hub_ordering'):
        return [r for r in rows if r['id'] == scope.hub_id or r['data']['parent_id'] == scope.hub_id]
    if role == 'booth':
        return [r for r in rows if r['id'] == scope.booth_id]
    return []


def unreferenced(tx, run_id, entity_id, *, structural=False):
    for snapshot in tx.execute(select(fleet_revisions.c.snapshot).where(fleet_revisions.c.run_id == run_id)).scalars():
        if snapshot.get('home_id') == entity_id:
            raise Conflict('Node is a home in saved fleet history; retain it')
    for snapshot in tx.execute(select(node_revisions.c.snapshot).where(node_revisions.c.run_id == run_id)).scalars():
        if snapshot.get('parent_id') == entity_id:
            raise Conflict('Node is a home in saved network history; retain it')
    # History is intentionally retained. Other references protect existing relationships.
    for table in s.metadata.tables.values():
        if table is node_revisions:
            continue
        for fk in table.foreign_key_constraints:
            if fk.referred_table is not s.nodes:
                continue
            target = tx.execute(select(s.nodes).where(s.nodes.c.run_id == run_id,
                s.nodes.c.id == entity_id)).mappings().one()
            conditions = [element.parent == target[element.column.name] for element in fk.elements]
            if tx.execute(select(table).where(and_(*conditions)).limit(1)).first():
                raise Conflict('Node has saved references; remove dependent setup first or keep its home and active status')


def validate_tree(tx, run_id, entity_id, data, effective_date):
    # Check every scheduled boundary, so a child cannot outlive an active parent.
    dates = {effective_date, *tx.execute(select(node_revisions.c.effective_date).where(
        node_revisions.c.run_id == run_id, node_revisions.c.effective_date >= effective_date)).scalars()}
    for day in dates:
        rows = {r['id']: r['data'] for r in list_nodes(tx, run_id, day)}
        rows[entity_id] = data
        names = [r['name'].strip().casefold() for r in rows.values()]
        if len(names) != len(set(names)):
            raise Conflict('Node names must be unique within the run')
        if sum(r['kind'] == 'plant' for r in rows.values()) != 1:
            raise Conflict('The network requires exactly one plant')
        for node in rows.values():
            if node['kind'] == 'plant':
                continue
            parent = rows.get(node['parent_id'])
            required = 'plant' if node['kind'] == 'hub' else 'hub'
            if not parent or parent['kind'] != required or (node['active'] and not parent['active']):
                raise Conflict('Home parent must exist at the effective date, have the correct level and be active for active children')


def mutate_node(auth, token, run_id, grant_id, entity_id, operation, body):
    if operation not in ('create', 'replace', 'delete'):
        raise ValueError('Unknown network operation')
    with auth.engine.connect() as tx:
        identity = auth.identity(tx, token, run_id, grant_id)
        require_setup(identity)
        sim_time = simulation_time(tx, run_id, auth.clock())
        replay = tx.execute(select(s.commands.c.id).where(s.commands.c.run_id == run_id,
            s.commands.c.id == body.command_id)).first()
    context = CommandContext(command_id=body.command_id, run_id=run_id, actor_id=identity.user_id,
        expected_revision=body.expected_revision, delivery_date=body.effective_date,
        occurred_at=body.occurred_at, reason=body.reason)
    # Exact replay is validated by the command layer and does not depend on provider uptime.
    evidence = None
    if operation != 'delete' and not replay:
        evidence = validate_location(Location(lat=body.data.lat, lon=body.data.lon))

    def handler(effects):
        tx = effects.connection
        if body.effective_date < business_date(tx, run_id, auth.clock()):
            raise Conflict('Effective date cannot precede the current business date')
        if tx.execute(select(s.days.c.id).where(s.days.c.run_id == run_id,
            s.days.c.delivery_date >= body.effective_date).limit(1)).first():
            raise Conflict('Choose an effective date after all started days')
        rows = history(tx, run_id, entity_id)
        original = baseline(tx, run_id, entity_id)
        head = rows[0] if rows else original
        if operation == 'create':
            if head is not None or body.expected_revision != 0:
                raise Conflict('Node ID exists or creation revision is not zero')
            revision = 1
        else:
            if head is None or head['deleted']:
                raise AuthError(404, 'Node not found')
            if body.expected_revision != head['revision'] or body.effective_date < head['effective_date']:
                raise Conflict('Node revision or effective date is stale')
            revision = head['revision'] + 1
        if operation == 'delete':
            if head['snapshot']['kind'] == 'plant':
                raise Conflict('The plant cannot be deleted')
            unreferenced(tx, run_id, entity_id)
            data = head['snapshot']
        else:
            if evidence is None:
                raise Conflict('Retry the original command')
            data = {**body.data.model_dump(mode='json'), 'road_validation': evidence}
            data['name'] = data['name'].strip()
            if head:
                previous = head['snapshot']
                if data['kind'] != previous['kind']:
                    raise Conflict('A node cannot change its level')
                if data['parent_id'] != previous['parent_id'] or (previous['active'] and not data['active']):
                    unreferenced(tx, run_id, entity_id, structural=True)
            validate_tree(tx, run_id, entity_id, data, body.effective_date)
        if operation == 'create':
            tx.execute(s.nodes.insert().values(run_id=run_id, id=entity_id, revision=revision,
                kind=data['kind'], name=data['name'], parent_id=data['parent_id'],
                parent_kind={'plant': None, 'hub': 'plant', 'booth': 'hub'}[data['kind']],
                lat_e7=round(data['lat']*1e7), lon_e7=round(data['lon']*1e7),
                road_lat_e7=round(evidence['snapped']['lat']*1e7), road_lon_e7=round(evidence['snapped']['lon']*1e7),
                snap_distance_m=ceil(evidence['snap_distance_m']), priority=data['priority'], active=data['active']))
        else:
            if not rows:
                tx.execute(node_revisions.insert().values(run_id=run_id, entity_id=entity_id,
                    command_id=context.command_id, **original))
            tx.execute(update(s.nodes).where(s.nodes.c.run_id == run_id, s.nodes.c.id == entity_id).values(revision=revision))
        tx.execute(node_revisions.insert().values(run_id=run_id, entity_id=entity_id, revision=revision,
            effective_date=body.effective_date, snapshot=data, deleted=operation == 'delete', command_id=context.command_id))
        effects.notify('setup_changed', 'run:'+run_id, {'kind': 'node', 'id': entity_id, 'revision': revision})
        return {'id': entity_id, 'revision': revision, 'effective_date': body.effective_date.isoformat(),
                'deleted': operation == 'delete', 'data': data}

    return execute_authenticated(auth, token, grant_id, context, 'catalog.node.'+operation,
        {'id': entity_id, **body.model_dump(mode='json')}, handler, sim_time=sim_time,
        policy=lambda tx, current_identity, payload: require_setup(current_identity))
