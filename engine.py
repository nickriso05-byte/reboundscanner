"""
Signal engine and backtester.

Everything works on wide frames (dates x tickers) so each indicator is computed
once for the whole universe, and toggling a filter is a boolean operation, not
a recompute.

The system, in plain terms:

  SETUP     the stock washed out recently: RSI(14) under 30, or down 25%+
            from its recent high, at some point in the last N sessions.
  TRIGGER   it reclaims: closes above its 20-day EMA and above its prior
            10-day high, on volume at least 1.5x its 20-day average.
  FILTERS   optional checks (MACD, 200/50-day SMA, market, sector strength).
            Either all checked filters must pass, or at least K of them.
  EXIT      first of: MACD turns down, close below the 20-day EMA, stop,
            target, max hold.

A signal is the first day the whole rule set is true (the day before it was
not). One position per name: a new signal while already holding is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from data import SECTOR_ETF


# -- indicators --------------------------------------------------------------

def _wilder_rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _ema(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.ewm(span=n, adjust=False, min_periods=n).mean()


@dataclass
class Market:
    """Wide price frames plus every indicator the rules read."""
    dates: pd.DatetimeIndex
    tickers: list[str]
    o: pd.DataFrame
    h: pd.DataFrame
    l: pd.DataFrame
    c: pd.DataFrame
    v: pd.DataFrame
    ind: dict[str, pd.DataFrame] = field(default_factory=dict)
    sector: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)


def build_market(long: pd.DataFrame, pool: pd.DataFrame) -> Market:
    long = long.copy()
    long["Date"] = pd.to_datetime(long["Date"])
    wide = {col: long.pivot(index="Date", columns="ticker", values=col)
            for col in ("Open", "High", "Low", "Close", "Volume")}
    # SPY's calendar is the trading calendar; drop stray non-session rows.
    dates = wide["Close"]["SPY"].dropna().index
    for k in wide:
        wide[k] = wide[k].reindex(dates)
    c = wide["Close"]
    m = Market(dates=dates, tickers=list(c.columns), o=wide["Open"], h=wide["High"],
               l=wide["Low"], c=c, v=wide["Volume"])

    macd = _ema(c, 12) - _ema(c, 26)
    sig = _ema(macd, 9)
    m.ind = {
        "rsi": _wilder_rsi(c),
        "ema20": _ema(c, 20),
        "sma50": c.rolling(50, min_periods=50).mean(),
        "sma200": c.rolling(200, min_periods=200).mean(),
        "macd": macd,
        "macd_sig": sig,
        # Prior sessions only: "today beat its average", "today beat the high".
        "vol20": m.v.shift(1).rolling(20, min_periods=20).mean(),
        "hi10_high": m.h.shift(1).rolling(10, min_periods=10).max(),
        "hi10_close": c.shift(1).rolling(10, min_periods=10).max(),
    }
    if not pool.empty:
        m.sector = dict(zip(pool["ticker"], pool.get("sector", pd.Series(dtype=str))))
        if "name" in pool:
            m.names = dict(zip(pool["ticker"], pool["name"].fillna("")))
    return m


# -- rules -------------------------------------------------------------------

@dataclass
class Rules:
    # setup
    use_rsi: bool = True
    rsi_level: float = 30
    use_drop: bool = True
    drop_pct: float = 25
    drop_lookback: int = 60
    setup_window: int = 20
    # trigger
    use_ema: bool = True
    use_breakout: bool = True
    breakout_days: int = 10
    breakout_on: str = "highs"          # "highs" or "closes"
    use_volume: bool = True
    vol_mult: float = 1.5
    # optional filters
    use_macd: bool = False
    use_sma200: bool = False
    use_sma50: bool = False
    use_market: bool = True
    market_etf: str = "IWM"
    use_sector: bool = False
    sector_metric: str = "RSI"          # "RSI" or "20-day return"
    min_score: int | None = None        # None = every checked item must pass
    # universe
    min_price: float = 5
    min_dollar_vol: float = 10e6
    # exits
    exit_macd: bool = True
    exit_ema: bool = False
    exit_sma50: bool = False
    stop_pct: float | None = None
    target_pct: float | None = None
    max_hold: int = 120
    grace_days: int = 0
    # execution
    fill: str = "next open"             # "next open" or "signal close"
    cost_pct: float = 0.10              # per side


def _bcast(series: pd.Series, like: pd.DataFrame) -> pd.DataFrame:
    """A per-date series spread across every column."""
    return pd.DataFrame(np.repeat(series.to_numpy()[:, None], like.shape[1], axis=1),
                        index=like.index, columns=like.columns)


def checks(m: Market, r: Rules) -> dict[str, pd.DataFrame]:
    """Each rule component as a boolean frame. Keys are display labels."""
    c, ind = m.c, m.ind
    out: dict[str, pd.DataFrame] = {}

    wash = pd.DataFrame(False, index=c.index, columns=c.columns)
    if r.use_rsi:
        wash |= ind["rsi"] < r.rsi_level
    if r.use_drop:
        peak = c.rolling(r.drop_lookback, min_periods=20).max()
        wash |= c <= peak * (1 - r.drop_pct / 100)
    out["Washout"] = wash.astype(float).rolling(r.setup_window, min_periods=1).max() > 0

    if r.use_ema:
        out["Above 20 EMA"] = c > ind["ema20"]
    if r.use_breakout:
        if r.breakout_days == 10:
            hi = ind["hi10_high" if r.breakout_on == "highs" else "hi10_close"]
        else:
            src = m.h if r.breakout_on == "highs" else c
            hi = src.shift(1).rolling(r.breakout_days, min_periods=r.breakout_days).max()
        out[f"{r.breakout_days}-day high"] = c > hi
    if r.use_volume:
        out["Volume spike"] = m.v >= ind["vol20"] * r.vol_mult

    if r.use_macd:
        out["MACD > signal"] = ind["macd"] > ind["macd_sig"]
    if r.use_sma200:
        out["Above 200 SMA"] = c > ind["sma200"]
    if r.use_sma50:
        out["Above 50 SMA"] = c > ind["sma50"]
    if r.use_market:
        etf = r.market_etf
        ok = c[etf] > ind["sma200"][etf]
        out[f"{etf} > 200 SMA"] = _bcast(ok, c)
    if r.use_sector:
        out["Beats sector ETF"] = _sector_check(m, r)
    return out


def _sector_check(m: Market, r: Rules) -> pd.DataFrame:
    c = m.c
    metric = m.ind["rsi"] if r.sector_metric == "RSI" else c / c.shift(20) - 1
    etfs = [SECTOR_ETF.get(m.sector.get(t, "")) for t in c.columns]
    known = np.array([e is not None and e in metric for e in etfs])
    bench = metric.reindex(columns=[e if k else "SPY" for e, k in zip(etfs, known)])
    res = metric.to_numpy() > bench.to_numpy()
    res[:, ~known] = False   # no sector on file: the check can't pass
    return pd.DataFrame(res, index=c.index, columns=c.columns)


# The setup and trigger are the core; anything else is a "filter".
CORE = {"Washout"}


def tradeable(m: Market, r: Rules) -> pd.DataFrame:
    dv = (m.c * m.v).rolling(20, min_periods=20).mean()
    ok = (m.c >= r.min_price) & (dv >= r.min_dollar_vol)
    from data import REFERENCE
    ref = [t for t in REFERENCE if t in ok.columns]
    ok[ref] = False
    return ok


def combine(parts: dict[str, pd.DataFrame], r: Rules) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(rule true today, score). Washout is always required."""
    others = [k for k in parts if k not in CORE]
    score = sum(parts[k].astype(int) for k in others) if others else None
    if score is None:
        hit = parts["Washout"].copy()
        score = pd.DataFrame(0, index=hit.index, columns=hit.columns)
    else:
        need = len(others) if r.min_score is None else min(r.min_score, len(others))
        hit = parts["Washout"] & (score >= need)
    return hit, score


