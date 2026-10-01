"""Task 1: fetch books from a REST API, store them in SQLite, and display them.

Source: the Open Library Search API (https://openlibrary.org/dev/docs/api/search).
It needs no API key, and every hit carries a title, its author names and the
year the work was first published.

    python task1_books.py --query "python programming" --limit 25
    python task1_books.py --show-only        # print what is already stored
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby
from typing import Any

import requests

from common import build_session, configure_logging, connect, get_json

log = logging.getLogger("books")

API_URL = "https://openlibrary.org/search.json"
FIELDS = "key,title,author_name,first_publish_year"
MAX_PAGE_SIZE = 100
USER_AGENT = "accuknox-assignment-books/1.0 (python-requests)"

# Books and authors are many-to-many, so they get their own tables and a link
# table. source_key (Open Library's work id) makes every write an idempotent
# upsert: running the script twice updates rows instead of duplicating them.
SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id               INTEGER PRIMARY KEY,
    source_key       TEXT    NOT NULL UNIQUE,
    title            TEXT    NOT NULL,
    publication_year INTEGER CHECK (publication_year BETWEEN 1 AND 2100),  -- NULL allowed
    fetched_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS authors (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS book_authors (
    book_id   INTEGER NOT NULL REFERENCES books(id)   ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,   -- keeps the credited order of co-authors
    PRIMARY KEY (book_id, author_id)
);
CREATE INDEX IF NOT EXISTS idx_books_year          ON books(publication_year);
CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);
"""


@dataclass(frozen=True)
class Book:
    source_key: str
    title: str
    authors: tuple[str, ...]
    publication_year: int | None


def parse_book(doc: dict[str, Any]) -> Book | None:
    """Map one search hit to a Book. Returns None when it has no key or title."""
    key = doc.get("key")
    title = " ".join(str(doc.get("title") or "").split())
    if not key or not title:
        return None
    names = (" ".join(str(n).split()) for n in doc.get("author_name") or [])
    authors = tuple(dict.fromkeys(n for n in names if n))  # drop blanks and repeats, keep order
    year = doc.get("first_publish_year")
    if not isinstance(year, int) or isinstance(year, bool) or not 1 <= year <= 2100:
        year = None
    return Book(str(key), title, authors, year)


def fetch_books(
    session: requests.Session, query: str, limit: int, api_url: str = API_URL
) -> list[Book]:
    """Page through the search results until `limit` usable books are collected."""
    page_size = min(limit, MAX_PAGE_SIZE)
    books: dict[str, Book] = {}
    skipped = 0
    page = 1
    while len(books) < limit:
        params = {"q": query, "fields": FIELDS, "limit": page_size, "page": page}
        payload = get_json(session, api_url, params=params)
        docs = payload.get("docs") or []
        before = len(books)
        for doc in docs:
            book = parse_book(doc)
            if book is None:
                skipped += 1
                continue
            books.setdefault(book.source_key, book)  # the same work can appear on two pages
            if len(books) == limit:
                break
        total = payload.get("numFound", payload.get("num_found", 0))
        log.debug(
            "page %d: %d docs, %d of %d results seen", page, len(docs), page * page_size, total
        )
        # Stop at the last page, or if a page adds nothing new (guards against an
        # API that ignores the page parameter and repeats itself forever).
        if not docs or page * page_size >= total or len(books) == before:
            break
        page += 1
    if skipped:
        log.info("Skipped %d result(s) without a key or title", skipped)
    return list(books.values())


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def save_books(conn: sqlite3.Connection, books: Iterable[Book]) -> int:
    """Upsert books with their authors in a single transaction and return how many were written."""
    written = 0
    with conn:  # commits once at the end, or rolls everything back on error
        for book in books:
            conn.execute(
                """
                INSERT INTO books (source_key, title, publication_year)
                VALUES (?, ?, ?)
                ON CONFLICT (source_key) DO UPDATE SET
                    title            = excluded.title,
                    publication_year = excluded.publication_year,
                    fetched_at       = datetime('now')
                """,
                (book.source_key, book.title, book.publication_year),
            )
            (book_id,) = conn.execute(
                "SELECT id FROM books WHERE source_key = ?", (book.source_key,)
            ).fetchone()
            # Replace the author links so a corrected author list never leaves stale rows.
            conn.execute("DELETE FROM book_authors WHERE book_id = ?", (book_id,))
            for position, name in enumerate(book.authors):
                conn.execute("INSERT OR IGNORE INTO authors (name) VALUES (?)", (name,))
                (author_id,) = conn.execute(
                    "SELECT id FROM authors WHERE name = ?", (name,)
                ).fetchone()
                conn.execute(
                    "INSERT OR IGNORE INTO book_authors (book_id, author_id, position) "
                    "VALUES (?, ?, ?)",
                    (book_id, author_id, position),
                )
            written += 1
    return written


def load_books(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read every stored book with its authors in credited order, oldest first."""
    rows = conn.execute(
        """
        SELECT b.id, b.title, b.publication_year, a.name AS author
        FROM books b
        LEFT JOIN book_authors ba ON ba.book_id = b.id
        LEFT JOIN authors a       ON a.id = ba.author_id
        ORDER BY b.publication_year IS NULL, b.publication_year,
                 b.title COLLATE NOCASE, b.id, ba.position
        """
    ).fetchall()
    books = []
    for _, group in groupby(rows, key=lambda row: row["id"]):
        group_rows = list(group)
        authors = [row["author"] for row in group_rows if row["author"]]
        books.append(
            {
                "title": group_rows[0]["title"],
                "year": group_rows[0]["publication_year"],
                "authors": ", ".join(authors) or "Unknown",
            }
        )
    return books


def render_table(books: list[dict[str, Any]], max_title: int = 46, max_authors: int = 40) -> str:
    """Format books as a fixed-width text table that fits a normal terminal."""

    def clip(text: str, width: int) -> str:
        return text if len(text) <= width else text[: width - 3] + "..."

    title_w = min(max((len(b["title"]) for b in books), default=5), max_title)
    author_w = min(max((len(b["authors"]) for b in books), default=7), max_authors)
    header = f"{'Year':<6}{'Title':<{title_w + 2}}Author(s)"
    lines = [header, "-" * (6 + title_w + 2 + author_w)]
    for b in books:
        year = str(b["year"]) if b["year"] else "n/a"
        title = clip(b["title"], title_w)
        authors = clip(b["authors"], author_w)
        lines.append(f"{year:<6}{title:<{title_w + 2}}{authors}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch books from Open Library into SQLite.")
    parser.add_argument("--query", default="python programming", help="search terms")
    parser.add_argument("--limit", type=int, default=20, help="books to fetch (default 20)")
    parser.add_argument("--db", default="output/books.db", help="SQLite file to write")
    parser.add_argument(
        "--api-url", default=API_URL, help="search endpoint (for tests or a mirror)"
    )
    parser.add_argument("--show-only", action="store_true", help="print stored books, skip the API")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")

    conn = connect(args.db)
    try:
        init_db(conn)
        if not args.show_only:
            session = build_session(USER_AGENT)
            try:
                books = fetch_books(session, args.query, args.limit, api_url=args.api_url)
            except (requests.RequestException, ValueError) as exc:
                log.error("Could not fetch books: %s", exc)
                return 1
            written = save_books(conn, books)
            log.info("Saved %d book(s) for query %r into %s", written, args.query, args.db)

        stored = load_books(conn)
        (n_authors,) = conn.execute("SELECT COUNT(*) FROM authors").fetchone()
        print(render_table(stored))
        print(f"\n{len(stored)} book(s) by {n_authors} author(s) in {args.db}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
