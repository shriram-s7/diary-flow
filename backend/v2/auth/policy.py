"""Default-deny role gates shared by future handlers, exports and file access."""
from sqlalchemy import select
from v2.db import schema as s
from .service import AuthError, node_filter, trip_filter


def require_setup(identity):
    if identity.scope.role.value != 'admin':
        raise AuthError(403, 'Only administrators can change setup.')


def require_node_action(tx, identity, node_id, action):
    from datetime import datetime, timezone
    from v2.catalog.network import scoped_nodes
    from v2.catalog.service import business_date
    record = next((r for r in scoped_nodes(tx, identity,
        business_date(tx, identity.run_id, datetime.now(timezone.utc))) if r['id'] == node_id), None)
    node = {**record['data'], 'id': record['id'], 'revision': record['revision']} if record else None
    if not node: raise AuthError(404,'Not found')
    role=identity.scope.role.value
    allowed = action=='read'
    if action=='order':
        allowed=(role=='booth' and node['id']==identity.scope.booth_id) or (
            role=='hub_ordering' and node['id']==identity.scope.hub_id)
    if action=='operate':
        allowed=(role=='plant_manager' and node['kind']=='plant') or (
            role=='hub_manager' and node['id']==identity.scope.hub_id)
    if action=='setup': allowed=role=='admin'
    if not allowed: raise AuthError(403,'This action is not allowed for your role.')
    return node


def require_trip_action(tx,identity,trip_id,action):
    trip=tx.execute(select(s.trips).where(trip_filter(identity),s.trips.c.id==trip_id)).mappings().first()
    if not trip: raise AuthError(404,'Not found')
    role=identity.scope.role.value
    allowed=action=='read'
    if action=='drive': allowed=role=='driver'
    if action=='operate':
        source=tx.execute(select(s.nodes.c.kind).join(s.plans,
            (s.plans.c.run_id==s.nodes.c.run_id)&(s.plans.c.source_id==s.nodes.c.id)).where(
            s.plans.c.run_id==identity.run_id,s.plans.c.id==trip['plan_id'])).scalar_one()
        allowed=(role=='plant_manager' and source=='plant') or (role=='hub_manager' and source=='hub')
    if not allowed: raise AuthError(403,'This trip action is not allowed for your role.')
    return trip