def signals(m: Market, r: Rules) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    parts = checks(m, r)
    hit, score = combine(parts, r)
    hit &= tradeable(m, r)
    fresh = hit & ~hit.shift(1, fill_value=False)
    return fresh, parts, score


def exit_flags(m: Market, r: Rules) -> pd.DataFrame:
    """Close-based exit signals (stop/target/max-hold are handled per trade)."""
    c, ind = m.c, m.ind
    x = pd.DataFrame(False, index=c.index, columns=c.columns)
    if r.exit_macd:
        # The signal line "turns south": today's value below yesterday's.
        x |= ind["macd_sig"] < ind["macd_sig"].shift(1)
    if r.exit_ema:
        x |= c < ind["ema20"]
    if r.exit_sma50:
        x |= c < ind["sma50"]
    return x


# -- backtest ----------------------------------------------------------------

def backtest(m: Market, r: Rules, fresh: pd.DataFrame) -> pd.DataFrame:
    """One row per trade. Open trades are marked to the last close."""
    O, H, L, C = (f.to_numpy(dtype="float64") for f in (m.o, m.h, m.l, m.c))
    X = exit_flags(m, r).to_numpy()
    S = fresh.to_numpy()
    spy_o = m.o["SPY"].to_numpy(dtype="float64")
    spy_c = m.c["SPY"].to_numpy(dtype="float64")
    T = len(m.dates)
    cost = r.cost_pct / 100
    next_open = r.fill == "next open"
    rows = []

    for j in np.flatnonzero(S.any(axis=0)):
        busy_until = -1
        for i in np.flatnonzero(S[:, j]):
            if i <= busy_until:
                continue
            e = i + 1 if next_open else i
            if e >= T:
                rows.append(_pending(m, j, i))
                break
            entry = O[e, j] if next_open else C[e, j]
            if not np.isfinite(entry) or entry <= 0:
                continue
            first = e if next_open else e + 1        # first bar exits can fire
            end = min(T, e + r.max_hold)              # bars [first, end)
            k, px, why = _find_exit(O[:, j], H[:, j], L[:, j], C[:, j], X[:, j],
                                    entry, first, end, e, r, T)
            spy_in = spy_o[e] if next_open else spy_c[e]
            gross = px / entry - 1
            seg_l = L[e:k + 1, j]
            rows.append({
                "ticker": m.tickers[j],
                "signal": m.dates[i],
                "entry_date": m.dates[e],
                "entry": entry,
                "exit_date": m.dates[k],
                "exit": px,
                "reason": why,
                "days": k - e + 1,
                "ret": (1 + gross) * (1 - cost) / (1 + cost) - 1,
                "spy": spy_c[k] / spy_in - 1,
                "worst": np.nanmin(seg_l) / entry - 1 if seg_l.size else np.nan,
                "open": why == "open",
            })
            busy_until = k
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["sector"] = df["ticker"].map(m.sector).fillna("")
    df["name"] = df["ticker"].map(m.names).fillna("")
    return df.sort_values("entry_date").reset_index(drop=True)


