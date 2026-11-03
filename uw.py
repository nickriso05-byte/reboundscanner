"""
Unusual Whales data backend.

Why this exists
---------------
yfinance is throttled server-side: measured over 2,400 tickers, fetching in
parallel is no faster than serial (114s at 1 worker vs 143s at 4), so a
whole-market pool takes minutes and no amount of client concurrency fixes it.

The UW API changes the shape of the problem in two ways:

1. The stock screener returns the cross-sectional fields we would otherwise
   derive from a year of bars - last price, 52-week high/low, market cap,
   30-day average volume, relative volume, sector - 50 names per request. That
   alone answers most of a momentum screen without downloading any history.

2. Daily OHLC is a proper rate-limited API rather than a scraped endpoint, so
   the per-ticker history needed for SMA / RSI / MACD / pivots can be fetched
   concurrently.

Because the screener filters server-side (market cap, volume, price), we only
pull history for a pre-qualified pool of names instead of every listed symbol.

Credentials
-----------
The token is read from st.secrets["UW_API_TOKEN"] or the UW_API_TOKEN
environment variable. It is never written to disk or committed - see
.streamlit/secrets.toml.example.
"""

from __future__ import annotations

import os
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://api.unusualwhales.com"
SCREENER_PATH = "/api/screener/stocks"
OHLC_PATH = "/api/stock/{ticker}/ohlc/1d"

# The screener caps a page at 50 rows.
PAGE_SIZE = 50

# UW is a paid, properly rate-limited API, so history fetches do parallelise.
OHLC_WORKERS = 12

TIMEOUT = 30


class UWError(RuntimeError):
    """Raised when the UW API is unreachable or rejects the credentials."""


def _token_from_local_secrets() -> str | None:
    """
    Read .streamlit/secrets.toml sitting next to this module.

    st.secrets resolves relative to the *working directory*, so launching as
    `streamlit run momentum_screener/app.py` from a parent folder silently finds
    no token and drops back to the slow free backend. Anchoring to the module
    directory makes the token discoverable regardless of where it was launched.
    """
    path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh).get("UW_API_TOKEN") or None
    except Exception:  # noqa: BLE001 - absent or malformed is simply "no token"
        return None


def get_token() -> str | None:
    """Token from the environment, Streamlit secrets, or the local secrets file."""
    env = os.environ.get("UW_API_TOKEN")
    if env:
        return env

    try:
        import streamlit as st

        token = st.secrets.get("UW_API_TOKEN")  # type: ignore[union-attr]
        if token:
            return str(token)
    except Exception:  # noqa: BLE001 - no secrets file, or no streamlit runtime
        pass

    return _token_from_local_secrets()


def has_token() -> bool:
    return bool(get_token())


