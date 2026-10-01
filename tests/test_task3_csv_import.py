from __future__ import annotations

from pathlib import Path

import pytest

import task3_csv_import as importer
from common import connect

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "users.csv"


@pytest.fixture
def db():
    conn = connect(":memory:")
    importer.init_db(conn)
    yield conn
    conn.close()


def write_csv(tmp_path: Path, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / "users.csv"
    path.write_text(text, encoding=encoding)
    return path


@pytest.mark.parametrize(
    ("name", "email", "expected"),
    [
        ("  Sneha   Iyer ", "  Sneha.Iyer@Example.com ", ("Sneha Iyer", "sneha.iyer@example.com")),
        ("Fatima", "fatima@example.co.in", ("Fatima", "fatima@example.co.in")),
    ],
)
def test_clean_row_normalises(name, email, expected):
    assert importer.clean_row(name, email) == expected


@pytest.mark.parametrize(
    ("name", "email"),
    [
        ("", "a@example.com"),
        (None, "a@example.com"),
        ("A", "a@example"),
        ("A", "a(at)example.com"),
        ("A", "a b@example.com"),
        ("A", "a@exa..mple.com"),
        ("A", None),
    ],
)
def test_clean_row_rejects(name, email):
    with pytest.raises(ValueError):
        importer.clean_row(name, email)


def test_sample_file(db):
    report = importer.import_users(db, SAMPLE)
    assert (report.rows_read, report.inserted, report.unchanged) == (15, 11, 1)
    assert [line for line, _ in report.invalid] == [9, 10, 14]
    row = db.execute(
        "SELECT name, email FROM users WHERE email = 'sneha.iyer@example.com'"
    ).fetchone()
    assert tuple(row) == ("Sneha Iyer", "sneha.iyer@example.com")


def test_reimport_creates_no_duplicates(db):
    importer.import_users(db, SAMPLE)
    again = importer.import_users(db, SAMPLE)
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 12)
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 11


def test_update_mode_changes_only_rows_that_differ(db, tmp_path):
    importer.import_users(
        db, write_csv(tmp_path, "name,email\nOld Name,x@example.com\nSame,y@example.com\n")
    )
    csv_v2 = write_csv(
        tmp_path, "name,email\nNew Name,X@example.com\nSame,y@example.com\nNew,z@example.com\n"
    )
    report = importer.import_users(db, csv_v2, on_conflict="update")
    assert (report.inserted, report.updated, report.unchanged) == (1, 1, 1)
    assert (
        db.execute("SELECT name FROM users WHERE email = 'x@example.com'").fetchone()[0]
        == "New Name"
    )


def test_headers_are_case_insensitive_and_bom_is_ignored(db, tmp_path):
    path = write_csv(tmp_path, "Email, NAME \nq@example.com,Qasim\n", encoding="utf-8-sig")
    assert importer.import_users(db, path).inserted == 1


def test_missing_column_is_reported_and_nothing_is_written(db, tmp_path):
    with pytest.raises(ValueError, match="missing required column"):
        importer.import_users(db, write_csv(tmp_path, "full_name,email\nA,a@example.com\n"))
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_cli(tmp_path, capsys):
    db_file = tmp_path / "users.db"
    assert importer.main([str(SAMPLE), "--db", str(db_file)]) == 0
    assert "11 inserted, 0 updated, 1 already present, 3 rejected" in capsys.readouterr().out
    assert importer.main([str(tmp_path / "missing.csv"), "--db", str(db_file)]) == 1
