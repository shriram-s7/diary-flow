from datetime import date
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.exc import IntegrityError
from v2.auth.policy import require_setup
from v2.auth.service import AuthError
from v2.db.commands import Conflict
from .models import ProductMutation, SettingsMutation, Mutation, NodeMutation, VehicleMutation, DriverMutation
from .fleet import mutate_fleet, list_fleet, resolve_fleet, history as fleet_history
from .network import mutate_node, list_nodes, resolve_node, history as node_history
from .service import mutate, resolve, list_products, business_date, version_rows
from .roads import Location, RoadError, validate_location


def router(auth, credentials, identify):
    api = APIRouter(prefix='/setup', tags=['Setup'])

    @api.post('/locations/validate')
    def location(body: Location, request: Request):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
        # The connection is closed before bounded network work starts.
        try:
            result = validate_location(body)
        except RoadError as exc:
            raise HTTPException(exc.status, {'code': exc.code, 'message': str(exc)}) from None
        # Provider work may outlive session/grant revocation; recheck before reply.
        with auth().engine.connect() as tx:
            require_setup(identify(tx, request))
        return result

    def change(request, kind, entity_id, operation, body):
        token, run, grant = credentials(request)
        try:
            if kind in ('vehicle', 'driver'):
                return mutate_fleet(auth(), token, run, grant, kind, entity_id, operation, body)
            if kind == 'node':
                return mutate_node(auth(), token, run, grant, entity_id, operation, body)
            return mutate(auth(), token, run, grant, kind, entity_id, operation, body)
        except RoadError as exc:
            raise HTTPException(exc.status, {'code': exc.code, 'message': str(exc)}) from None
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except IntegrityError:
            raise HTTPException(409, 'Setup conflicts with saved records; reload and review') from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.get('/products')
    def products(request: Request, as_of: date | None = None):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
            return list_products(tx, identity.run_id, as_of or business_date(tx, identity.run_id, auth().clock()))

    @api.get('/fleet/{kind}')
    def fleet_list(kind: str, request: Request, as_of: date | None = None):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
            if kind not in ('vehicle', 'driver'):
                raise AuthError(404, 'Unknown fleet resource')
            return list_fleet(tx, identity.run_id, kind, as_of or business_date(tx, identity.run_id, auth().clock()))

    @api.get('/fleet/{kind}/{entity_id}')
    def fleet_detail(kind: str, entity_id: str, request: Request, as_of: date | None = None):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
            if kind not in ('vehicle', 'driver'):
                raise AuthError(404, 'Unknown fleet resource')
            record = resolve_fleet(tx, identity.run_id, kind, entity_id,
                as_of or business_date(tx, identity.run_id, auth().clock()))
            if not record:
                raise AuthError(404, 'Fleet record not found at this date')
            return record

    @api.post('/vehicles/{entity_id}', status_code=201)
    def create_vehicle(entity_id: str, body: VehicleMutation, request: Request):
        return change(request, 'vehicle', entity_id, 'create', body)

    @api.put('/vehicles/{entity_id}')
    def replace_vehicle(entity_id: str, body: VehicleMutation, request: Request):
        return change(request, 'vehicle', entity_id, 'replace', body)

    @api.post('/drivers/{entity_id}', status_code=201)
    def create_driver(entity_id: str, body: DriverMutation, request: Request):
        return change(request, 'driver', entity_id, 'create', body)

    @api.put('/drivers/{entity_id}')
    def replace_driver(entity_id: str, body: DriverMutation, request: Request):
        return change(request, 'driver', entity_id, 'replace', body)

    @api.delete('/fleet/{kind}/{entity_id}')
    def delete_fleet(kind: str, entity_id: str, body: Mutation, request: Request):
        if kind not in ('vehicle', 'driver'):
            raise AuthError(404, 'Unknown fleet resource')
        return change(request, kind, entity_id, 'delete', body)

    @api.get('/network')
    def network(request: Request, as_of: date | None = None):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
            return list_nodes(tx, identity.run_id, as_of or business_date(tx, identity.run_id, auth().clock()))

    @api.get('/network/{node_id}')
    def node(node_id: str, request: Request, as_of: date | None = None):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
            record = resolve_node(tx, identity.run_id, node_id, as_of or business_date(tx, identity.run_id, auth().clock()))
            if not record:
                raise AuthError(404, 'Node not found at this date')
            return record

    @api.post('/network/{node_id}', status_code=201)
    def create_node(node_id: str, body: NodeMutation, request: Request):
        return change(request, 'node', node_id, 'create', body)

    @api.put('/network/{node_id}')
    def replace_node(node_id: str, body: NodeMutation, request: Request):
        return change(request, 'node', node_id, 'replace', body)

    @api.delete('/network/{node_id}')
    def delete_node(node_id: str, body: Mutation, request: Request):
        return change(request, 'node', node_id, 'delete', body)

    @api.get('/settings')
    def settings(request: Request, as_of: date | None = None):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
            return resolve(tx, identity.run_id, 'settings', 'registry',
                           as_of or business_date(tx, identity.run_id, auth().clock()))

    @api.get('/products/{product_id}')
    def product(product_id: str, request: Request, as_of: date | None = None):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
            record = resolve(tx, identity.run_id, 'product', product_id,
                             as_of or business_date(tx, identity.run_id, auth().clock()))
            if record is None:
                raise AuthError(404, 'Product not found at this delivery date')
            return record

    @api.get('/history/{kind}/{entity_id}')
    def history(kind: str, entity_id: str, request: Request):
        with auth().engine.connect() as tx:
            identity = identify(tx, request)
            require_setup(identity)
            if kind in ('vehicle', 'driver'):
                return [dict(r) for r in fleet_history(tx, identity.run_id, kind, entity_id)]
            if kind == 'node':
                return [dict(r) for r in node_history(tx, identity.run_id, entity_id)]
            if kind not in ('product', 'settings') or (kind == 'settings' and entity_id != 'registry'):
                raise AuthError(404, 'Not found')
            return [dict(r) for r in version_rows(tx, identity.run_id, kind, entity_id)]

    @api.post('/products/{product_id}', status_code=201)
    def create_product(product_id: str, body: ProductMutation, request: Request):
        return change(request, 'product', product_id, 'create', body)

    @api.put('/products/{product_id}')
    def replace_product(product_id: str, body: ProductMutation, request: Request):
        return change(request, 'product', product_id, 'replace', body)

    @api.delete('/products/{product_id}')
    def delete_product(product_id: str, body: Mutation, request: Request):
        return change(request, 'product', product_id, 'delete', body)

    @api.put('/settings')
    def replace_settings(body: SettingsMutation, request: Request):
        return change(request, 'settings', 'registry', 'replace', body)

    return api
