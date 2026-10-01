"""Helpers shared by the three tasks: a resilient HTTP session and a SQLite connection."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# (connect, read) timeouts in seconds. Without a timeout, requests can wait forever.
DEFAULT_TIMEOUT = (5, 30)


def build_session(user_agent: str, retries: int = 3, backoff: float = 0.5) -> requests.Session:
    """Return a Session that retries transient failures on GET.

    Retries cover dropped connections and the status codes a busy public API
    returns (429 and 5xx), backing off 0.5s, 1s, 2s... and honouring Retry-After.
    """
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,  # hand the last response back so raise_for_status names the code
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
    return session


def get_json(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
) -> Any:
    """GET a URL and decode the JSON body, failing loudly on HTTP errors or bad JSON."""
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(f"{response.url} did not return valid JSON") from exc


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite database with foreign keys enforced and rows addressable by column name."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite leaves this off by default
    return conn


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
