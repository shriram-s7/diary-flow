"""V2 subapplication: fail closed unless explicitly configured, never expose v1 world."""
import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from v2.auth.service import AuthService, AuthError, node_filter, trip_filter
from v2.auth.tables import demo_users
from v2.db import schema as s
from v2.db.engine import open_database, unit_of_work
from v2.catalog.api import router as setup_router
from v2.catalog.service import list_products, business_date
from v2.catalog.network import scoped_nodes


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(max_length=30)
    pin: str = Field(min_length=6,max_length=12,pattern=r"^[0-9]+$")


class DemoLogin(BaseModel):
    user_id: str = Field(min_length=1,max_length=128)


def create_app(service=None):
    app = FastAPI(title="Allocra v2")
    app.state.auth = service

    def auth():
        if app.state.auth is None:
            raise HTTPException(503,"V2 needs database setup and a signing key. See docs/v2/AUTH.md.")
        return app.state.auth

    def credentials(request):
        header = request.headers.get("authorization","")
        if not header.startswith("Bearer "): raise AuthError()
        return header[7:],request.headers.get("x-run-id",""),request.headers.get("x-grant-id","")

    def identify(tx, request):
        return auth().identity(tx,*credentials(request))

    @app.exception_handler(AuthError)
    async def auth_error(request, exc):
        return JSONResponse({"detail":exc.detail},status_code=exc.status,headers={"Cache-Control":"no-store"})

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        return response

    @app.get("/status")
    def status():
        return {"configured":app.state.auth is not None,"demo":bool(app.state.auth and app.state.auth.demo),
                "checkpoint":"P04 partial: product/settings/network/fleet/driver APIs implemented; user management and setup editors remain pending"}

    @app.post("/auth/login")
    def login(body:Login,request:Request):
        try: return auth().login(body.phone,body.pin,request.client.host if request.client else "unknown")
        except ValueError: raise HTTPException(422,"Enter a valid international phone number and PIN.") from None

    @app.get("/auth/demo-users")
    def demos():
        service=auth()
        if not service.demo: raise HTTPException(404,"Not found")
        with service.engine.connect() as tx:
            return [dict(r) for r in tx.execute(select(demo_users.c.user_id,demo_users.c.label).join(
                s.users,s.users.c.id==demo_users.c.user_id).where(s.users.c.active.is_(True))).mappings()]

    @app.post("/auth/demo-login")
    def demo_login(body:DemoLogin,request:Request):
        service=auth()
        if not service.demo: raise HTTPException(404,"Not found")
        service.throttle("demo:"+body.user_id,request.client.host if request.client else "unknown")
        with service.engine.connect() as tx:
            if not tx.execute(select(demo_users.c.user_id).where(demo_users.c.user_id==body.user_id)).first():
                raise HTTPException(404,"Not found")
        return service.issue(body.user_id)

    @app.get("/me")
    def me(request:Request):
        with auth().engine.connect() as tx:
            identity=identify(tx,request)
            return {"user_id":identity.user_id,"real_actor_id":identity.real_actor_id,
                    "run_id":identity.run_id,"scope":identity.scope.model_dump(mode="json")}

    @app.post("/auth/logout")
    def logout(request:Request):
        with unit_of_work(auth().engine) as tx:
            identity=identify(tx,request)
            auth().logout(tx,identity)
        return {"signed_out":True}

    @app.get("/network")
    def network(request:Request):
        with auth().engine.connect() as tx:
            identity=identify(tx,request)
            return [{**r['data'], 'id': r['id'], 'revision': r['revision']} for r in scoped_nodes(
                tx, identity, business_date(tx, identity.run_id, auth().clock()))]

    @app.get("/network/{node_id}")
    def node(node_id:str,request:Request):
        with auth().engine.connect() as tx:
            identity=identify(tx,request)
            row=next((r for r in scoped_nodes(tx, identity, business_date(tx, identity.run_id, auth().clock()))
                      if r['id'] == node_id), None)
            if not row: raise HTTPException(404,"Not found")
            return {**row['data'], 'id': row['id'], 'revision': row['revision']}

    @app.get("/trips")
    def trips(request:Request):
        with auth().engine.connect() as tx:
            identity=identify(tx,request)
            return [dict(r) for r in tx.execute(select(s.trips.c.id,s.trips.c.state,s.trips.c.vehicle_id,
                s.trips.c.driver_id).where(trip_filter(identity))).mappings()]

    @app.get("/products")
    def products(request:Request):
        with auth().engine.connect() as tx:
            identity=identify(tx,request)
            if identity.scope.role.value not in ("admin","plant_manager","hub_manager"):
                raise AuthError(403,"This view is available to managers only.")
            return [{**r['data'], 'id': r['id'], 'revision': r['revision'],
                     'effective_date': r['effective_date']} for r in list_products(
                         tx, identity.run_id, business_date(tx, identity.run_id, auth().clock()))
                    if r['data']['active']]

    @app.post("/auth/view-as")
    def view_as(body:DemoLogin,request:Request):
        service=auth()
        if not service.demo: raise HTTPException(404,"Not found")
        with service.engine.connect() as tx:
            identity=identify(tx,request)
            if identity.scope.role.value!="admin" or identity.real_actor_id!=identity.user_id:
                raise AuthError(403,"Only the demo administrator can use View as.")
            target=tx.execute(select(demo_users.c.user_id).join(s.grants,s.grants.c.user_id==demo_users.c.user_id).where(
                demo_users.c.user_id==body.user_id,s.grants.c.run_id==identity.run_id)).first()
            if not target: raise HTTPException(404,"Not found")
        return service.issue(body.user_id,real_actor=identity.user_id)

    @app.websocket("/stream")
    async def stream(ws:WebSocket):
        # Token goes in first frame, never a URL/access log. Revalidate each snapshot.
        await ws.accept()
        try:
            hello=await asyncio.wait_for(ws.receive_json(),timeout=5)
            if not isinstance(hello,dict): raise AuthError()
            token,run,grant=(hello.get(k,"") for k in ("token","run_id","grant_id"))
            def snapshot():
                with auth().engine.connect() as tx:
                    identity=auth().identity(tx,token,run,grant)
                    records=[dict(r) for r in tx.execute(select(s.trips.c.id,s.trips.c.state).where(trip_filter(identity))).mappings()]
                    return {"type":"scoped_snapshot","trips":records,"scope":identity.scope.model_dump(mode="json")}
            while True:
                await ws.send_json(await run_in_threadpool(snapshot))
                await asyncio.sleep(1)
        except (AuthError, HTTPException, ValueError, TypeError, asyncio.TimeoutError):
            await ws.close(code=4401)
        except WebSocketDisconnect:
            pass
        return

    app.include_router(setup_router(auth, credentials, identify))
    return app


def configured_app():
    key=os.environ.get("ALLOCRA_SIGNING_KEY")
    key_file=os.environ.get("ALLOCRA_SIGNING_KEY_FILE")
    if not key and key_file:
        key=Path(key_file).read_text(encoding="utf-8").strip()
    if not key: return create_app()
    path=os.environ.get("ALLOCRA_DB",str(Path(__file__).resolve().parents[2]/"data"/"allocra-v2.sqlite3"))
    # parents[2] is repository root for backend/v2/app.py.
    engine=open_database(path)
    return create_app(AuthService(engine,key,demo=os.environ.get("ALLOCRA_DEMO")=="1"))
