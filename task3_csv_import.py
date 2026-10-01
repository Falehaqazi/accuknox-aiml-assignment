"""Task 3: import users from a CSV file (name, email) into SQLite.

Each row is cleaned and validated before it is written. The whole file goes in
as one transaction, and email is the table's natural key, so running the import
twice never creates duplicate users.

    python task3_csv_import.py data/users.csv
    python task3_csv_import.py data/users.csv --on-conflict update
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from common import configure_logging, connect

log = logging.getLogger("csv-import")

T = TypeVar("T")

REQUIRED_COLUMNS = ("name", "email")
BATCH_SIZE = 1000

# Deliberately practical rather than RFC 5322-complete: one @, no spaces, and a
# domain made of dot-separated labels ending in a 2+ letter TLD.
EMAIL_RE = re.compile(r"^[^@\s]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL CHECK (length(trim(name)) > 0),
    email      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INSERT_SQL = {
    # Keep the row that is already there.
    "skip": "INSERT INTO users (name, email) VALUES (?, ?) ON CONFLICT (email) DO NOTHING",
    # Let the CSV win, but only touch rows whose name actually changed.
    "update": """
        INSERT INTO users (name, email) VALUES (?, ?)
        ON CONFLICT (email) DO UPDATE SET name = excluded.name, updated_at = datetime('now')
        WHERE users.name IS NOT excluded.name
    """,
}


@dataclass
class ImportReport:
    rows_read: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0  # valid rows whose email was already stored (or repeated in the file)
    invalid: list[tuple[int, str]] = field(default_factory=list)  # (line number, reason)

    def summary(self) -> str:
        return (
            f"{self.rows_read} rows read: {self.inserted} inserted, {self.updated} updated, "
            f"{self.unchanged} already present, {len(self.invalid)} rejected"
        )


def clean_row(name: str | None, email: str | None) -> tuple[str, str]:
    """Normalise one row, or raise ValueError saying why it was rejected."""
    clean_name = " ".join((name or "").split())  # trim and collapse inner whitespace
    clean_email = (email or "").strip().lower()
    if not clean_name:
        raise ValueError("name is empty")
    if not EMAIL_RE.match(clean_email):
        raise ValueError(f"invalid email {email!r}")
    return clean_name, clean_email


def read_rows(csv_path: Path) -> Iterator[tuple[int, str | None, str | None]]:
    """Stream (line_number, name, email) from the CSV, matching headers case-insensitively."""
    # utf-8-sig strips the byte-order mark Excel adds when it saves CSV as UTF-8.
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"{csv_path} is empty")
        columns = {header.strip().lower(): header for header in reader.fieldnames if header}
        missing = [c for c in REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise ValueError(f"{csv_path} is missing required column(s): {', '.join(missing)}")
        for row in reader:
            yield reader.line_num, row.get(columns["name"]), row.get(columns["email"])


def chunked(items: Iterable[T], size: int) -> Iterator[list[T]]:
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def import_users(
    conn: sqlite3.Connection,
    csv_path: Path,
    on_conflict: str = "skip",
    batch_size: int = BATCH_SIZE,
) -> ImportReport:
    """Validate and insert every row of the CSV in a single transaction."""
    if on_conflict not in INSERT_SQL:
        raise ValueError(f"on_conflict must be one of {sorted(INSERT_SQL)}")
    report = ImportReport()

    def valid_rows() -> Iterator[tuple[str, str]]:
        for line, name, email in read_rows(csv_path):
            report.rows_read += 1
            try:
                yield clean_row(name, email)
            except ValueError as exc:
                report.invalid.append((line, str(exc)))
                log.warning("Line %d rejected: %s", line, exc)

    (before,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    changes_before = conn.total_changes
    valid = 0
    with conn:  # one transaction: a crash halfway leaves the table as it was
        for batch in chunked(valid_rows(), batch_size):
            conn.executemany(INSERT_SQL[on_conflict], batch)
            valid += len(batch)
    (after,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()

    # New rows show up in COUNT(*); every other change the statement made was an update.
    report.inserted = after - before
    report.updated = conn.total_changes - changes_before - report.inserted
    report.unchanged = valid - report.inserted - report.updated
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import users from a CSV file into SQLite.")
    parser.add_argument("csv_file", type=Path, help="CSV with 'name' and 'email' columns")
    parser.add_argument("--db", default="output/users.db", help="SQLite file to write")
    parser.add_argument(
        "--on-conflict",
        choices=sorted(INSERT_SQL),
        default="skip",
        help="what to do when the email already exists (default: skip)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    conn = connect(args.db)
    try:
        init_db(conn)
        report = import_users(conn, args.csv_file, args.on_conflict)
        (total,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    except (OSError, ValueError, csv.Error) as exc:
        log.error("Import failed: %s", exc)
        return 1
    finally:
        conn.close()

    print(report.summary())
    print(f"{total} user(s) now in {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
