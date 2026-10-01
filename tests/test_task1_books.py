from __future__ import annotations

import pytest
import requests

import task1_books as books
from common import build_session, connect


@pytest.fixture
def session():
    # No retries in tests: a failing route should fail immediately.
    return build_session("tests", retries=0)


@pytest.fixture
def db():
    conn = connect(":memory:")
    books.init_db(conn)
    yield conn
    conn.close()


def test_parse_book_normalises_and_tolerates_missing_fields():
    full = books.parse_book(
        {
            "key": "/works/OL1W",
            "title": "  Clean   Code ",
            "author_name": ["Robert C. Martin", "Robert C. Martin"],
            "first_publish_year": 2008,
        }
    )
    assert full == books.Book("/works/OL1W", "Clean Code", ("Robert C. Martin",), 2008)

    bare = books.parse_book({"key": "/works/OL2W", "title": "Untitled Notes"})
    assert bare.authors == () and bare.publication_year is None

    assert books.parse_book({"key": "/works/OL3W"}) is None  # no title
    assert books.parse_book({"title": "No key"}) is None
    assert (
        books.parse_book({"key": "k", "title": "t", "first_publish_year": "1999"}).publication_year
        is None
    )


def test_fetch_returns_all_usable_hits_in_one_page(openlibrary, session):
    result = books.fetch_books(session, "programming", limit=50, api_url=openlibrary.url)
    assert len(result) == 8  # 9 hits, one without a title
    assert len(openlibrary.hits) == 1
    assert "fields=key%2Ctitle%2Cauthor_name%2Cfirst_publish_year" in openlibrary.hits[0]


def test_fetch_follows_pages_until_results_run_out(openlibrary, session, monkeypatch):
    monkeypatch.setattr(books, "MAX_PAGE_SIZE", 3)
    result = books.fetch_books(session, "programming", limit=50, api_url=openlibrary.url)
    assert len(result) == 8
    assert [h.split("page=")[1] for h in openlibrary.hits] == ["1", "2", "3"]


def test_fetch_stops_at_limit(openlibrary, session):
    result = books.fetch_books(session, "programming", limit=3, api_url=openlibrary.url)
    assert [b.title for b in result] == [
        "The C Programming Language",
        "Structure and Interpretation of Computer Programs",
        "The Pragmatic Programmer",
    ]
    assert len(openlibrary.hits) == 1


def test_http_errors_surface(json_server, session):
    json_server.routes["/search.json"] = lambda q: (503, {"error": "busy"})
    with pytest.raises(requests.HTTPError):
        books.fetch_books(session, "anything", limit=5, api_url=f"{json_server.base}/search.json")


def test_save_is_idempotent_and_keeps_author_order(openlibrary, session, db):
    fetched = books.fetch_books(session, "programming", limit=50, api_url=openlibrary.url)
    books.save_books(db, fetched)
    books.save_books(db, fetched)  # second run must update, not duplicate

    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 8
    assert db.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 13

    stored = {b["title"]: b for b in books.load_books(db)}
    assert (
        stored["Design Patterns"]["authors"]
        == "Erich Gamma, Richard Helm, Ralph Johnson, John Vlissides"
    )
    assert stored["Programming Notes"]["authors"] == "Unknown"
    assert stored["Programming Notes"]["year"] is None


def test_updated_author_list_replaces_old_links(db):
    books.save_books(db, [books.Book("/works/X", "Draft", ("A. Writer",), 2001)])
    books.save_books(db, [books.Book("/works/X", "Final", ("B. Editor", "A. Writer"), 2002)])
    (row,) = books.load_books(db)
    assert row == {"title": "Final", "year": 2002, "authors": "B. Editor, A. Writer"}


def test_cli_end_to_end(openlibrary, tmp_path, capsys):
    db_file = tmp_path / "books.db"
    assert books.main(["--api-url", openlibrary.url, "--db", str(db_file), "--limit", "4"]) == 0
    out = capsys.readouterr().out
    assert "The C Programming Language" in out and "Brian W. Kernighan, Dennis M. Ritchie" in out
    assert "4 book(s) by 7 author(s)" in out
