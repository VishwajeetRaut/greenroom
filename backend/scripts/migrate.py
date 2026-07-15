"""
Applies supabase/migrations/*.sql to a Postgres database, in filename order,
exactly once each.

Until now every migration in this repo was applied by hand — supabase/schema.sql
still opens with "Run this in the Supabase SQL editor". That works right up until
someone forgets, or applies them in a different order, or half-applies one and
nobody can tell which. This tracks what ran in the database itself.

    export DATABASE_URL='postgresql://postgres:PASSWORD@db.<ref>.supabase.co:5432/postgres'

    python scripts/migrate.py --status      # what is applied, what is pending
    python scripts/migrate.py --dry-run     # what would run, without running it
    python scripts/migrate.py               # apply everything pending
    python scripts/migrate.py --baseline 20260713_analytics_events

DATABASE_URL is the Postgres connection string (Supabase → Project Settings →
Database → Connection string), NOT the SUPABASE_URL/service-role key the app
uses. The app talks to PostgREST, which cannot execute DDL at all.

## Adopting this on a database that already has the tables

The first four migrations were applied by hand before this existed, so a plain
run would try to apply them again. --baseline records migrations up to and
including the one named, WITHOUT executing them, so the runner's view of the
database matches reality:

    python scripts/migrate.py --baseline 20260713_analytics_events

Then future migrations apply normally. Baseline is a no-op for migrations that
are already recorded, so it is safe to re-run.

## Guarantees

- Each migration runs inside a transaction together with its schema_migrations
  insert, so a migration can never end up applied-but-unrecorded (or the
  reverse). A failure rolls the whole thing back and stops — later migrations
  do not run, because they may depend on the one that failed.
- A session-level advisory lock means two concurrent runners (two CI jobs, two
  engineers) queue rather than race.
- Migrations are sent as a single statement each, so PL/pgSQL bodies with
  semicolons inside $$ ... $$ are parsed by the server, not split by us.

Caveat: statements that cannot run inside a transaction (CREATE INDEX
CONCURRENTLY, ALTER TYPE ... ADD VALUE on older servers) will fail here. No
current migration uses them; if one needs to, it needs its own path.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

# Arbitrary but fixed: any runner against this database takes the same lock.
_LOCK_KEY = 8675309

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class Migration:
    def __init__(self, path: Path):
        self.path = path
        self.version = path.stem
        self.sql = path.read_text()
        self.checksum = hashlib.sha256(self.sql.encode()).hexdigest()[:16]


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Every .sql in the directory, ordered by filename.

    Filenames are date-prefixed (20260715_...), so lexicographic order is
    chronological order. That is the whole ordering scheme — it relies on
    nobody back-dating a migration.
    """
    return [Migration(p) for p in sorted(directory.glob("*.sql"))]


def pending(all_migrations: list[Migration], applied: set[str]) -> list[Migration]:
    return [m for m in all_migrations if m.version not in applied]


def drifted(all_migrations: list[Migration], applied: dict[str, str]) -> list[Migration]:
    """Applied migrations whose file has changed since it ran.

    Editing an applied migration is a silent way to make environments diverge:
    the file says one thing, the database another, and nothing complains. We
    only warn — the database is already whatever it is, and rerunning would not
    fix it.
    """
    return [m for m in all_migrations if m.version in applied and applied[m.version] != m.checksum]


def _connect(dsn: str):
    try:
        import psycopg
    except ImportError:
        sys.exit(
            "psycopg is not installed. It is a dev/ops dependency, not a runtime one:\n"
            "    pip install -r requirements-dev.txt"
        )
    return psycopg.connect(dsn, autocommit=True)


def _applied_map(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(_TRACKING_TABLE)
        cur.execute("SELECT version, checksum FROM schema_migrations")
        return dict(cur.fetchall())


def cmd_status(conn, migrations: list[Migration]) -> int:
    applied = _applied_map(conn)
    for m in migrations:
        if m.version not in applied:
            mark = "PENDING"
        elif applied[m.version] != m.checksum:
            mark = "APPLIED (file changed since!)"
        else:
            mark = "applied"
        print(f"  {mark:<30} {m.version}")
    n_pending = len(pending(migrations, set(applied)))
    print(f"\n{len(applied)} applied, {n_pending} pending")
    return 0


def cmd_baseline(conn, migrations: list[Migration], up_to: str) -> int:
    versions = [m.version for m in migrations]
    if up_to not in versions:
        print(f"No migration named {up_to!r}. Known:", file=sys.stderr)
        for v in versions:
            print(f"  {v}", file=sys.stderr)
        return 1

    applied = _applied_map(conn)
    cutoff = versions.index(up_to)
    marked = 0
    with conn.cursor() as cur:
        for m in migrations[: cutoff + 1]:
            if m.version in applied:
                continue
            cur.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (m.version, m.checksum),
            )
            print(f"  baselined (not executed)  {m.version}")
            marked += 1
    print(f"\n{marked} migration(s) recorded as already applied.")
    return 0


def cmd_apply(conn, migrations: list[Migration], dry_run: bool) -> int:
    applied = _applied_map(conn)

    for m in drifted(migrations, applied):
        print(f"  WARNING: {m.version} was applied, but the file has changed since.", file=sys.stderr)

    todo = pending(migrations, set(applied))
    if not todo:
        print("Nothing to apply — the database is up to date.")
        return 0

    if dry_run:
        print("Would apply, in order:")
        for m in todo:
            print(f"  {m.version}")
        return 0

    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
    try:
        # Re-read: another runner may have applied these while we waited.
        applied = _applied_map(conn)
        todo = pending(migrations, set(applied))

        for m in todo:
            print(f"  applying  {m.version} ... ", end="", flush=True)
            try:
                # One transaction covering the migration AND its bookkeeping.
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(m.sql)
                        cur.execute(
                            "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                            (m.version, m.checksum),
                        )
            except Exception as exc:
                print("FAILED")
                print(f"\n{m.version} failed and was rolled back:\n  {exc}", file=sys.stderr)
                print("Stopping — later migrations may depend on this one.", file=sys.stderr)
                return 1
            print("ok")
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))

    print(f"\nApplied {len(todo)} migration(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--status", action="store_true", help="show applied/pending and exit")
    parser.add_argument("--dry-run", action="store_true", help="show what would be applied")
    parser.add_argument(
        "--baseline", metavar="VERSION",
        help="record migrations up to and including VERSION as applied, without running them",
    )
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit(
            "DATABASE_URL is not set. This is the Postgres connection string\n"
            "(Supabase → Project Settings → Database), not SUPABASE_URL:\n"
            "    export DATABASE_URL='postgresql://postgres:PASSWORD@db.<ref>.supabase.co:5432/postgres'"
        )

    if not MIGRATIONS_DIR.is_dir():
        sys.exit(f"No migrations directory at {MIGRATIONS_DIR}")

    migrations = discover()
    if not migrations:
        print(f"No .sql files in {MIGRATIONS_DIR}")
        return 0

    with _connect(dsn) as conn:
        if args.status:
            return cmd_status(conn, migrations)
        if args.baseline:
            return cmd_baseline(conn, migrations, args.baseline)
        return cmd_apply(conn, migrations, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
