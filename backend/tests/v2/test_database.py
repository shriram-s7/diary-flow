"""Real file-backed SQLite, concurrent writers, fresh processes and crash boundaries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys
from threading import Barrier

import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.schema import CreateIndex, CreateTable

from v2.db import schema as s
from v2.db.backup import inspect_backup, restore, snapshot
from v2.db.commands import Conflict, compare_and_swap, execute
from v2.db.engine import open_database, unit_of_work
from v2.db.lease import acquire, release, renew, require_lease
from v2.db.migrate import migrate
from v2.db.outbox import pending, publish_pending
from v2.db.reservations import reserve
from v2.domain.types import CommandContext

AT = datetime(2026, 10, 9, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 9)


def bootstrap(engine):
    with unit_of_work(engine) as tx:
        tx.execute(s.runs.insert().values(id="r", name="demo", seed=1, settings={}))
        tx.execute(s.users.insert().values(id="u", phone="demo", pin_hash="!disabled", active=False))
        tx.execute(s.days.insert().values(run_id="r", id="day", delivery_date=DAY, state="open"))


def seed_stock(engine):
    with unit_of_work(engine) as tx:
        for node in [dict(id="p", kind="plant", name="Plant"),
                     dict(id="h", kind="hub", name="Hub", parent_id="p", parent_kind="plant"),
                     dict(id="b", kind="booth", name="Booth", parent_id="h", parent_kind="hub")]:
            tx.execute(s.nodes.insert().values(run_id="r", lat_e7=130000000, lon_e7=800000000, **node))
        tx.execute(s.products.insert().values(run_id="r", id="milk", name="Milk", dimension="ml",
            pack_size=500, units_per_container=24, full_container_g=13000, container_type="crate",
            returnable=True, lane="chilled", shelf_life_minutes=1440))
        tx.execute(s.locations.insert().values(run_id="r", id="location", kind="node", node_id="h"))
        tx.execute(s.lots.insert().values(run_id="r", id="lot", sku_id="milk", batch="batch1",
                                          expires_at=AT + timedelta(days=1), status="usable"))
        tx.execute(s.balances.insert().values(run_id="r", id="balance", lot_id="lot",
                                             location_id="location", units=100, reserved_units=0))


@pytest.fixture
def db(tmp_path):
    engine = open_database(tmp_path / "domain.sqlite3")
    migrate(engine)
    bootstrap(engine)
    seed_stock(engine)
    yield engine
    engine.dispose()


def context(key="c", revision=0):
    return CommandContext(command_id=key, run_id="r", actor_id="u", expected_revision=revision,
                          delivery_date=DAY, occurred_at=AT, reason="test")


def reserve_and_notify(effects):
    result = reserve(effects, "balance", 60)
    effects.notify("reserved", "u", result)
    return result


def rows(engine, table):
    with engine.connect() as tx:
        return tx.execute(select(table)).mappings().all()


def test_migrations_from_empty_and_previous_revision_preserve_data(tmp_path):
    engine = open_database(tmp_path / "migration.sqlite3")
    try:
        migrate(engine, "0001_foundation")
        bootstrap(engine)
        assert "products" not in inspect(engine).get_table_names()
        migrate(engine)
        migrate(engine)  # Safe no-op, no startup reset.
        assert len(rows(engine, s.runs)) == 1
        assert set(s.metadata.tables).issubset(inspect(engine).get_table_names())
        assert inspect_backup(engine.url.database) == "0006_fleet"
    finally:
        engine.dispose()


def test_failed_migration_rolls_back_ddl_and_version(tmp_path, monkeypatch):
    engine = open_database(tmp_path / "migration-failure.sqlite3")
    migrate(engine, "0001_foundation")
    bootstrap(engine)
    original = s.products.create
    def fail(*args, **kwargs):
        raise RuntimeError("injected DDL failure")
    monkeypatch.setattr(s.products, "create", fail)
    with pytest.raises(RuntimeError):
        migrate(engine)
    assert "nodes" not in inspect(engine).get_table_names()
    assert inspect_backup(engine.url.database) == "0001_foundation"
    monkeypatch.setattr(s.products, "create", original)
    migrate(engine)
    assert len(rows(engine, s.runs)) == 1
    engine.dispose()


def test_connections_have_wal_foreign_keys_and_bounded_wait(db):
    with db.connect() as tx:
        assert tx.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert tx.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert tx.exec_driver_sql("PRAGMA busy_timeout").scalar() == 3000
        assert tx.exec_driver_sql("PRAGMA synchronous").scalar() == 2


def test_command_exact_retry_returns_result_once(db):
    first = execute(db, context(), "reserve", {"units": 60}, reserve_and_notify, sim_time=AT)
    second = execute(db, context(), "reserve", {"units": 60},
                     lambda _: pytest.fail("retry handler ran"), sim_time=AT)
    assert first == second
    assert rows(db, s.balances)[0]["reserved_units"] == 60
    for table in (s.reservations, s.commands, s.audit, s.outbox):
        assert len(rows(db, table)) == 1


def test_changed_payload_or_context_conflicts(db):
    execute(db, context(), "reserve", {"units": 60}, reserve_and_notify, sim_time=AT)
    for ctx, payload in [(context(), {"units": 1}), (context(revision=1), {"units": 60})]:
        with pytest.raises(Conflict):
            execute(db, ctx, "reserve", payload, reserve_and_notify, sim_time=AT)
    assert len(rows(db, s.commands)) == 1


@pytest.mark.parametrize("stage", ["after_reserve", "after_outbox", "bad_result"])
def test_failure_rolls_back_every_effect(db, stage):
    def fail(effects):
        reserve(effects, "balance", 60)
        if stage != "after_reserve":
            effects.notify("reserved", "u", {})
        if stage == "bad_result":
            return {"bad": float("nan")}
        raise RuntimeError("crash")
    with pytest.raises((RuntimeError, ValueError)):
        execute(db, context(), "reserve", {}, fail, sim_time=AT)
    assert rows(db, s.balances)[0]["reserved_units"] == 0
    for table in (s.reservations, s.commands, s.audit, s.outbox):
        assert rows(db, table) == []
    execute(db, context(), "reserve", {}, reserve_and_notify, sim_time=AT)
    assert rows(db, s.balances)[0]["reserved_units"] == 60


def test_two_simultaneous_duplicate_commands_execute_once(db):
    barrier = Barrier(2)
    def run(_):
        barrier.wait()
        return execute(db, context(), "reserve", {}, reserve_and_notify, sim_time=AT)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [1, 2]))
    assert results[0] == results[1]
    assert len(rows(db, s.reservations)) == 1


def test_concurrent_reservations_cannot_overdraw(db):
    barrier = Barrier(2)
    def run(key):
        barrier.wait()
        try:
            return execute(db, context(key), "reserve", {}, reserve_and_notify, sim_time=AT)
        except Conflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["c1", "c2"]))
    assert results.count("conflict") == 1
    assert rows(db, s.balances)[0]["reserved_units"] == 60
    assert len(rows(db, s.commands)) == 1
    # Even after observing the new revision, 60 more cannot fit in remaining40.
    with pytest.raises(Conflict):
        execute(db, context("c3", 1), "reserve", {}, reserve_and_notify, sim_time=AT)


def test_optimistic_revision_rejects_stale_mutation(db):
    def edit(effects):
        revision = compare_and_swap(effects.connection, s.nodes, "r", "b",
                                     effects.context.expected_revision, name="Renamed")
        return {"revision": revision}
    assert execute(db, context(), "rename", {}, edit, sim_time=AT) == {"revision": 1}
    with pytest.raises(Conflict):
        execute(db, context("another"), "rename", {}, edit, sim_time=AT)


@pytest.mark.parametrize("sql", [
    "UPDATE inventory_balances SET reserved_units=101 WHERE id='balance'",
    "UPDATE inventory_balances SET units=-1 WHERE id='balance'",
    "UPDATE inventory_balances SET units=10.5 WHERE id='balance'",
    "UPDATE nodes SET parent_id='b',parent_kind='hub' WHERE id='b'",
    "UPDATE nodes SET parent_id=NULL WHERE id='b'",
    "UPDATE nodes SET parent_kind=NULL WHERE id='b'",
    "UPDATE nodes SET road_lat_e7=1,road_lon_e7=1,snap_distance_m=NULL WHERE id='b'",
    "UPDATE products SET returnable=2 WHERE id='milk'",
    "UPDATE inventory_lots SET expires_at=1.5 WHERE id='lot'",
    "UPDATE products SET units_per_container=0 WHERE id='milk'",
])
def test_database_enforces_invariants_even_through_raw_sql(db, sql):
    with pytest.raises(IntegrityError):
        with unit_of_work(db) as tx:
            tx.execute(text(sql))


def test_cross_run_foreign_keys_and_unique_plant(db):
    with unit_of_work(db) as tx:
        tx.execute(s.runs.insert().values(id="other", name="other", seed=2, settings={}))
    for run_id, kind, parent_id, parent_kind in [("other", "hub", "p", "plant"), ("r", "plant", None, None)]:
        with pytest.raises(IntegrityError):
            with unit_of_work(db) as tx:
                tx.execute(s.nodes.insert().values(run_id=run_id, id="bad", kind=kind, name="Bad",
                    parent_id=parent_id, parent_kind=parent_kind, lat_e7=0, lon_e7=0))


def test_exact_integer_and_aware_timestamp_bindings(db):
    for value in [True, 1.1, "3", 2**64]:
        with pytest.raises(StatementError):
            with unit_of_work(db) as tx:
                tx.execute(update(s.balances).values(units=value))
    with pytest.raises(StatementError):
        with unit_of_work(db) as tx:
            tx.execute(update(s.lots).values(expires_at=datetime(2026, 1, 1)))
    assert rows(db, s.lots)[0]["expires_at"] == AT + timedelta(days=1)


def test_event_deduplicates_across_invocation_ids_and_checks_input(db):
    lease = acquire(db, "scheduler-a")
    first = execute(db, context(), "cutoff", {"policy": 1}, reserve_and_notify,
                    sim_time=AT, event_key=("cutoff", "h"), lease=lease)
    second = execute(db, context("new-id"), "cutoff", {"policy": 1},
        lambda _: pytest.fail("event ran twice"), sim_time=AT, event_key=("cutoff", "h"), lease=lease)
    assert first == second
    assert len(rows(db, s.events)) == len(rows(db, s.commands)) == 1
    with pytest.raises(Conflict):
        execute(db, context("third-id"), "cutoff", {"policy": 2}, reserve_and_notify,
                sim_time=AT, event_key=("cutoff", "h"), lease=lease)
    release(db, lease)


def test_event_rollback_also_rolls_back_journal(db):
    lease = acquire(db, "scheduler")
    def fail(effects):
        reserve_and_notify(effects)
        raise RuntimeError("event died")
    with pytest.raises(RuntimeError):
        execute(db, context(), "cutoff", {}, fail, sim_time=AT, event_key=("cutoff", "h"), lease=lease)
    assert not rows(db, s.events) and not rows(db, s.commands)
    assert rows(db, s.balances)[0]["reserved_units"] == 0


def test_scheduler_owner_and_stale_fencing(db, monkeypatch):
    import v2.db.lease as module
    wall = datetime.now(timezone.utc)
    monkeypatch.setattr(module, "now", lambda: wall)
    first = acquire(db, "one", 10)
    with pytest.raises(Conflict):
        acquire(db, "two", 10)
    renew(db, first, 10)
    wall += timedelta(seconds=11)
    second = acquire(db, "two", 10)
    assert second.generation > first.generation
    with pytest.raises(Conflict):
        with unit_of_work(db) as tx:
            require_lease(tx, first)
    with pytest.raises(Conflict):
        release(db, first)
    release(db, second)


def test_publication_is_outside_transaction_and_retries_same_event(db):
    execute(db, context(), "reserve", {}, reserve_and_notify, sim_time=AT)
    seen = []
    def publish(event):
        seen.append(event["id"])
        # Separate writer can acquire DB during publication (no lock held by dispatcher).
        with unit_of_work(db) as tx:
            tx.execute(update(s.runs).where(s.runs.c.id == "r").values(name="publisher"))
        if len(seen) == 1:
            raise RuntimeError("network result lost")
    with pytest.raises(RuntimeError):
        publish_pending(db, publish)
    assert len(pending(db)) == 1
    assert publish_pending(db, publish) == 1
    assert seen[0] == seen[1]  # Consumer must deduplicate this ID.
    assert pending(db) == []


def test_committed_audit_command_and_event_are_immutable(db):
    execute(db, context(), "reserve", {}, reserve_and_notify, sim_time=AT)
    for table in (s.commands, s.audit):
        for statement in (table.delete(), table.update().values(revision=5)):
            with pytest.raises(IntegrityError, match="immutable"):
                with unit_of_work(db) as tx:
                    tx.execute(statement)


def test_online_backup_contains_committed_wal_and_restores_new_path(db, tmp_path):
    execute(db, context(), "reserve", {}, reserve_and_notify, sim_time=AT)
    backup = snapshot(db.url.database, tmp_path / "backup.sqlite3")
    restored = restore(backup, tmp_path / "restored.sqlite3")
    other = open_database(restored)
    try:
        assert rows(other, s.commands)[0]["result"]["units"] == 60
        assert rows(other, s.balances)[0]["reserved_units"] == 60
        assert inspect_backup(restored) == "0006_fleet"
    finally:
        other.dispose()
    with pytest.raises(FileExistsError):
        restore(backup, restored)


def child(db, source):
    return subprocess.run([sys.executable, "-c", source, str(db.url.database)],
                          capture_output=True, text=True, timeout=30,
                          cwd=Path(__file__).resolve().parents[2])


def test_process_death_before_commit_is_recoverable(db):
    result = child(db, """