def token_diagnostics() -> list[str]:
    """
    Report each place a token was looked for and what was found there.

    "No token found" on its own is a dead end - the reason is usually specific
    (deployed without secrets configured, launched from another directory, a
    stale process) and invisible from the message alone. Surfacing the actual
    search results turns it into something answerable.
    """
    lines: list[str] = []

    env = os.environ.get("UW_API_TOKEN")
    lines.append(f"environment UW_API_TOKEN: {'set' if env else 'not set'}")

    try:
        import streamlit as st

        secret = st.secrets.get("UW_API_TOKEN")  # type: ignore[union-attr]
        lines.append(f"st.secrets (cwd {Path.cwd()}): "
                     f"{'found' if secret else 'no UW_API_TOKEN key'}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"st.secrets: unavailable ({type(exc).__name__})")

    path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if not path.exists():
        lines.append(f"{path}: does not exist")
    else:
        try:
            with open(path, "rb") as fh:
                has_key = bool(tomllib.load(fh).get("UW_API_TOKEN"))
            lines.append(f"{path}: {'found' if has_key else 'no UW_API_TOKEN key'}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{path}: unreadable ({type(exc).__name__})")

    return lines


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    return s


def _get(session: requests.Session, path: str, params: dict | None = None,
         retries: int = 3) -> dict:
    """
    GET with backoff, raising UWError carrying the actual status and body.

    A bare HTTPError is useless in a deployed app: Streamlit redacts the message,
    leaving a traceback with no status code. Rate limits and transient 5xx are
    also normal here - a 2,500-name pool is ~50 sequential screener pages - so
    they are retried rather than surfaced as failures.
    """
    url = f"{BASE_URL}{path}"
    last: str = ""

    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params or {}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise UWError(f"{path}: request failed after {retries + 1} tries - {last}")

        if resp.status_code in (401, 403):
            raise UWError(
                f"Unusual Whales rejected the request (HTTP {resp.status_code}). "
                f"{resp.text[:200]}"
            )

        if resp.status_code == 429 or resp.status_code >= 500:
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if attempt < retries:
                # Honour Retry-After when the server sends one.
                wait = resp.headers.get("Retry-After")
                try:
                    delay = float(wait) if wait else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(min(delay, 30))
                continue
            raise UWError(f"{path}: {last}")

        if not resp.ok:
            raise UWError(f"{path}: HTTP {resp.status_code} - {resp.text[:300]}")

        try:
            return resp.json()
        except ValueError:
            raise UWError(f"{path}: response was not JSON - {resp.text[:200]}")

    raise UWError(f"{path}: exhausted retries - {last}")


# -- screener ----------------------------------------------------------------

# Screener fields we keep, mapped to the metrics-table names used everywhere else.
_SCREENER_FIELDS = {
    "ticker": "ticker",
    "full_name": "name",
    "sector": "sector",
    "close": "price",
    "week_52_high": "w52_high",
    "week_52_low": "w52_low",
    "marketcap": "marketcap",
    "avg30_volume": "avg30_volume",
    "stock_volume": "stock_volume",
    "relative_volume": "rel_vol",
    "volatility": "volatility",
    "next_earnings_date": "next_earnings",
}

_NUMERIC = ["price", "w52_high", "w52_low", "marketcap", "avg30_volume",
            "stock_volume", "rel_vol", "volatility"]


def _screener_rows(payload) -> list[dict]:
    """The screener has returned rows under `data` and under `result`."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "result", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def fetch_pool(
    min_marketcap: float | None = None,
    min_stock_volume: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_names: int = 1500,
    issue_types: tuple[str, ...] = ("Common Stock", "ADR"),
    on_progress=None,
) -> pd.DataFrame:
    """
    Page the UW stock screener into a DataFrame of candidate names.

    Filtering happens server-side, so the returned pool is already restricted to
    liquid, tradeable names and we never download history for the long tail.
    """
    token = get_token()
    if not token:
        raise UWError("No UW_API_TOKEN configured.")

    params: dict[str, object] = {
        "order": "marketcap",
        "order_direction": "desc",
        # requests serialises a list value as repeated keys, which is the
        # bracketed array form the API documents (issue_types[]=A&issue_types[]=B).
        "issue_types[]": list(issue_types),
    }
    for key, value in (
        ("min_marketcap", min_marketcap),
        ("min_stock_volume", min_stock_volume),
        ("min_underlying_price", min_price),
        ("max_underlying_price", max_price),
    ):
        if value is not None:
            params[key] = str(value)

    # The REST screener does not document limit/offset (only the MCP wrapper
    # caps pages at 50), so ask for everything first and fall back to paging
    # only if the response comes back looking like a capped page.
    rows: list[dict] = []
    seen: set[str] = set()
    session = _session(token)
    page_index = 0
    while len(rows) < max_names:
        query = dict(params)
        if page_index:
            query.update({"limit": PAGE_SIZE, "offset": page_index})
        try:
            batch = _screener_rows(_get(session, SCREENER_PATH, query))
        except UWError:
            # Auth failures are worth stopping for; a page that keeps failing
            # after retries is not, once we already have a usable pool. Losing
            # the whole build over page 37 of 50 helps nobody.
            if not rows:
                raise
            break
        if not batch:
            break

        fresh = [r for r in batch if str(r.get("ticker", "")).upper() not in seen]
        seen.update(str(r.get("ticker", "")).upper() for r in batch)
        rows.extend(fresh)
        if on_progress:
            on_progress(min(len(rows) / max_names, 1.0), len(rows))

        # A short page, or a page that added nothing new, means we have it all.
        if len(batch) < PAGE_SIZE or not fresh:
            break
        page_index += 1

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    keep = {src: dst for src, dst in _SCREENER_FIELDS.items() if src in df.columns}
    df = df[list(keep)].rename(columns=keep)

    for col in _NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.upper()
        df = df.drop_duplicates("ticker")
        df = _fill_names(df)

    return df.head(max_names).reset_index(drop=True)


def _fill_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill company names the screener did not supply.

    As of October 2026 `full_name` comes back null for every row, which turned
    the Name column into a wall of "nan". The exchange symbol directories carry
    the same information for free, and universe owns that lookup.
    """
    try:
        import universe
        return universe.fill_names(df)
    except Exception:  # noqa: BLE001 - a missing name must not fail the build
        return df


# -- daily history -----------------------------------------------------------

# Ratios a real stock split actually uses. A genuine crash rarely lands within a
# few percent of exactly one of these.
# 3:2 (1.5) is deliberately excluded: its residual band would swallow ordinary
# -20% to -45% earnings crashes and "adjust" them away. Splits below 2:1 are
# rare; misreading a real crash as one is not a trade worth making.
_SPLIT_RATIOS = [2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50]

# A split day carries the split ratio *and* that session's real move: KLAC's
# 10:1 came through as 0.106, not 0.100, because the stock also rose ~6%. So
# match on the residual after dividing the split out, allowing a normal day.
_RESIDUAL_LO, _RESIDUAL_HI = 0.88, 1.15


def _split_factor(ratio: float) -> float | None:
    """Return the clean split ratio `ratio` matches, or None."""
    if not np.isfinite(ratio) or ratio <= 0:
        return None
    # Only consider moves too big to be an ordinary session.
    if 0.7 < ratio < 1.4:
        return None

    best: tuple[float, float] | None = None
    for n in _SPLIT_RATIOS:
        for candidate in (1.0 / n, float(n)):
            residual = ratio / candidate
            if _RESIDUAL_LO < residual < _RESIDUAL_HI:
                score = abs(np.log(residual))
                if best is None or score < best[0]:
                    best = (score, candidate)
    return None if best is None else best[1]


def adjust_splits(df: pd.DataFrame) -> tuple[pd.DataFrame, list[tuple[str, float]]]:
    """
    Back-adjust an unadjusted OHLCV series for stock splits.

    UW daily bars are raw: KLAC's 10:1 split shows up as a single -89% day, which
    would otherwise read as a huge drawdown from the 52-week high and average
    pre- and post-split prices together in the moving averages. The splits API
    that would confirm these needs the Advanced tier, so instead we detect the
    telltale one-day jump to a clean split ratio and rescale everything before it.

    Returns the adjusted frame and the list of (date, ratio) applied, so callers
    can surface what was changed rather than adjusting silently.
    """
    if df.empty or "Close" not in df.columns or len(df) < 2:
        return df, []

    close = df["Close"].to_numpy(dtype="float64")
    ratios = np.divide(
        close[1:], close[:-1],
        out=np.full(len(close) - 1, np.nan),
        where=close[:-1] != 0,
    )

    applied: list[tuple[str, float]] = []
    factors = np.ones(len(df), dtype="float64")
    for i, r in enumerate(ratios):
        f = _split_factor(float(r))
        if f is None:
            continue
        # Everything strictly before the split bar scales by the ratio.
        factors[: i + 1] *= f
        applied.append((str(df.index[i + 1].date()), f))

    if not applied:
        return df, []

    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = out[col].to_numpy(dtype="float64") * factors
    if "Volume" in out.columns:
        # Share count moves inversely to price.
        out["Volume"] = out["Volume"].to_numpy(dtype="float64") / factors
    return out, applied


def fetch_ohlc(ticker: str, timeframe: str = "10Y",
               session: requests.Session | None = None) -> pd.DataFrame:
    """Daily OHLCV for one ticker, indexed by date (oldest first)."""
    token = get_token()
    if not token:
        raise UWError("No UW_API_TOKEN configured.")
    own = session is None
    session = session or _session(token)
    try:
        payload = _get(session, OHLC_PATH.format(ticker=ticker),
                       {"timeframe": timeframe})
    except UWError:
        raise
    except Exception:  # noqa: BLE001 - a single bad symbol must not stop a build
        return pd.DataFrame()
    finally:
        if own:
            session.close()

    data = payload.get("data") or []
    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    date_col = "date" if "date" in df.columns else "start_time"
    if date_col not in df.columns:
        return pd.DataFrame()

    # The daily endpoint returns one row per session per date - premarket ("pr"),
    # regular ("r") and postmarket ("po") - so a year comes back as ~755 rows,
    # not ~252. Keeping all three would interleave three different bars under
    # the same date and quietly corrupt every rolling indicator, so reduce to
    # the regular session, which is the conventional daily bar.
    if "market_time" in df.columns:
        regular = df[df["market_time"] == "r"]
        if not regular.empty:
            df = regular

    df["__date"] = pd.to_datetime(df[date_col], errors="coerce", utc=True)
    df = df.dropna(subset=["__date"]).set_index("__date").sort_index()
    df.index = df.index.tz_localize(None).normalize()
    df.index.name = "Date"

    # Belt and braces: a duplicated date would break rolling windows even after
    # the session filter, so collapse any that remain to the last bar.
    df = df[~df.index.duplicated(keep="last")]

    rename = {"open": "Open", "high": "High", "low": "Low",
              "close": "Close", "volume": "Volume"}
    df = df.rename(columns=rename)
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    if not cols:
        return pd.DataFrame()

    out = df[cols].apply(pd.to_numeric, errors="coerce").dropna(how="all")
    adjusted, _ = adjust_splits(out)
    return adjusted


def fetch_many_ohlc(
    tickers: tuple[str, ...],
    timeframe: str = "10Y",
    workers: int = OHLC_WORKERS,
    on_progress=None,
) -> dict[str, pd.DataFrame]:
    """Concurrently fetch daily history for a pool of tickers."""
    token = get_token()
    if not token:
        raise UWError("No UW_API_TOKEN configured.")

    out: dict[str, pd.DataFrame] = {}
    done = 0
    session = _session(token)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(fetch_ohlc, t, timeframe, session): t for t in tickers
            }
            for fut in as_completed(futures):
                ticker = futures[fut]
                done += 1
                try:
                    df = fut.result()
                except Exception:  # noqa: BLE001
                    df = pd.DataFrame()
                if not df.empty:
                    out[ticker] = df
                if on_progress:
                    on_progress(done / max(len(tickers), 1), done, len(tickers), len(out))
    finally:
        session.close()
    return out
