"""Explicit local prototype seed; not production account provisioning."""
from pathlib import Path
import argparse
import secrets
from sqlalchemy import select
from v2.db import schema as s
from v2.db.engine import open_database, unit_of_work
from v2.db.migrate import migrate
from .tables import demo_users
from .service import hash_pin


def seed(engine):
    # Compute hashes outside the write lock. PINs are random, never displayed/stored raw.
    roles=("admin","plant_manager","hub_manager","booth","hub_ordering","driver")
    hashed={r:hash_pin(str(secrets.randbelow(900000)+100000)) for r in roles}
    with unit_of_work(engine) as tx:
        if tx.execute(select(s.runs.c.id).where(s.runs.c.id=="auth-demo")).first(): return
        tx.execute(s.runs.insert().values(id="auth-demo",name="Prototype access demo",seed=1,settings={}))
        for nid,kind,name,parent,parent_kind in [("plant","plant","Madhavaram prototype",None,None),
                ("hub","hub","Chennai demo hub","plant","plant"),("booth","booth","Demo booth","hub","hub")]:
            tx.execute(s.nodes.insert().values(run_id="auth-demo",id=nid,kind=kind,name=name,
                parent_id=parent,parent_kind=parent_kind,lat_e7=131480000,lon_e7=802310000,
                attributes={"coordinates":"illustrative; not road verified"}))
        for i,role in enumerate(roles):
            uid="demo-"+role
            tx.execute(s.users.insert().values(id=uid,phone=f"+91000000000{i}",pin_hash=hashed[role],active=True))
            tx.execute(demo_users.insert().values(user_id=uid,label=role.replace("_"," ").title()))
            if role=="driver":
                tx.execute(s.drivers.insert().values(run_id="auth-demo",id="driver",user_id=uid,
                    home_id="hub",name="Demo driver",licence="Prototype only"))
            tx.execute(s.grants.insert().values(run_id="auth-demo",id=role,user_id=uid,role=role,
                hub_id="hub" if role in ("hub_manager","booth","hub_ordering") else None,
                booth_id="booth" if role=="booth" else None,driver_id="driver" if role=="driver" else None))
        tx.execute(s.products.insert().values(run_id="auth-demo",id="toned-500",name="Toned milk 500 ml",
            dimension="ml",pack_size=500,units_per_container=24,full_container_g=13000,
            container_type="crate",returnable=True,lane="chilled",shelf_life_minutes=1440))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--database",required=True)
    parser.add_argument("--key-file",required=True)
    args=parser.parse_args()
    key=Path(args.key_file)
    key.parent.mkdir(parents=True,exist_ok=True)
    if not key.exists():
        with key.open("x",encoding="utf-8") as out: out.write(secrets.token_urlsafe(48))
    engine=open_database(args.database)
    try:
        migrate(engine)
        seed(engine)
    finally: engine.dispose()
    print("Prototype identities ready. Key stored locally; enable ALLOCRA_DEMO=1 explicitly.")


if __name__=="__main__": main()
