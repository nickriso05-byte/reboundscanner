"""
Universe and daily bars for the scanner.

The universe is every liquid US common stock / ADR above a market-cap floor,
pulled from the UW screener, plus the reference ETFs the filters compare
against (SPY, IWM and the eleven State Street sector SPDRs). Daily history is
fetched once per refresh with UW's 10Y timeframe (~800-1,100 sessions per
name) and stored as one long parquet.

Run `python data.py` to refresh from the command line.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import uw

DATA_DIR = Path(__file__).parent / ".data"
DATA_DIR.mkdir(exist_ok=True)
BARS_PATH = DATA_DIR / "bars.parquet"
POOL_PATH = DATA_DIR / "pool.parquet"
META_PATH = DATA_DIR / "meta.json"

MIN_MARKETCAP = 1e9
MAX_NAMES = 2000

# UW sector name -> State Street sector SPDR.
SECTOR_ETF = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}
REFERENCE = ["SPY", "IWM", "QQQ", *SECTOR_ETF.values()]

ET = ZoneInfo("America/New_York")
# Refresh once a day, on the first visit at or after this hour Eastern:
# after the previous session has settled, before the next one opens.
REFRESH_HOUR = 5


def refresh(on_progress=None) -> dict:
    """Rebuild the pool and every name's daily history. Returns the meta."""
    t0 = time.time()
    pool = uw.fetch_pool(min_marketcap=MIN_MARKETCAP, max_names=MAX_NAMES)
    if pool.empty:
        raise uw.UWError("The UW screener returned no names.")
    pool = pool[~pool["ticker"].isin(REFERENCE)]
    tickers = tuple(dict.fromkeys([*REFERENCE, *pool["ticker"]]))

    bars = uw.fetch_many_ohlc(tickers, "10Y", on_progress=on_progress)
    missing = [t for t in ("SPY", "IWM") if t not in bars]
    if missing:
        raise uw.UWError(f"No history for {', '.join(missing)}.")

    frames = []
    for t, df in bars.items():
        df = df.copy()
        df["ticker"] = t
        frames.append(df.reset_index())
    long = pd.concat(frames, ignore_index=True)
    long.to_parquet(BARS_PATH, index=False)
    pool.to_parquet(POOL_PATH, index=False)

    meta = {
        "refreshed": datetime.now(ET).replace(tzinfo=None).isoformat(timespec="seconds"),
        "names": int(len(bars)),
        "last_bar": str(long["Date"].max().date()),
        "first_bar": str(long["Date"].min().date()),
        "seconds": round(time.time() - t0, 1),
    }
    META_PATH.write_text(json.dumps(meta, indent=2))
    return meta


def load() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """(long bars, pool, meta) from disk, or empty frames if never refreshed."""
    if not BARS_PATH.exists():
        return pd.DataFrame(), pd.DataFrame(), {}
    meta = json.loads(META_PATH.read_text()) if META_PATH.exists() else {}
    return pd.read_parquet(BARS_PATH), pd.read_parquet(POOL_PATH), meta


def load_meta() -> dict:
    return json.loads(META_PATH.read_text()) if META_PATH.exists() else {}


def refresh_due() -> bool:
    """No data yet, or today's (Eastern) refresh hasn't happened."""
    meta = load_meta()
    if not BARS_PATH.exists() or not meta.get("refreshed"):
        return True
    now = datetime.now(ET)
    last = datetime.fromisoformat(meta["refreshed"])
    return now.hour >= REFRESH_HOUR and last.date() < now.date()


def stamp() -> float:
    """Cache key: changes whenever the bars file is rewritten."""
    return BARS_PATH.stat().st_mtime if BARS_PATH.exists() else 0.0


if __name__ == "__main__":
    def _p(frac, done, total, ok):
        if done % 100 == 0 or done == total:
            print(f"{done}/{total} fetched ({ok} with data)", flush=True)
    print(json.dumps(refresh(_p), indent=2))
    sys.exit(0)
