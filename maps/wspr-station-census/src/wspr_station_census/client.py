"""A small, polite ClickHouse HTTP client for the WsprDaemon mirrors.

Constraints this client exists to encode, all measured against
``wd10.wsprdaemon.org`` on 2026-07-27/28:

* **GET only.** A POST body is rejected by the front-end nginx with ``403``.
* **Plain HTTP on port 80.** ``https://`` and port 8123 do not answer.
* **No authentication** for reads.
* **A ~10 s nginx read timeout**, which surfaces as an HTML ``504`` page rather
  than a ClickHouse error. This is a *proxy* timeout, not a row cap, so the fix
  is always a narrower time window — hence :func:`fetch_window`.
* ClickHouse reports query errors with HTTP 200 in some configurations, so the
  response body must be inspected for a leading ``Code:``.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from . import __version__

LOGGER = logging.getLogger(__name__)

#: ClickHouse output format used for every query. Chosen over TSV because
#: several census aggregates return arrays (``groupUniqArray``), which TSV
#: renders as an escaped string that would have to be re-parsed by hand.
OUTPUT_FORMAT = "JSONCompactEachRowWithNames"

#: Identify ourselves so the WsprDaemon operators can see who is querying.
USER_AGENT = (
    f"wspr-station-census/{__version__} "
    "(HamSCI polar-psws; https://github.com/HamSCI/polar-psws)"
)


class QueryError(RuntimeError):
    """A ClickHouse query returned an error rather than a result set."""


class GatewayTimeout(RuntimeError):
    """The nginx front end timed out; the query window is too wide."""


class ClickHouseHTTP:
    """Minimal GET-only ClickHouse HTTP client with pacing and retries.

    Parameters
    ----------
    endpoint : str
        Base URL, e.g. ``http://wd10.wsprdaemon.org/``.
    timeout_s : float
        Per-request client timeout in seconds.
    pace_s : float
        Minimum wall-clock interval enforced between successive requests.
    max_retries : int
        Attempts per request before raising. Gateway timeouts are *not*
        retried here — widening the retry count cannot fix a too-wide window,
        so :exc:`GatewayTimeout` propagates immediately to
        :func:`fetch_window`, which narrows the window instead.

    Attributes
    ----------
    n_requests : int
        Successful requests issued, for the run manifest.
    seconds_spent : float
        Cumulative request wall time, for the run manifest.
    """

    def __init__(
        self,
        endpoint: str,
        timeout_s: float = 60.0,
        pace_s: float = 0.5,
        max_retries: int = 3,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.pace_s = pace_s
        self.max_retries = max_retries
        self.n_requests = 0
        self.seconds_spent = 0.0
        self._last_request_at = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    # ---------------------------------------------------------------- plumbing

    def _wait_for_slot(self) -> None:
        """Sleep as needed to honour :attr:`pace_s`."""
        gap = time.monotonic() - self._last_request_at
        if gap < self.pace_s:
            time.sleep(self.pace_s - gap)

    def query_text(self, sql: str) -> str:
        """Execute ``sql`` and return the raw response body.

        Parameters
        ----------
        sql : str
            A complete SQL statement. A ``FORMAT`` clause is appended if absent.

        Returns
        -------
        str
            The response body, unparsed.

        Raises
        ------
        GatewayTimeout
            The nginx front end returned ``504``; narrow the time window.
        QueryError
            ClickHouse reported a query error, or the request failed after
            :attr:`max_retries` attempts.
        """
        statement = sql.strip().rstrip(";")
        if "FORMAT" not in statement.upper():
            statement = f"{statement} FORMAT {OUTPUT_FORMAT}"

        params = {
            "query": statement,
            # Without this, ClickHouse quotes 64-bit integers as JSON strings.
            "output_format_json_quote_64bit_integers": "0",
        }

        last_error: str = ""
        for attempt in range(1, self.max_retries + 1):
            self._wait_for_slot()
            started = time.monotonic()
            try:
                response = self._session.get(
                    self.endpoint, params=params, timeout=self.timeout_s
                )
            except requests.RequestException as exc:  # network-level failure
                last_error = f"{type(exc).__name__}: {exc}"
                self._last_request_at = time.monotonic()
                if attempt == self.max_retries:
                    raise QueryError(
                        f"request failed after {attempt} attempts: {last_error}"
                    ) from exc
                time.sleep(2.0 * attempt)
                continue

            elapsed = time.monotonic() - started
            self._last_request_at = time.monotonic()
            self.seconds_spent += elapsed

            if response.status_code == 504:
                raise GatewayTimeout(
                    f"nginx 504 after {elapsed:.1f} s — query window too wide"
                )
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            elif response.text.startswith("Code:"):
                # ClickHouse can report SQL errors with a 200 status.
                raise QueryError(response.text[:800])
            else:
                self.n_requests += 1
                LOGGER.debug("query ok in %.2f s (%d bytes)", elapsed, len(response.text))
                return response.text

            if attempt == self.max_retries:
                raise QueryError(f"after {attempt} attempts: {last_error}")
            time.sleep(2.0 * attempt)

        raise QueryError(last_error or "unreachable")  # pragma: no cover

    def query_frame(self, sql: str) -> pd.DataFrame:
        """Execute ``sql`` and return the result as a :class:`pandas.DataFrame`."""
        return parse_response(self.query_text(sql))


def parse_response(body: str) -> pd.DataFrame:
    """Parse a ``JSONCompactEachRowWithNames`` response body.

    The first line is a JSON array of column names; each subsequent line is a
    JSON array of values in that order.

    Parameters
    ----------
    body : str
        Raw response body.

    Returns
    -------
    pandas.DataFrame
        Empty with no columns if the body is blank (a query that matched no rows
        still returns the header line, so this is rare).
    """
    lines = [line for line in body.splitlines() if line]
    if not lines:
        return pd.DataFrame()
    columns: list[str] = json.loads(lines[0])
    rows: list[list[Any]] = [json.loads(line) for line in lines[1:]]
    return pd.DataFrame(rows, columns=columns)


def fetch_window(
    client: ClickHouseHTTP,
    build_sql: Callable[[date, date], str],
    lo: date,
    hi: date,
    min_window: timedelta = timedelta(days=1),
) -> list[pd.DataFrame]:
    """Fetch ``[lo, hi)`` as one query, bisecting the window on gateway timeout.

    The nginx front end enforces a wall-clock timeout rather than a row limit,
    so a ``504`` is always a signal to ask for less time — never to retry the
    same request. This walks the window down by halving until each piece
    answers, or until the piece is smaller than ``min_window``.

    Parameters
    ----------
    client : ClickHouseHTTP
        Client to issue queries with.
    build_sql : callable
        ``build_sql(lo, hi) -> str``, producing SQL for a half-open window.
    lo, hi : datetime.date
        Half-open window bounds.
    min_window : datetime.timedelta, optional
        Refuse to bisect below this width; a timeout at this width is a real
        error and is re-raised.

    Returns
    -------
    list of pandas.DataFrame
        One frame per sub-window actually queried, in chronological order.

    Raises
    ------
    GatewayTimeout
        A window at or below ``min_window`` still timed out.
    """
    try:
        return [client.query_frame(build_sql(lo, hi))]
    except GatewayTimeout:
        span = hi - lo
        if span <= min_window:
            LOGGER.error("504 at minimum window %s..%s; giving up", lo, hi)
            raise
        mid = lo + span / 2
        LOGGER.warning("504 for %s..%s (%s); bisecting at %s", lo, hi, span, mid)
        frames = fetch_window(client, build_sql, lo, mid, min_window)
        frames += fetch_window(client, build_sql, mid, hi, min_window)
        return frames


# --------------------------------------------------------------- raw response
# cache
#
# The cache stores the *literal bytes the server returned*, gzipped. That keeps
# the package free of a parquet dependency, makes extraction resumable, and
# preserves an auditable record of what the database actually said -- which
# matters for a project whose data-provenance requirements are explicit.


def cache_path(cache_dir: Path, family: str, lo: date, hi: date) -> Path:
    """Return the cache file for one query family over one window."""
    return cache_dir / family / f"{lo:%Y%m%d}_{hi:%Y%m%d}.jsonl.gz"


def write_cache(path: Path, body: str) -> None:
    """Write a raw response body to ``path``, gzipped, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        handle.write(body)
    tmp.replace(path)  # atomic, so an interrupted run leaves no partial file


def read_cache(path: Path) -> pd.DataFrame:
    """Read and parse a cached response body."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return parse_response(handle.read())
