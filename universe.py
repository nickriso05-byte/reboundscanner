"""
Ticker universe loader.

Two families of universe:

  • "S&P 500"      – the classic large-cap index (fast, ~500 names)
  • "All US listed" – every common stock on NASDAQ / NYSE / NYSE American,
                      sourced from the official NASDAQ Trader symbol directory
                      (~6,000 names once funds, ETFs, warrants, units, rights,
                      preferreds and notes are stripped out)

The NASDAQ Trader files are the authoritative daily symbol directory and need
no API key, which makes them the right backbone for a "whole market" screen.
"""

from __future__ import annotations

import io
import os
import re

import pandas as pd
import requests
import streamlit as st

_HERE = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_CSV = os.path.join(_HERE, "sp500.csv")

_GITHUB_CSV = (
    "https://raw.githubusercontent.com/datasets/"
    "s-and-p-500-companies/main/data/constituents.csv"
)
_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

_NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Exchange single-letter codes used in otherlisted.txt
_EXCHANGE_NAMES = {
    "N": "NYSE",
    "A": "NYSE American",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}

# Security names that are not ordinary common equity.  Plural-tolerant.
_NON_COMMON = re.compile(
    r"\b("
    r"warrants?|rights?|units?|preferreds?|debentures?|"
    r"notes?\s+due|convertible\s+notes?|subordinated|"
    r"depositary\s+shares?\s+.*preferred|"
    r"etn|exchange[- ]traded\s+note|index[- ]linked|"
    r"closed[- ]end\s+fund|\bfund\b"
    r")\b",
    re.I,
)


# ── S&P 500 sources ───────────────────────────────────────────────────────────

