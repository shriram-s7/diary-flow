"""Wall-clock auth. Scope is reloaded on every request, never trusted from JWT alone."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from uuid import uuid4

import jwt
from sqlalchemy import select, update, delete, and_, or_

from v2.db import schema as s
from v2.db.engine import unit_of_work
from v2.domain.types import Scope
from .tables import attempts, auth_audit, demo_users


class AuthError(Exception):
    def __init__(self, status=401, detail="Session expired or invalid. Sign in again."):
        self.status, self.detail = status, detail


def phone_number(value):
    value = value.strip().replace(" ", "").replace("-", "")
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
        raise ValueError("Use an international phone number, such as +919876543210")
    return value


def hash_pin(pin):
    if not re.fullmatch(r"[0-9]{6,12}", pin):
        raise ValueError("PIN must contain 6–12 digits")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 600000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_pin(pin, stored):
    try:
        algorithm, rounds, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256" or rounds != "600000":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Identity:
    user_id: str
    session_id: str
    run_id: str
    grant_id: str
    scope: Scope
    real_actor_id: str


class AuthService:
    def __init__(self, engine, secret, *, demo=False, clock=None):
        if len(secret.encode()) < 32:
            raise ValueError("Signing secret must contain at least 32 bytes")
        self.engine, self.secret, self.demo = engine, secret, demo
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.dummy_hash = hash_pin(secrets.choice("123456789") + "00000")

    def audit(self, tx, kind, actor=None, effective=None):
        tx.execute(auth_audit.insert().values(id=str(uuid4()), actor_id=actor,
            effective_actor_id=effective or actor, kind=kind, at=self.clock()))

    def throttle(self, phone, ip):
        # Count all attempts; cheap persistent gate runs before expensive hashing.
        with unit_of_work(self.engine) as tx:
            now = self.clock()
            for raw, limit in (("account:"+phone, 5), ("address:"+ip, 30)):
                key = hmac.new(self.secret.encode(), raw.encode(), "sha256").hexdigest()
                row = tx.execute(select(attempts).where(attempts.c.key == key)).mappings().first()
                if row and row["reset_at"] > now and row["count"] >= limit:
                    raise AuthError(429, "Too many sign-in attempts. Try again in 15 minutes.")
                if not row:
                    tx.execute(attempts.insert().values(key=key, count=1, reset_at=now+timedelta(minutes=15)))
                else:
                    tx.execute(update(attempts).where(attempts.c.key == key).values(
                        count=row["count"]+1 if row["reset_at"] > now else 1,
                        reset_at=row["reset_at"] if row["reset_at"] > now else now+timedelta(minutes=15)))

    def login(self, phone, pin, ip):
        phone = phone_number(phone)
        self.throttle(phone, ip)
        with self.engine.connect() as tx:
            user = tx.execute(select(s.users).where(s.users.c.phone == phone)).mappings().first()
        valid = verify_pin(pin, user["pin_hash"] if user else self.dummy_hash)
        if not user or not user["active"] or not valid:
            with unit_of_work(self.engine) as tx:
                self.audit(tx, "login_failed", user["id"] if user else None)
            raise AuthError(401, "Phone number or PIN is incorrect.")
        return self.issue(user["id"])

    def issue(self, user_id, *, real_actor=None):
        with unit_of_work(self.engine) as tx:
            user = tx.execute(select(s.users).where(s.users.c.id == user_id, s.users.c.active.is_(True))).mappings().first()
            if not user:
                raise AuthError()
            grants = list(tx.execute(select(s.grants).where(s.grants.c.user_id == user_id)).mappings())
            if not grants:
                raise AuthError(403, "No role has been assigned to this account.")
            now, sid = self.clock(), str(uuid4())
            claims = {"sub":user_id,"sid":sid,"ver":user["session_version"],"real":real_actor or user_id,
                "iss":"allocra","aud":"allocra-app","iat":int(now.timestamp()),
                "nbf":int(now.timestamp()),"exp":int((now+timedelta(hours=8)).timestamp()),
                "grants":[{"id":g["id"],"run_id":g["run_id"],"role":g["role"]} for g in grants]}
            token = jwt.encode(claims, self.secret, algorithm="HS256")
            tx.execute(s.sessions.insert().values(id=sid,user_id=user_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),expires_at=now+timedelta(hours=8)))
            self.audit(tx, "demo_view_as" if real_actor else "login", real_actor or user_id, user_id)
            return {"token":token,"expires_at":claims["exp"],"grants":claims["grants"]}

    def identity(self, tx, token, run_id, grant_id):
        try:
            claims = jwt.decode(token,self.secret,algorithms=["HS256"],issuer="allocra",audience="allocra-app",
                options={"require":["sub","sid","ver","real","iat","nbf","exp"],
                         "verify_exp":False,"verify_iat":False,"verify_nbf":False})
            now = self.clock()
            if not (claims["nbf"] <= now.timestamp() < claims["exp"]) or claims["iat"] > now.timestamp():
                raise ValueError()
            session = tx.execute(select(s.sessions).where(s.sessions.c.id==claims["sid"],
                s.sessions.c.user_id==claims["sub"],s.sessions.c.revoked_at.is_(None),
                s.sessions.c.expires_at>now)).mappings().first()
            user = tx.execute(select(s.users).where(s.users.c.id==claims["sub"],s.users.c.active.is_(True),
                s.users.c.session_version==claims["ver"])).mappings().first()
            if not user or not session or not hmac.compare_digest(session["token_hash"],hashlib.sha256(token.encode()).hexdigest()):
                raise ValueError()
            if claims["real"] != claims["sub"]:
                real = tx.execute(select(s.users.c.id).where(s.users.c.id==claims["real"],s.users.c.active.is_(True))).first()
                admin = tx.execute(select(s.grants.c.id).where(s.grants.c.user_id==claims["real"],
                    s.grants.c.run_id==run_id,s.grants.c.role=="admin")).first()
                if not self.demo or not real or not admin:
                    raise ValueError()
        except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
            raise AuthError() from None
        grant = tx.execute(select(s.grants).where(s.grants.c.run_id==run_id,s.grants.c.id==grant_id,
            s.grants.c.user_id==user["id"])).mappings().first()
        if not grant:
            raise AuthError(403,"This role or workspace is unavailable.")
        try:
            scope = Scope(role=grant["role"],hub_id=grant["hub_id"],booth_id=grant["booth_id"],driver_id=grant["driver_id"])
        except ValueError:
            raise AuthError(403,"Role configuration is invalid.") from None
        if scope.hub_id:
            from v2.catalog.network import resolve_node
            from v2.catalog.service import business_date
            hub = resolve_node(tx, run_id, scope.hub_id, business_date(tx, run_id, self.clock()))
            if not hub or hub['data']['kind'] != 'hub' or not hub['data']['active']:
                raise AuthError(403,"Hub access is unavailable.")
        if scope.booth_id:
            booth = resolve_node(tx, run_id, scope.booth_id, business_date(tx, run_id, self.clock()))
            if not booth or booth['data']['kind'] != 'booth' or not booth['data']['active'] or booth['data']['parent_id'] != scope.hub_id:
                raise AuthError(403,"Booth access is unavailable.")
        if scope.driver_id:
            from v2.catalog.fleet import resolve_fleet
            from v2.catalog.service import business_date
            driver = resolve_fleet(tx, run_id, 'driver', scope.driver_id, business_date(tx, run_id, self.clock()))
            if not driver or not driver['data']['active'] or driver['data']['user_id'] != user['id']:
                raise AuthError(403,"Driver access is unavailable.")
        return Identity(user["id"],session["id"],run_id,grant_id,scope,claims["real"])

    def logout(self, tx, identity):
        tx.execute(update(s.sessions).where(s.sessions.c.id==identity.session_id).values(revoked_at=self.clock()))
        self.audit(tx,"logout",identity.real_actor_id,identity.user_id)


def node_filter(identity):
    scope = identity.scope
    base = s.nodes.c.run_id==identity.run_id
    if scope.role.value in ("admin","plant_manager"): return base
    if scope.role.value in ("hub_manager","hub_ordering"):
        return and_(base,or_(s.nodes.c.id==scope.hub_id,s.nodes.c.parent_id==scope.hub_id))
    if scope.role.value=="booth": return and_(base,s.nodes.c.id==scope.booth_id)
    return and_(base,s.nodes.c.id=="__no_driver_network_access__")


def trip_filter(identity):
    scope = identity.scope
    base = s.trips.c.run_id==identity.run_id
    if scope.role.value in ("admin","plant_manager"): return base
    if scope.role.value in ("hub_manager","hub_ordering"):
        return and_(base,s.trips.c.plan_id.in_(select(s.plans.c.id).where(
            s.plans.c.run_id==identity.run_id,s.plans.c.source_id==scope.hub_id)))
    if scope.role.value=="driver": return and_(base,s.trips.c.driver_id==scope.driver_id)
    return and_(base,s.trips.c.id=="__no_booth_trip_access__")