def _pending(m: Market, j: int, i: int) -> dict:
    """A signal on the last bar: entry is tomorrow's open."""
    return {"ticker": m.tickers[j], "signal": m.dates[i], "entry_date": pd.NaT,
            "entry": np.nan, "exit_date": pd.NaT, "exit": np.nan,
            "reason": "enter next open", "days": 0, "ret": np.nan, "spy": np.nan,
            "worst": np.nan, "open": True}


def _find_exit(o, h, l, c, x, entry, first, end, e, r: Rules, T):
    """First exit in bars [first, end). Returns (bar index, fill price, reason)."""
    stop = entry * (1 - r.stop_pct / 100) if r.stop_pct else None
    tgt = entry * (1 + r.target_pct / 100) if r.target_pct else None
    for k in range(first, end):
        # Intraday levels first; a bar touching both is scored as the stop.
        if stop is not None and l[k] <= stop:
            return k, min(o[k], stop) if k > e else stop, "stop"
        if tgt is not None and h[k] >= tgt:
            return k, max(o[k], tgt) if k > e else tgt, "target"
        if x[k] and k >= e + r.grace_days:
            if r.fill == "next open" and k + 1 < T and np.isfinite(o[k + 1]):
                return k + 1, o[k + 1], "signal"
            return k, c[k], "signal"
    last = end - 1
    if end >= T:
        # Ran out of data: still holding.
        valid = np.flatnonzero(np.isfinite(c[:T]))
        last = valid[-1] if valid.size else T - 1
        return last, c[last], "open"
    return last, c[last], "max hold"


# -- summaries ---------------------------------------------------------------

def summary(trades: pd.DataFrame) -> dict:
    t = trades[trades["reason"] != "open"].dropna(subset=["ret"]) if not trades.empty else trades
    if t.empty:
        return {"trades": 0}
    wins, losses = t[t["ret"] > 0], t[t["ret"] <= 0]
    pf = wins["ret"].sum() / -losses["ret"].sum() if len(losses) and losses["ret"].sum() < 0 else np.inf
    return {
        "trades": len(t),
        "win": len(wins) / len(t),
        "avg": t["ret"].mean(),
        "median": t["ret"].median(),
        "avg_win": wins["ret"].mean() if len(wins) else np.nan,
        "avg_loss": losses["ret"].mean() if len(losses) else np.nan,
        "pf": pf,
        "days": t["days"].mean(),
        "spy": t["spy"].mean(),
        "beat_spy": (t["ret"] > t["spy"]).mean(),
        "best": t["ret"].max(),
        "worst": t["ret"].min(),
    }


def by_period(trades: pd.DataFrame, freq: str = "Y") -> pd.DataFrame:
    t = trades[trades["reason"] != "open"].dropna(subset=["ret"])
    if t.empty:
        return pd.DataFrame()
    key = t["entry_date"].dt.to_period(freq).astype(str)
    g = t.groupby(key)
    return pd.DataFrame({
        "Trades": g.size(),
        "Win %": g["ret"].apply(lambda s: (s > 0).mean() * 100),
        "Avg %": g["ret"].mean() * 100,
        "Median %": g["ret"].median() * 100,
        "SPY same days %": g["spy"].mean() * 100,
        "Avg days": g["days"].mean(),
    })


def equity(trades: pd.DataFrame, per_trade: float = 1000) -> pd.DataFrame:
    """Running P&L of $per_trade into every closed trade, vs SPY over the same days."""
    t = trades[trades["reason"] != "open"].dropna(subset=["ret"]).sort_values("exit_date")
    if t.empty:
        return pd.DataFrame()
    daily = t.groupby("exit_date")[["ret", "spy"]].sum() * per_trade
    out = daily.cumsum()
    out.columns = ["System", "SPY, same holding days"]
    return out