import os,sys
from datetime import datetime,timezone,date
from v2.db.engine import open_database
from v2.db.commands import execute
from v2.db.reservations import reserve
from v2.domain.types import CommandContext
engine=open_database(sys.argv[1])
at=datetime(2026,10,9,tzinfo=timezone.utc)
ctx=CommandContext(command_id='crash',run_id='r',actor_id='u',expected_revision=0,delivery_date=date(2026,10,9),occurred_at=at)
def fail(effects):
 reserve(effects,'balance',60)
 effects.notify('reserved','u',{})
 os._exit(17)
execute(engine,ctx,'reserve',{},fail,sim_time=at)
""")
    assert result.returncode == 17, result.stderr
    assert rows(db, s.balances)[0]["reserved_units"] == 0
    assert not rows(db, s.commands) and not rows(db, s.outbox)
    execute(db, context("crash"), "reserve", {}, reserve_and_notify, sim_time=AT)


def test_fresh_process_reads_commit_after_abrupt_exit(db):
    result = child(db, """
import os,sys
from datetime import datetime,timezone,date
from v2.db.engine import open_database
from v2.db.commands import execute
from v2.db.reservations import reserve
from v2.domain.types import CommandContext
engine=open_database(sys.argv[1]); at=datetime(2026,10,9,tzinfo=timezone.utc)
ctx=CommandContext(command_id='committed',run_id='r',actor_id='u',expected_revision=0,delivery_date=date(2026,10,9),occurred_at=at,reason='test')
def commit(effects):
 result=reserve(effects,'balance',60)
 effects.notify('reserved','u',result)
 return result