def _from_github() -> pd.DataFrame:
    r = requests.get(_GITHUB_CSV, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    return df[["Symbol", "Security", "GICS Sector"]].rename(
        columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"}
    )


def _from_wikipedia() -> pd.DataFrame:
    tables = pd.read_html(_WIKI_URL)
    for tbl in tables:
        cols = [str(c).lower() for c in tbl.columns]
        if any("symbol" in c or "ticker" in c for c in cols):
            ticker_col = next(c for c in tbl.columns if "symbol" in str(c).lower() or "ticker" in str(c).lower())
            name_col = next((c for c in tbl.columns if "security" in str(c).lower() or "name" in str(c).lower()), tbl.columns[1])
            sector_col = next((c for c in tbl.columns if "sector" in str(c).lower() or "gics" in str(c).lower()), None)
            out = tbl[[ticker_col, name_col]].rename(columns={ticker_col: "ticker", name_col: "name"})
            out["sector"] = tbl[sector_col] if sector_col is not None else "Unknown"
            return out
    raise ValueError("No suitable table found on Wikipedia")


def _from_bundled() -> pd.DataFrame:
    if not os.path.exists(_BUNDLED_CSV):
        raise FileNotFoundError(f"Bundled CSV not found at {_BUNDLED_CSV}")
    return pd.read_csv(_BUNDLED_CSV)


@st.cache_data(ttl=86400, show_spinner=False)
def _sp500() -> pd.DataFrame:
    errors = []
    for source_fn, label in [
        (_from_github, "GitHub CSV"),
        (_from_wikipedia, "Wikipedia"),
        (_from_bundled, "bundled CSV"),
    ]:
        try:
            df = source_fn()
            if df is not None and not df.empty:
                df = df.copy()
                df["exchange"] = "S&P 500"
                return df
        except Exception as e:  # noqa: BLE001 - fall through to next source
            errors.append(f"{label}: {e}")
    raise RuntimeError("Could not load S&P 500 list.\n" + "\n".join(errors))


# ── Whole-market source (NASDAQ Trader symbol directory) ─────────────────────

def _read_pipe_file(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="|")
    # Last line of these files is a "File Creation Time" footer.
    first_col = df.columns[0]
    return df[~df[first_col].astype(str).str.contains("File Creation Time", na=False)]


# The instrument descriptor the exchange files append to every name: "NVIDIA
# Corporation - Common Stock", "Alcoa Corporation Common Stock". It is the same
# for thousands of rows, so it carries no information and only costs column
# width. Anchored to the end so a descriptor word inside a real name survives.
_SHARE_CLASS = re.compile(
    r"(?:\s+-)?\s+(?:"
    r"(?:class\s+[a-z]\s+)?(?:new\s+)?"
    r"(?:common|ordinary|capital|voting|subordinate\s+voting|registered)\s+"
    r"(?:stock|shares?)|"
    r"american\s+depositar(?:y|ies)\s+(?:shares?|receipts?).*|"
    r"depositary\s+shares?.*|"
    r"common\s+stock.*|"
    r"ordinary\s+shares?.*"
    r")\s*$",
    re.I,
)


def _clean_name(raw: str) -> str:
    """The company name without the exchange's instrument boilerplate."""
    name = _SHARE_CLASS.sub("", str(raw)).strip(" -,")
    return name or str(raw).strip()


@st.cache_data(ttl=86400, show_spinner=False)
def security_names() -> dict[str, str]:
    """
    Ticker -> company name, from the exchange symbol directories.

    Unusual Whales stopped populating the screener's `full_name` (it returns
    null for every row as of October 2026), so names come from here instead.
    These are the same two files the whole-market universe already reads: free,
    keyless, and cached for a day, so restoring the column costs no API calls.

    Deliberately unfiltered, unlike `_all_us_listed`: this is a lookup table,
    and an ETF or odd share class in the pool still deserves its name.
    """
    out: dict[str, str] = {}
    for url, symbol_cols in ((_OTHER_LISTED, ("NASDAQ Symbol", "ACT Symbol")),
                             (_NASDAQ_LISTED, ("Symbol",))):
        try:
            df = _read_pipe_file(url)
        except Exception:  # noqa: BLE001 - a missing name is not worth failing over
            continue
        if "Security Name" not in df.columns:
            continue
        for col in symbol_cols:
            if col not in df.columns:
                continue
            for ticker, raw in zip(df[col], df["Security Name"]):
                symbol = str(ticker).strip().upper()
                if not symbol:
                    continue
                name = _clean_name(raw)
                # Share classes are written with a dot here (BRK.B) and without
                # one by the screener (BRKB), so index both spellings.
                for key in (symbol, symbol.replace(".", "")):
                    out.setdefault(key, name)
    return out


def fill_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill a pool's missing company names from the exchange directories.

    Applied per missing row, so a name the screener does supply is still
    preferred if `full_name` ever starts coming back. Also applied when a pool
    is read, not only when it is built, so a pool already on disk with the
    empty names repairs itself on load instead of waiting for the next refresh.
    """
    if df is None or df.empty or "ticker" not in df.columns:
        return df

    blank = pd.Series(index=df.index, dtype=object)
    if "name" in df.columns:
        have = df["name"]
        blank = have.where(have.notna() & (have.astype(str).str.strip() != ""), None)
        if blank.notna().all():
            return df

    try:
        lookup = security_names()
    except Exception:  # noqa: BLE001 - a missing name must not fail the build
        lookup = {}

    df = df.copy()
    filled = blank.fillna(df["ticker"].astype(str).str.upper().map(lookup))
    # Empty beats the literal string "nan" for anything still unknown.
    df["name"] = filled.fillna("")
    return df


@st.cache_data(ttl=86400, show_spinner=False)
def _all_us_listed() -> pd.DataFrame:
    nd = _read_pipe_file(_NASDAQ_LISTED)
    ot = _read_pipe_file(_OTHER_LISTED)

    nasdaq = pd.DataFrame({
        "ticker": nd["Symbol"],
        "name": nd["Security Name"],
        "etf": nd["ETF"],
        "test": nd["Test Issue"],
        "exchange": "NASDAQ",
    })
    other = pd.DataFrame({
        "ticker": ot["NASDAQ Symbol"],
        "name": ot["Security Name"],
        "etf": ot["ETF"],
        "test": ot["Test Issue"],
        "exchange": ot["Exchange"].map(_EXCHANGE_NAMES).fillna("Other"),
    })

    df = pd.concat([nasdaq, other], ignore_index=True)

    # Drop test issues and fund wrappers
    df = df[df["test"].astype(str).str.upper() != "Y"]
    df = df[df["etf"].astype(str).str.upper() != "Y"]

    # Drop warrants / rights / units / preferreds / notes / CEFs
    df = df[~df["name"].astype(str).str.contains(_NON_COMMON, na=False, regex=True)]

    # Plain 1-5 letter tickers only (drops class/warrant suffixed symbols)
    tick = df["ticker"].astype(str)
    df = df[tick.str.fullmatch(r"[A-Z]{1,5}")]

    df = df.drop(columns=["etf", "test"])
    df["sector"] = "Unknown"
    return df.reset_index(drop=True)


# ── public API ───────────────────────────────────────────────────────────────

SCOPES = {
    "S&P 500": "sp500",
    "NASDAQ": "nasdaq",
    "NYSE": "nyse",
    "All US listed": "all",
}


@st.cache_data(ttl=86400, show_spinner="Loading ticker universe…")
def get_universe(scope: str = "S&P 500") -> pd.DataFrame:
    """
    Returns DataFrame[ticker, name, sector, exchange] for the requested scope.
    """
    key = SCOPES.get(scope, "sp500")

    if key == "sp500":
        df = _sp500()
    else:
        df = _all_us_listed()
        if key == "nasdaq":
            df = df[df["exchange"] == "NASDAQ"]
        elif key == "nyse":
            df = df[df["exchange"].isin(["NYSE", "NYSE American"])]

    df = df.copy()
    df["ticker"] = (
        df["ticker"].astype(str).str.strip().str.upper().str.replace(".", "-", regex=False)
    )
    df = df.dropna(subset=["ticker"]).drop_duplicates("ticker").reset_index(drop=True)

    if "sector" not in df.columns:
        df["sector"] = "Unknown"
    if "name" not in df.columns:
        df["name"] = df["ticker"]
    if "exchange" not in df.columns:
        df["exchange"] = "—"

    return df[["ticker", "name", "sector", "exchange"]]
