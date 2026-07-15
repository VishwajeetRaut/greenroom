"""Unit tests for the migration runner's decision logic.

Only the pure parts are covered here: which migrations exist, which are pending,
which have drifted. Everything that needs a database — transactional rollback,
the advisory lock, baseline recording — is not testable without a real Postgres,
and a mock of psycopg would only assert that the mock was called.

Those paths were verified by hand against a local Postgres 18 instance; the
results are in the PR. This file guards the ordering and drift logic, which is
where a silent mistake would otherwise go unnoticed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# scripts/ isn't a package, so load migrate.py by path.
_MIGRATE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "migrate.py"
_spec = importlib.util.spec_from_file_location("migrate", _MIGRATE_PATH)
migrate = importlib.util.module_from_spec(_spec)
sys.modules["migrate"] = migrate
_spec.loader.exec_module(migrate)


@pytest.fixture
def migrations_dir(tmp_path):
    def write(name: str, sql: str = "SELECT 1;"):
        (tmp_path / name).write_text(sql)
    return tmp_path, write


def test_discovers_in_filename_order(migrations_dir):
    """Date-prefixed filenames make lexicographic order chronological order.

    Written out of order on purpose — glob() does not promise sorted results.
    """
    tmp, write = migrations_dir
    write("20260713_third.sql")
    write("20260706_first.sql")
    write("20260708_second.sql")

    versions = [m.version for m in migrate.discover(tmp)]
    assert versions == ["20260706_first", "20260708_second", "20260713_third"]


def test_ignores_non_sql_files(migrations_dir):
    tmp, write = migrations_dir
    write("20260706_real.sql")
    (tmp / "README.md").write_text("not a migration")
    (tmp / "notes.txt").write_text("also not")

    assert [m.version for m in migrate.discover(tmp)] == ["20260706_real"]


def test_empty_directory_is_not_an_error(migrations_dir):
    tmp, _ = migrations_dir
    assert migrate.discover(tmp) == []


def test_pending_excludes_applied(migrations_dir):
    tmp, write = migrations_dir
    write("20260706_a.sql")
    write("20260708_b.sql")
    write("20260713_c.sql")
    all_m = migrate.discover(tmp)

    todo = migrate.pending(all_m, applied={"20260706_a"})
    assert [m.version for m in todo] == ["20260708_b", "20260713_c"]


def test_pending_preserves_order_with_a_gap(migrations_dir):
    """A middle migration recorded (e.g. baselined) must not reorder the rest."""
    tmp, write = migrations_dir
    write("20260706_a.sql")
    write("20260708_b.sql")
    write("20260713_c.sql")
    all_m = migrate.discover(tmp)

    todo = migrate.pending(all_m, applied={"20260708_b"})
    assert [m.version for m in todo] == ["20260706_a", "20260713_c"]


def test_nothing_pending_when_all_applied(migrations_dir):
    tmp, write = migrations_dir
    write("20260706_a.sql")
    all_m = migrate.discover(tmp)

    assert migrate.pending(all_m, applied={"20260706_a"}) == []


def test_checksum_is_content_derived(migrations_dir):
    """Identical content hashes the same; a byte of difference does not."""
    tmp, write = migrations_dir
    write("20260706_a.sql", "CREATE TABLE t (id int);")
    write("20260708_b.sql", "CREATE TABLE t (id int);")
    write("20260713_c.sql", "CREATE TABLE t (id bigint);")
    a, b, c = migrate.discover(tmp)

    assert a.checksum == b.checksum
    assert a.checksum != c.checksum


def test_drift_detects_an_edited_applied_migration(migrations_dir):
    """Editing an applied migration is how environments silently diverge.

    The file says one thing, the database another, and without this nothing
    complains.
    """
    tmp, write = migrations_dir
    write("20260706_a.sql", "CREATE TABLE t (id int);")
    [m] = migrate.discover(tmp)

    assert migrate.drifted([m], applied={"20260706_a": m.checksum}) == []

    (tmp / "20260706_a.sql").write_text("CREATE TABLE t (id bigint);")
    [edited] = migrate.discover(tmp)
    assert [d.version for d in migrate.drifted([edited], {"20260706_a": m.checksum})] == ["20260706_a"]


def test_drift_ignores_pending_migrations(migrations_dir):
    """A migration that never ran cannot have drifted from anything."""
    tmp, write = migrations_dir
    write("20260706_a.sql")
    all_m = migrate.discover(tmp)

    assert migrate.drifted(all_m, applied={}) == []


def test_real_migrations_are_discoverable():
    """The shipped migrations parse and order correctly.

    Guards against a filename that breaks the date-prefix convention and
    silently sorts into the wrong position.
    """
    found = migrate.discover()
    assert found, "no migrations found in supabase/migrations"
    versions = [m.version for m in found]
    assert versions == sorted(versions), "migrations do not sort chronologically"
    assert all(m.sql.strip() for m in found), "a migration file is empty"