execute(engine,ctx,'reserve',{},commit,sim_time=at)
os._exit(18)
""")
    assert result.returncode == 18, result.stderr
    result = child(db, """
import sys,json
from sqlalchemy import select
from v2.db.engine import open_database
from v2.db.schema import commands
engine=open_database(sys.argv[1])
with engine.connect() as tx: print(json.dumps(tx.execute(select(commands.c.result)).scalar_one()))
engine.dispose()
""")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["units"] == 60
    replay = execute(db, context("committed"), "reserve", {},
                     lambda _: pytest.fail("already committed"), sim_time=AT)
    assert replay["units"] == 60 and len(pending(db)) == 1


def test_postgres_ddl_compiles_without_sqlite_type_checks():
    dialect = postgresql.dialect()
    for table in s.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "typeof(" not in ddl
        for index in table.indexes:
            str(CreateIndex(index).compile(dialect=dialect))
    # Compilation is a portability check, NOT a PostgreSQL runtime acceptance claim.


def test_schema_constraint_names_are_unique_per_table():
    for table in s.metadata.tables.values():
        names = [c.name for c in table.constraints if c.name is not None]
        assert len(names) == len(set(names)), (table.name, names)


def seed_trip(db):
    with unit_of_work(db) as tx:
        tx.execute(s.drivers.insert().values(run_id="r", id="d", user_id="u", home_id="h",
                                            name="Driver", licence="demo"))
        for vehicle_id in ("v", "v2"):
            tx.execute(s.vehicles.insert().values(run_id="r", id=vehicle_id, home_id="h", level="hub",
                name=vehicle_id, capacity_slots=100, capacity_g=1300000,
                lane_configurations=[["chilled"]], colour="#abcdef"))
        tx.execute(s.plans.insert().values(run_id="r", id="plan", delivery_date=DAY, source_id="h",
                                           input_hash="a" * 64, state="planned", snapshot={}))
        tx.execute(s.trips.insert().values(run_id="r", id="t", plan_id="plan", vehicle_id="v",
            driver_id="d", state="planned", departure_at=AT, return_at=AT+timedelta(hours=1),
            distance_m=10000, manifest_snapshot={}))


def test_trip_overlap_checks_insert_update_driver_and_boundary(db):
    seed_trip(db)
    base = dict(run_id="r", plan_id="plan", vehicle_id="v2", driver_id="d", state="planned",
                distance_m=10000, manifest_snapshot={})
    with pytest.raises(IntegrityError, match="overlap"):
        with unit_of_work(db) as tx:
            tx.execute(s.trips.insert().values(id="bad", departure_at=AT+timedelta(minutes=30),
                                               return_at=AT+timedelta(hours=2), **base))
    with unit_of_work(db) as tx:
        tx.execute(s.trips.insert().values(id="later", departure_at=AT+timedelta(hours=1),
                                           return_at=AT+timedelta(hours=2), **base))
    with pytest.raises(IntegrityError, match="overlap"):
        with unit_of_work(db) as tx:
            tx.execute(update(s.trips).where(s.trips.c.id == "later").values(departure_at=AT))


def test_day_grouping_foreign_key(db):
    with pytest.raises(IntegrityError):
        with unit_of_work(db) as tx:
            tx.execute(s.orders.insert().values(run_id="r", id="o", delivery_date=date(2030,1,1),
                source_id="h", receiver_id="b", level="hub", state="draft", cutoff_at=AT))


def test_stock_and_crate_history_cannot_be_rewritten(db):
    def transfer(effects):
        tx = effects.connection
        tx.execute(s.locations.insert().values(run_id="r", id="other-location", kind="external"))
        tx.execute(s.movements.insert().values(run_id="r", id="move", lot_id="lot",
            from_location_id="other-location", to_location_id="location", units=100,
            command_id=effects.context.command_id, reason="opening stock evidence"))
        tx.execute(s.crates.insert().values(run_id="r", id="crate-move", container_type="crate",
            from_location_id="other-location", to_location_id="location", count=4,
            command_id=effects.context.command_id, reason="opening crate count"))
        return {}
    execute(db, context(), "opening-evidence", {}, transfer, sim_time=AT)
    for table in (s.movements, s.crates):
        with pytest.raises(IntegrityError, match="immutable"):
            with unit_of_work(db) as tx:
                tx.execute(table.delete())


def test_failed_migration_does_not_remove_existing_foundation_command(tmp_path):
    engine = open_database(tmp_path / "upgrade-data.sqlite3")
    migrate(engine, "0001_foundation")
    bootstrap(engine)
    execute(engine, context(), "noop", {}, lambda _: {"kept": True}, sim_time=AT)
    migrate(engine)
    assert rows(engine, s.commands)[0]["result"] == {"kept": True}
    engine.dispose()
