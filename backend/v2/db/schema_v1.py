"""Frozen schema snapshot for Alembic revisions 0001/0002.

Do not edit released migrations to add future fields: add a new revision and
current metadata snapshot. JSON holds versioned configuration/evidence, not world state.
"""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, CheckConstraint, Column, Date, ForeignKey,
                        ForeignKeyConstraint, Index, JSON, MetaData, String,
                        Table, Text, UniqueConstraint)

from .sqltypes import ExactInteger as Int, UTCInstant

metadata = MetaData(naming_convention={
    "ix": "ix_%(table_name)s_%(column_0_name)s", "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})


def now():
    return datetime.now(timezone.utc)


def col(name, type_=String(128), **kw):
    if type_ is Boolean:
        type_ = Boolean(create_constraint=True, name=f"{name}_boolean")
    return Column(name, type_, nullable=False, **kw)


def optional(name, type_=String(128)):
    return Column(name, type_, nullable=True)


def check(sql, name):
    return CheckConstraint(sql, name=name)


def nonnegative(*names):
    return [check(f"{n} >= 0", f"{n}_nonnegative") for n in names]


def ref(target, field, remote="id"):
    return ForeignKeyConstraint(["run_id", field], [f"{target}.run_id", f"{target}.{remote}"])


def scoped(name, *fields, **kw):
    table = Table(name, metadata,
        Column("run_id", String(128), ForeignKey("runs.id"), primary_key=True),
        col("id", primary_key=True),
        col("revision", Int(), default=0, server_default="0"),
        col("created_at", UTCInstant(), default=now), *fields,
        check("revision >= 0", "revision_nonnegative"), **kw)
    if "delivery_date" in table.c and name != "days":
        table.append_constraint(ForeignKeyConstraint(
            ["run_id", "delivery_date"], ["days.run_id", "days.delivery_date"]))
    return table


runs = Table("runs", metadata, col("id", primary_key=True), col("name"),
    col("timezone", default="Asia/Kolkata", server_default="Asia/Kolkata"),
    col("seed", Int()), col("settings", JSON), col("created_at", UTCInstant(), default=now),
    optional("parent_run_id"), ForeignKeyConstraint(["parent_run_id"], ["runs.id"]),
    check("timezone = 'Asia/Kolkata'", "business_zone"))
users = Table("users", metadata, col("id", primary_key=True), col("phone", unique=True),
    col("pin_hash", Text), col("active", Boolean, default=True),
    col("session_version", Int(), default=0, server_default="0"),
    col("created_at", UTCInstant(), default=now), *nonnegative("session_version"))
days = scoped("days", col("delivery_date", Date), col("state", default="open"),
    UniqueConstraint("run_id", "delivery_date"),
    check("state IN ('open','closing','closed','closed_with_exceptions')", "state"))
clocks = scoped("clocks", col("sim_time", UTCInstant()), col("mode", default="paused"),
    col("speed", Int(), default=1), UniqueConstraint("run_id"),
    check("mode IN ('paused','playing','fast_forward')", "mode"),
    check("speed IN (1,5,15,30,60,120)", "speed"))
commands = scoped("commands", col("actor_id"), col("kind"), col("payload_hash", String(64)),
    col("result", JSON), col("sim_time", UTCInstant()), col("completed", Boolean, default=False, server_default="0"),
    ForeignKeyConstraint(["actor_id"], ["users.id"]))
audit = scoped("audit", col("command_id"), col("actor_id"), col("action"), col("details", JSON),
    optional("reason", Text), ref("commands", "command_id"),
    ForeignKeyConstraint(["actor_id"], ["users.id"]))
outbox = scoped("outbox", col("command_id"), col("topic"), col("recipient"), col("payload", JSON),
    optional("published_at", UTCInstant()), ref("commands", "command_id"),
    UniqueConstraint("run_id", "command_id", "topic", "recipient"))
Index("ix_outbox_pending", outbox.c.published_at, outbox.c.created_at)
events = scoped("scheduled_events", col("delivery_date", Date), col("event_type"), col("scope"),
    col("command_id"), col("input_hash", String(64)), col("result", JSON), ref("commands", "command_id"),
    UniqueConstraint("run_id", "delivery_date", "event_type", "scope"))
leases = Table("scheduler_leases", metadata, col("name", primary_key=True), col("owner"),
    col("generation", Int()), col("expires_at", UTCInstant()), *nonnegative("generation"))

FOUNDATION = tuple(metadata.tables)

nodes = scoped("nodes", col("kind"), col("name"), optional("parent_id"), optional("parent_kind"),
    col("lat_e7", Int()), col("lon_e7", Int()), optional("road_lat_e7", Int()),
    optional("road_lon_e7", Int()), optional("snap_distance_m", Int()),
    col("priority", default="Normal"), col("active", Boolean, default=True),
    col("attributes", JSON, default=dict), UniqueConstraint("run_id", "name"),
    UniqueConstraint("run_id", "id", "kind"),
    ForeignKeyConstraint(["run_id", "parent_id", "parent_kind"], ["nodes.run_id", "nodes.id", "nodes.kind"]),
    check("(kind='plant' AND parent_id IS NULL AND parent_kind IS NULL) OR "
          "(kind='hub' AND parent_id IS NOT NULL AND parent_kind IS NOT NULL AND parent_kind='plant') OR "
          "(kind='booth' AND parent_id IS NOT NULL AND parent_kind IS NOT NULL AND parent_kind='hub')", "tree"),
    check("lat_e7 BETWEEN -900000000 AND 900000000 AND lon_e7 BETWEEN -1800000000 AND 1800000000", "coordinates"),
    check("(road_lat_e7 IS NULL AND road_lon_e7 IS NULL AND snap_distance_m IS NULL) OR "
          "(road_lat_e7 IS NOT NULL AND road_lon_e7 IS NOT NULL AND snap_distance_m IS NOT NULL AND snap_distance_m BETWEEN 0 AND 300 "
          "AND road_lat_e7 BETWEEN -900000000 AND 900000000 AND road_lon_e7 BETWEEN -1800000000 AND 1800000000)", "road_coordinates"),
    check("priority IN ('Normal','High','Critical')", "priority"))
Index("uq_one_plant_per_run", nodes.c.run_id, unique=True,
      sqlite_where=nodes.c.kind == "plant", postgresql_where=nodes.c.kind == "plant")
products = scoped("products", col("name"), col("dimension"), col("pack_size", Int()),
    col("units_per_container", Int()), col("full_container_g", Int()), col("container_type"),
    col("returnable", Boolean), col("lane"), col("shelf_life_minutes", Int()),
    col("active", Boolean, default=True),
    check("dimension IN ('ml','g')", "dimension"),
    check("lane IN ('ambient','chilled','frozen')", "lane"),
    check("pack_size>0 AND units_per_container>0 AND full_container_g>0 AND shelf_life_minutes>0", "positive_packaging"))
drivers = scoped("drivers", col("user_id"), col("home_id"), col("name"), col("licence"),
    col("active", Boolean, default=True), ref("nodes", "home_id"),
    ForeignKeyConstraint(["user_id"], ["users.id"]), UniqueConstraint("run_id", "user_id"))
vehicles = scoped("vehicles", col("home_id"), col("level"), col("name"),
    col("capacity_slots", Int()), col("capacity_g", Int()), col("lane_configurations", JSON),
    col("status", default="active"), col("colour"), col("attributes", JSON, default=dict),
    ForeignKeyConstraint(["run_id", "home_id", "level"], ["nodes.run_id", "nodes.id", "nodes.kind"]),
    check("level IN ('plant','hub')", "level"),
    check("capacity_slots>0 AND capacity_g>0", "positive_capacity"),
    check("status IN ('active','maintenance','inactive')", "status"))
grants = scoped("role_grants", col("user_id"), col("role"), optional("hub_id"),
    optional("booth_id"), optional("driver_id"),
    ForeignKeyConstraint(["user_id"], ["users.id"]), ref("nodes", "hub_id"), ref("nodes", "booth_id"), ref("drivers", "driver_id"),
    check("(role IN ('admin','plant_manager') AND hub_id IS NULL AND booth_id IS NULL AND driver_id IS NULL) OR "
          "(role IN ('hub_manager','hub_ordering') AND hub_id IS NOT NULL AND booth_id IS NULL AND driver_id IS NULL) OR "
          "(role='booth' AND hub_id IS NOT NULL AND booth_id IS NOT NULL AND driver_id IS NULL) OR "
          "(role='driver' AND driver_id IS NOT NULL AND hub_id IS NULL AND booth_id IS NULL)", "scope"))
sessions = Table("auth_sessions", metadata, col("id", primary_key=True),
    Column("user_id", String(128), ForeignKey("users.id"), nullable=False),
    col("token_hash", String(64), unique=True), col("expires_at", UTCInstant()),
    optional("revoked_at", UTCInstant()))
settings = scoped("settings", col("key"), col("value", JSON), col("effective_date", Date),
    UniqueConstraint("run_id", "key", "effective_date", "revision"))
model_artifacts = scoped("model_artifacts", col("sha256", String(64)), col("path", Text),
    col("manifest", JSON), col("status"))
forecasts = scoped("forecasts", col("issuance_id"), col("booth_id"), col("sku_id"),
    col("target_date", Date), col("issued_at", UTCInstant()), col("model_id"),
    col("p10_milliunits", Int()), col("p50_milliunits", Int()), col("p90_milliunits", Int()),
    col("reason", Text), col("provenance", JSON), ref("nodes", "booth_id"),
    ref("products", "sku_id"), ref("model_artifacts", "model_id"),
    UniqueConstraint("run_id", "issuance_id", "booth_id", "sku_id", "target_date"),
    check("0<=p10_milliunits AND p10_milliunits<=p50_milliunits AND p50_milliunits<=p90_milliunits", "quantiles"))
observations = scoped("demand_observations", col("booth_id"), col("sku_id"), col("delivery_date", Date),
    col("kind"), col("units", Int()), col("provenance", JSON), ref("nodes", "booth_id"), ref("products", "sku_id"),
    UniqueConstraint("run_id", "booth_id", "sku_id", "delivery_date", "kind"), *nonnegative("units"),
    check("kind IN ('synthetic_demand','observed_sales','accepted_delivery')", "kind"))
orders = scoped("orders", col("delivery_date", Date), col("level"), col("source_id"), col("receiver_id"),
    col("state"), col("cutoff_at", UTCInstant()), ref("nodes", "source_id"), ref("nodes", "receiver_id"),
    UniqueConstraint("run_id", "delivery_date", "level", "receiver_id"),
    check("level IN ('plant','hub')", "level"),
    check("state IN ('draft','confirmed','locked','allocated','dispatched','delivered','exception','closed')", "state"))
order_lines = scoped("order_lines", col("order_id"), col("sku_id"), optional("forecast_id"),
    col("suggested_units", Int()), col("effective_units", Int()), optional("requested_units", Int()),
    col("allocated_units", Int(), default=0), col("accepted_units", Int(), default=0),
    col("snapshot", JSON), ref("orders", "order_id"), ref("products", "sku_id"), ref("forecasts", "forecast_id"),
    UniqueConstraint("run_id", "order_id", "sku_id"),
    *nonnegative("suggested_units", "effective_units", "requested_units", "allocated_units", "accepted_units"))
approvals = scoped("approvals", col("line_id"), col("proposal_revision", Int()), col("requested_by"),
    optional("decided_by"), col("state"), col("reason", Text), optional("decided_at", UTCInstant()),
    ref("order_lines", "line_id"), ForeignKeyConstraint(["requested_by"], ["users.id"]),
    ForeignKeyConstraint(["decided_by"], ["users.id"]),
    UniqueConstraint("run_id", "line_id", "proposal_revision"), *nonnegative("proposal_revision"),
    check("state IN ('pending','approved','rejected','expired','superseded')", "state"))
allocations = scoped("allocations", col("line_id"), col("ordered_units", Int()), col("allocated_units", Int()),
    col("policy", JSON), ref("order_lines", "line_id"),
    check("0<=allocated_units AND allocated_units<=ordered_units", "quantity_bounds"))
locations = scoped("locations", col("kind"), optional("node_id"), optional("vehicle_id"),
    ref("nodes", "node_id"), ref("vehicles", "vehicle_id"),
    check("(kind='node' AND node_id IS NOT NULL AND vehicle_id IS NULL) OR "
          "(kind='vehicle' AND vehicle_id IS NOT NULL AND node_id IS NULL) OR "
          "(kind IN ('external','quarantine','provisional') AND node_id IS NULL AND vehicle_id IS NULL)", "custody"))
lots = scoped("inventory_lots", col("sku_id"), col("batch"), col("expires_at", UTCInstant()),
    col("status"), ref("products", "sku_id"), check("status IN ('usable','quarantine','expired')", "status"))
balances = scoped("inventory_balances", col("lot_id"), col("location_id"), col("units", Int()),
    col("reserved_units", Int(), default=0, server_default="0"),
    ref("inventory_lots", "lot_id"), ref("locations", "location_id"),
    UniqueConstraint("run_id", "lot_id", "location_id"),
    check("units>=0 AND reserved_units>=0 AND reserved_units<=units", "stock_bounds"))
reservations = scoped("reservations", col("balance_id"), col("command_id"), col("units", Int()),
    col("state", default="active"), ref("inventory_balances", "balance_id"), ref("commands", "command_id"),
    check("units>0", "positive_units"), check("state IN ('active','released','consumed')", "state"))
movements = scoped("stock_movements", col("lot_id"), col("from_location_id"), col("to_location_id"),
    col("units", Int()), col("command_id"), col("reason", Text),
    ref("inventory_lots", "lot_id"), ref("locations", "from_location_id"), ref("locations", "to_location_id"),
    ref("commands", "command_id"), check("units>0 AND from_location_id<>to_location_id", "transfer"))
plans = scoped("plans", col("delivery_date", Date), col("source_id"), col("input_hash", String(64)),
    col("state"), col("snapshot", JSON), ref("nodes", "source_id"))
trips = scoped("trips", col("plan_id"), col("vehicle_id"), col("driver_id"), col("state"),
    col("departure_at", UTCInstant()), col("return_at", UTCInstant()), col("distance_m", Int()),
    col("manifest_snapshot", JSON), ref("plans", "plan_id"), ref("vehicles", "vehicle_id"), ref("drivers", "driver_id"),
    check("return_at>departure_at", "trip_interval"), *nonnegative("distance_m"),
    check("state IN ('planned','loading','dispatched','blocked','returning','completed','cancelled')", "state"))
stops = scoped("stops", col("trip_id"), col("receiver_id"), col("sequence", Int()), col("state"),
    optional("arrival_at", UTCInstant()), optional("completed_at", UTCInstant()),
    col("deadline_at", UTCInstant()), ref("trips", "trip_id"), ref("nodes", "receiver_id"),
    UniqueConstraint("run_id", "trip_id", "sequence"), *nonnegative("sequence"))
manifest_lines = scoped("manifest_lines", col("stop_id"), col("allocation_id"), col("sku_id"),
    col("lot_id"), col("units", Int()), col("slots", Int()), col("planning_g", Int()),
    col("packaging_snapshot", JSON), ref("stops", "stop_id"), ref("allocations", "allocation_id"),
    ref("products", "sku_id"), ref("inventory_lots", "lot_id"),
    check("units>0 AND slots>0 AND planning_g>0", "positive_load"))
routes = scoped("routes", col("geometry_hash", String(64)), col("geometry", JSON), col("approximate", Boolean),
    UniqueConstraint("run_id", "geometry_hash"))
motion = scoped("motion_checkpoints", col("trip_id"), col("route_id"), col("distance_mm", Int()),
    col("sim_time", UTCInstant()), ref("trips", "trip_id"), ref("routes", "route_id"),
    UniqueConstraint("run_id", "trip_id"), *nonnegative("distance_mm"))
disruptions = scoped("disruptions", col("kind"), col("geometry", JSON), col("sim_time", UTCInstant()),
    optional("cleared_at", UTCInstant()), check("kind IN ('rain','traffic','roadblock','area_closure')", "kind"))
deliveries = scoped("delivery_attempts", col("stop_id"), col("command_id"), col("state"),
    col("manifest_revision", Int()), col("evidence", JSON), ref("stops", "stop_id"), ref("commands", "command_id"),
    UniqueConstraint("run_id", "command_id"), *nonnegative("manifest_revision"))
delivery_lines = scoped("delivery_lines", col("delivery_id"), col("manifest_line_id"),
    col("offered_units", Int()), col("accepted_units", Int()), col("refused_units", Int()), col("damaged_units", Int()),
    ref("delivery_attempts", "delivery_id"), ref("manifest_lines", "manifest_line_id"),
    UniqueConstraint("run_id", "delivery_id", "manifest_line_id"),
    *nonnegative("offered_units", "accepted_units", "refused_units", "damaged_units"),
    check("offered_units=accepted_units+refused_units+damaged_units", "delivery_conservation"))
confirmations = scoped("pod_confirmations", col("delivery_id"), col("receiver_user_id"),
    col("challenge_hash", String(128)), col("expires_at", UTCInstant()), col("attempts", Int(), default=0),
    optional("confirmed_at", UTCInstant()), ref("delivery_attempts", "delivery_id"),
    ForeignKeyConstraint(["receiver_user_id"], ["users.id"]),
    UniqueConstraint("run_id", "delivery_id"), *nonnegative("attempts"))
crates = scoped("crate_movements", col("container_type"), col("from_location_id"), col("to_location_id"),
    col("count", Int()), col("command_id"), col("reason", Text), ref("locations", "from_location_id"),
    ref("locations", "to_location_id"), ref("commands", "command_id"),
    check("count>0 AND from_location_id<>to_location_id", "crate_transfer"))
loans = scoped("loans", col("booth_id"), col("delivery_date", Date), col("home_hub_id"),
    col("fulfillment_hub_id"), col("state"), col("requested_by"), optional("accepted_by"),
    ref("nodes", "booth_id"), ref("nodes", "home_hub_id"), ref("nodes", "fulfillment_hub_id"),
    ForeignKeyConstraint(["requested_by"], ["users.id"]), ForeignKeyConstraint(["accepted_by"], ["users.id"]))
Index("uq_accepted_loan", loans.c.run_id, loans.c.booth_id, loans.c.delivery_date, unique=True,
      sqlite_where=loans.c.state == "accepted", postgresql_where=loans.c.state == "accepted")
urgent = scoped("urgent_requests", col("booth_id"), col("sku_id"), col("delivery_date", Date),
    col("requested_units", Int()), col("accepted_units", Int()), col("state"), col("decision", JSON),
    ref("nodes", "booth_id"), ref("products", "sku_id"),
    check("requested_units>0 AND accepted_units>=0 AND accepted_units<=requested_units", "urgent_bounds"))
decisions = scoped("decisions", optional("trip_id"), col("command_id"), col("kind"),
    col("options", JSON), col("chosen", JSON), col("reason", Text), col("sim_time", UTCInstant()),
    ref("trips", "trip_id"), ref("commands", "command_id"))
threads = scoped("threads", col("kind"), optional("node_id"), optional("trip_id"),
    ref("nodes", "node_id"), ref("trips", "trip_id"))
participants = scoped("thread_participants", col("thread_id"), col("user_id"), ref("threads", "thread_id"),
    ForeignKeyConstraint(["user_id"], ["users.id"]), UniqueConstraint("run_id", "thread_id", "user_id"))
messages = scoped("messages", col("thread_id"), col("sender_id"), col("text", Text),
    col("requires_ack", Boolean), optional("ack_deadline", UTCInstant()), ref("threads", "thread_id"),
    ForeignKeyConstraint(["sender_id"], ["users.id"]))
acks = scoped("message_acks", col("message_id"), col("user_id"), col("acknowledged_at", UTCInstant()),
    ref("messages", "message_id"), ForeignKeyConstraint(["user_id"], ["users.id"]),
    UniqueConstraint("run_id", "message_id", "user_id"))
notifications = scoped("notifications", col("outbox_id"), col("user_id"), col("text", Text),
    optional("read_at", UTCInstant()), ref("outbox", "outbox_id"),
    ForeignKeyConstraint(["user_id"], ["users.id"]), UniqueConstraint("run_id", "outbox_id", "user_id"))
closes = scoped("day_closes", col("delivery_date", Date), col("scope"), col("report", JSON),
    col("command_id"), ref("commands", "command_id"), UniqueConstraint("run_id", "delivery_date", "scope"))
adjustments = scoped("close_adjustments", col("close_id"), col("command_id"), col("reason", Text),
    col("adjustment", JSON), ref("day_closes", "close_id"), ref("commands", "command_id"))

BUSINESS = tuple(name for name in metadata.tables if name not in FOUNDATION)

# SQLite affinity otherwise permits fractional numbers in integer ledger columns.
# PostgreSQL BIGINT itself enforces integer storage. ORM bind checks apply to both.
for table in metadata.tables.values():
    for column in table.columns:
        if isinstance(column.type, (Int, UTCInstant)):
            table.append_constraint(check(
                f"{column.name} IS NULL OR typeof({column.name}) = 'integer'",
                f"{column.name}_integer_storage").ddl_if(dialect="sqlite"))
