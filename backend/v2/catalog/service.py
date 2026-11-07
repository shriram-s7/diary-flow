"""Setup commands with dated immutable snapshots and no writes to trip inputs.

The original products/runs rows supply a baseline and retain foreign-key identity.
Once versioned, use resolve() instead of those rows for operational configuration.
"""
from datetime import date, timedelta, timezone
from sqlalchemy import select, update, and_

from v2.auth.commands import execute_authenticated
from v2.auth.policy import require_setup
from v2.auth.service import AuthError
from v2.db import schema as s
from v2.db.commands import Conflict
from v2.domain.types import CommandContext
from v2.domain.settings import Settings
from .models import Product
from .tables import revisions

BUSINESS_ZONE = timezone(timedelta(hours=5, minutes=30))


def simulation_time(tx, run_id, wall_time):
    return tx.execute(select(s.clocks.c.sim_time).where(s.clocks.c.run_id == run_id)).scalar_one_or_none() or wall_time


def business_date(tx, run_id, wall_time):
    return simulation_time(tx, run_id, wall_time).astimezone(BUSINESS_ZONE).date()


def version_rows(tx, run_id, kind, entity_id):
    return tx.execute(select(revisions).where(revisions.c.run_id == run_id,
        revisions.c.kind == kind, revisions.c.entity_id == entity_id)
        .order_by(revisions.c.revision.desc())).mappings().all()


def baseline(tx, run_id, kind, entity_id):
    if kind == 'product':
        row = tx.execute(select(s.products).where(s.products.c.run_id == run_id,
                                                s.products.c.id == entity_id)).mappings().first()
        if row is None:
            return None
        fields = {k: row[k] for k in Product.model_fields if k in row}
        fields['sku_id'] = entity_id
        data = Product(**fields).model_dump(mode='json')
        rev = row['revision']
    elif kind == 'settings' and entity_id == 'registry':
        row = tx.execute(select(s.runs.c.settings).where(s.runs.c.id == run_id)).first()
        if row is None:
            return None
        data, rev = Settings(**row[0]).model_dump(mode='json'), 0
    else:
        raise ValueError('Unknown setup resource')
    return {'revision': rev, 'effective_date': date.min, 'snapshot': data, 'deleted': False}


def resolve(tx, run_id, kind, entity_id, as_of):
    rows = version_rows(tx, run_id, kind, entity_id)
    head = rows[0]['revision'] if rows else None
    chosen = next((r for r in rows if r['effective_date'] <= as_of), None) if rows else baseline(
        tx, run_id, kind, entity_id)
    if chosen is None or chosen['deleted']:
        return None
    return {'id': entity_id, 'revision': chosen['revision'],
            'head_revision': head if head is not None else chosen['revision'],
            'effective_date': chosen['effective_date'].isoformat(), 'data': chosen['snapshot']}


def list_products(tx, run_id, as_of):
    ids = tx.execute(select(s.products.c.id).where(s.products.c.run_id == run_id)
                     .order_by(s.products.c.id)).scalars()
    return [item for entity_id in ids if (item := resolve(tx, run_id, 'product', entity_id, as_of)) is not None]


def require_unreferenced(tx, run_id, entity_id):
    # Inspect every declared product FK, including future schema additions.
    for table in s.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            if constraint.referred_table is not s.products:
                continue
            conditions = []
            for element in constraint.elements:
                value = run_id if element.column.name == 'run_id' else entity_id
                conditions.append(element.parent == value)
            if tx.execute(select(table).where(and_(*conditions)).limit(1)).first():
                raise Conflict('Product is referenced by saved business records; deactivate it instead')


def mutate(auth, token, run_id, grant_id, kind, entity_id, operation, body):
    if kind not in ('product', 'settings') or operation not in ('create', 'replace', 'delete'):
        raise ValueError('Unknown setup operation')
    if kind == 'settings' and (entity_id != 'registry' or operation != 'replace'):
        raise ValueError('Settings registry supports replacement only')
    if kind == 'product' and operation != 'delete' and body.data.sku_id != entity_id:
        raise ValueError('Product ID in path and body must match')
    # Authorize once to derive actor, then again inside the atomic command before replay.
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
            raise Conflict('This change would affect a started day; choose a later delivery date')
        rows = version_rows(tx, run_id, kind, entity_id)
        original = baseline(tx, run_id, kind, entity_id)
        head = rows[0] if rows else original
        if operation == 'create':
            if head is not None or body.expected_revision != 0:
                raise Conflict('Product ID already exists or creation revision is not zero')
            revision = 1
        else:
            if head is None or head['deleted']:
                raise AuthError(404, 'Setup record not found')
            if body.expected_revision != head['revision']:
                raise Conflict('Setup revision changed; reload before editing')
            if body.effective_date < head['effective_date']:
                raise Conflict('Effective date cannot precede the latest scheduled revision')
            revision = head['revision'] + 1
            if not rows:
                tx.execute(revisions.insert().values(run_id=run_id, kind=kind, entity_id=entity_id,
                    command_id=context.command_id, **original))
        if operation == 'delete':
            require_unreferenced(tx, run_id, entity_id)
            data = head['snapshot']
        else:
            data = body.data.model_dump(mode='json')
        if operation == 'create':
            values = {k: v for k, v in data.items() if k in s.products.c}
            tx.execute(s.products.insert().values(run_id=run_id, id=entity_id, revision=revision, **values))
        elif kind == 'product':
            tx.execute(update(s.products).where(s.products.c.run_id == run_id,
                s.products.c.id == entity_id).values(revision=revision))
        tx.execute(revisions.insert().values(run_id=run_id, kind=kind, entity_id=entity_id,
            revision=revision, effective_date=body.effective_date, snapshot=data,
            deleted=operation == 'delete', command_id=context.command_id))
        effects.notify('setup_changed', 'run:' + run_id, {'kind': kind, 'id': entity_id,
            'revision': revision, 'effective_date': body.effective_date.isoformat()})
        return {'id': entity_id, 'revision': revision, 'effective_date': body.effective_date.isoformat(),
                'deleted': operation == 'delete', 'data': data}

    return execute_authenticated(auth, token, grant_id, context, f'catalog.{kind}.{operation}',
        {'id': entity_id, **body.model_dump(mode='json')}, handler, sim_time=sim_time,
        policy=lambda tx, current_identity, payload: require_setup(current_identity))
