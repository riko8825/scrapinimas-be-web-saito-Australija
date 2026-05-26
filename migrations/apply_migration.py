"""Idempotent SQLite migration runner.

SQLite NE turi `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Šis helperis
parsina migration .sql failą, ištraukia ADD COLUMN/CREATE INDEX statement'us
ir tikrina egzistuojantį column/index sąrašą prieš leisdamas.

Naudojimas:
    python migrations/apply_migration.py 001_v2lite
    python migrations/apply_migration.py 001_v2lite --db /custom/path/outreach.db
    python migrations/apply_migration.py --list

Migration failai gyvena `migrations/<name>.sql` ir gali turėti tik:
    ALTER TABLE ... ADD COLUMN ...
    CREATE INDEX [IF NOT EXISTS] ...
    -- comments
    (tuščios eilutės)

Bet kokia kita SQL konstrukcija → skipped + WARNING.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.db import connect, default_db_path  # noqa: E402


MIGRATIONS_DIR = ROOT / "migrations"

ALTER_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s+",
    re.IGNORECASE,
)
INDEX_RE = re.compile(
    r"CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(\w+)",
    re.IGNORECASE,
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _existing_indices(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return {r["name"] for r in rows}


def _split_statements(sql: str) -> list[str]:
    """Naivus split per `;`. Migration failai paprasti — be string literal'ų."""
    out: list[str] = []
    for raw in sql.split(";"):
        stripped_lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            stripped_lines.append(line)
        stmt = " ".join(stripped_lines).strip()
        if stmt:
            out.append(stmt)
    return out


def apply(conn: sqlite3.Connection, migration_path: Path) -> dict[str, int]:
    """Run migration. Returns counters: {applied, skipped_exists, skipped_unsupported}."""
    sql = migration_path.read_text(encoding="utf-8")
    statements = _split_statements(sql)

    counters = {"applied": 0, "skipped_exists": 0, "skipped_unsupported": 0}

    for stmt in statements:
        # ADD COLUMN
        m = ALTER_RE.match(stmt)
        if m:
            table, column = m.group(1), m.group(2)
            cols = _existing_columns(conn, table)
            if column in cols:
                print(f"  SKIP (exists)  {table}.{column}")
                counters["skipped_exists"] += 1
                continue
            conn.execute(stmt)
            print(f"  APPLIED        {table}.{column}")
            counters["applied"] += 1
            continue

        # CREATE INDEX
        m = INDEX_RE.match(stmt)
        if m:
            idx_name, table = m.group(1), m.group(2)
            idx_set = _existing_indices(conn, table)
            if idx_name in idx_set:
                print(f"  SKIP (exists)  INDEX {idx_name}")
                counters["skipped_exists"] += 1
                continue
            conn.execute(stmt)
            print(f"  APPLIED        INDEX {idx_name}")
            counters["applied"] += 1
            continue

        # Anything else → warn
        snippet = stmt[:80] + ("..." if len(stmt) > 80 else "")
        print(f"  WARN (unsupported) {snippet}", file=sys.stderr)
        counters["skipped_unsupported"] += 1

    return counters


def list_migrations() -> list[Path]:
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply SQLite migrations idempotently."
    )
    parser.add_argument(
        "name", nargs="?", default=None,
        help="Migration name without .sql extension (e.g. '001_v2lite')",
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Path to outreach.db (default: dashboard/outreach.db)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available migrations and exit",
    )
    args = parser.parse_args(argv)

    if args.list or not args.name:
        migs = list_migrations()
        if not migs:
            print("(no migrations in migrations/*.sql)")
        else:
            print("Available migrations:")
            for m in migs:
                print(f"  {m.stem}")
        return 0

    mig_path = MIGRATIONS_DIR / f"{args.name}.sql"
    if not mig_path.exists():
        print(f"ERROR: migration not found: {mig_path}", file=sys.stderr)
        return 1

    db_path = args.db or default_db_path()
    if not db_path.exists():
        # Fallback OneDrive (same logic kaip enrich_places.py)
        onedrive = Path(
            r"C:\Users\pinig\OneDrive\Stalinis kompiuteris\Automatiomm_empirra"
            r"\abr-data\abr-pipeline\dashboard\outreach.db"
        )
        if onedrive.exists():
            db_path = onedrive
        else:
            print(f"ERROR: outreach.db not found at {db_path}", file=sys.stderr)
            return 1

    print(f"DB:        {db_path}")
    print(f"Migration: {mig_path.name}")
    print()

    conn = connect(db_path)
    counters = apply(conn, mig_path)
    conn.commit()

    print()
    print(
        f"Done. applied={counters['applied']}, "
        f"skipped_exists={counters['skipped_exists']}, "
        f"skipped_unsupported={counters['skipped_unsupported']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
