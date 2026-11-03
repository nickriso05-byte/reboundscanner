"""
Rebound Scan - washout / reclaim scanner with a live-tunable backtest.

Laid out like the Trading Desk Scan: rules in sidebar cards, one tab per list,
and a results table whose names chart directly beneath it.

    streamlit run app.py
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import chartpanel
import data
import engine
import gate
import livequotes
import theme
from engine import Rules

_HERE = Path(__file__).parent

st.set_page_config(page_title="Rebound Scan", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")


# -- theme -------------------------------------------------------------------

_EXTRA_CSS = """
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:6px 0 14px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;box-shadow:0 1px 2px var(--shadow)}
.kpi .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.kpi .v{font-size:22px;font-weight:700;color:var(--ink);margin-top:2px}
.kpi .s{font-size:12px;color:var(--muted)}
.kpi .v.pos{color:var(--up)} .kpi .v.neg{color:var(--down)}
.rules{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--brand);border-radius:8px;padding:10px 14px;margin:4px 0 14px;font-size:13px;line-height:1.7;color:var(--ink-2)}
.rules b{color:var(--ink)}
@media (max-width:900px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""


def _inject_theme(dark: bool) -> None:
    # Blank lines would end the HTML block and dump the CSS onto the page.
    with open(_HERE / "styles.css", encoding="utf-8") as fh:
        css = "".join(line for line in fh if line.strip())
    extra = "".join(_EXTRA_CSS.splitlines())
    st.markdown(f"{theme.FONT_LINK}<style>{theme.css_vars(dark)}{css}{extra}</style>",
                unsafe_allow_html=True)


dark_mode = False
_inject_theme(dark_mode)

# Nothing below runs - no data, no live feed, no API calls - until unlocked.
gate.require()


# -- data --------------------------------------------------------------------

@st.cache_resource(show_spinner="Building indicators for the universe…")
def market(stamp: float):
    long, pool, _ = data.load()
    if long.empty:
        return None, long
    return engine.build_market(long, pool), long


@st.cache_data(show_spinner=False, max_entries=24)
def run(stamp: float, rules: tuple):
    m, _ = market(stamp)
    r = Rules(**dict(rules))
    fresh, _, _ = engine.signals(m, r)
    return engine.backtest(m, r, fresh)


@st.cache_resource
def _refresh_state() -> dict:
    return {"lock": threading.Lock(), "failed_at": 0.0}


RETRY_AFTER_S = 30 * 60   # after a failed automatic refresh


stamp = data.stamp()
meta = data.load_meta()


# -- sidebar -----------------------------------------------------------------

def _card(title: str):
    st.markdown(f'<div class="side-title">{title}</div>', unsafe_allow_html=True)
    return st.container(border=True)


def _note(text: str) -> None:
    st.markdown(f'<div class="side-note">{text}</div>', unsafe_allow_html=True)


S = st.session_state
S.setdefault("rsi_level", 30)
S.setdefault("drop_pct", 25)
S.setdefault("vol_mult", 1.5)
S.setdefault("breakout_days", 10)
S.setdefault("market_etf", "IWM")
S.setdefault("within", 1)

with st.sidebar:
    with _card("Buy rules"):
        _note(f"Always required: a <b>washout</b> — RSI under {S.rsi_level} or "
              f"{S.drop_pct}% off its high in the last 20 sessions.")
        use_ema = st.checkbox("Closes above 20-day EMA", True)
        use_breakout = st.checkbox(f"Closes above {S.breakout_days}-day high", True)
        use_volume = st.checkbox(f"Volume {S.vol_mult:g}× its 20-day average", True)
        use_market = st.checkbox(f"Market uptrend ({S.market_etf} > 200-day)", True)
        use_macd = st.checkbox("MACD above its signal line", False)
        use_sma200 = st.checkbox("Above its 200-day SMA", False)
        use_sma50 = st.checkbox("Above its 50-day SMA", False)
        use_sector = st.checkbox("Stronger than its sector ETF", False)
        n_checks = sum([use_ema, use_breakout, use_volume, use_market, use_macd,
                        use_sma200, use_sma50, use_sector])
        need = (st.slider("Must pass", 1, n_checks, n_checks, format=f"%d of {n_checks}")
                if n_checks > 1 else n_checks)

    with _card("Sell rules"):
        exit_macd = st.checkbox("MACD signal line turns down", True)
        exit_ema = st.checkbox("Closes below 20-day EMA", False)
        exit_sma50 = st.checkbox("Closes below 50-day SMA", False)
        c1, c2 = st.columns(2)
        stop = c1.number_input("Stop %", 0, 50, 0, help="0 = off")
        target = c2.number_input("Target %", 0, 300, 0, help="0 = off")
        max_hold = st.number_input("Max hold (days)", 5, 500, 120, 5)

    with _card("Screen"):
        c1, c2 = st.columns(2)
        min_price = c1.number_input("Min $", 1, 100, 5)
        min_dv = c2.number_input("Min $ vol (M)", 1, 500, 10)

    with _card("Data"):
        if meta:
            _note(f"{meta['names']:,} stocks over $1B. Prices through <b>{meta['last_bar']}</b>.")
        refresh = st.button("Refresh prices", use_container_width=True)


# -- masthead ----------------------------------------------------------------

st.markdown('<h2 style="margin:0">Rebound Scan</h2>'
            '<div class="lead" style="margin-bottom:10px">Finds stocks that <b>dropped hard and are '
            'bouncing back</b>. Buy when the bounce is confirmed, sell when the momentum fades.</div>',
            unsafe_allow_html=True)

# Refreshes on request, and by itself once a day (or when there is no data at
# all, as on a fresh Cloud start). One refresh at a time; other visitors wait.
_rs = _refresh_state()
_auto = data.refresh_due() and (not stamp or time.time() - _rs["failed_at"] > RETRY_AFTER_S)
if refresh or _auto:
    lock = _rs["lock"]
    if not lock.acquire(blocking=False):
        if not stamp:
            st.info("Loading market data for the first time (about two minutes). "
                    "This page updates itself.")
            time.sleep(5)
            st.rerun()
    else:
        try:
            bar = st.progress(0.0, "Loading the universe…")
            data.refresh(lambda f, d, t, ok: bar.progress(f, f"Loading {d:,} of {t:,}"))
        except Exception as exc:  # noqa: BLE001
            _rs["failed_at"] = time.time()
            lock.release()
            if not stamp:
                st.error(f"Could not load market data: {exc}")
                st.stop()
            st.warning(f"Refresh failed, showing the last saved prices: {exc}")
        else:
            lock.release()
            market.clear()
            run.clear()
            st.rerun()

m, long_bars = market(stamp) if stamp else (None, None)
if m is None:
    st.error("No market data. Check the UW_API_TOKEN secret, then press Refresh prices.")
    st.stop()


# -- fine-tuning (numbers behind the rules) -----------------------------------

with st.expander("Fine-tune the numbers behind the rules"):
    c1, c2, c3, c4 = st.columns(4)
    rsi_level = c1.slider("Washout: RSI under", 15, 45, key="rsi_level")
    drop_pct = c2.slider("…or down % from high", 10, 60, step=5, key="drop_pct")
    drop_lb = c3.number_input("High of last N days", 20, 252, 60, 10)
    setup_window = c4.slider("Washout within N sessions", 5, 60, 20)
    c1, c2, c3, c4 = st.columns(4)
    breakout_days = c1.number_input("Breakout: N-day high", 3, 50, key="breakout_days")
    breakout_on = c2.selectbox("High of", ["highs", "closes"])
    vol_mult = c3.slider("Volume multiple", 1.0, 4.0, step=0.1, key="vol_mult")
    market_etf = c4.selectbox("Market ETF", ["IWM", "SPY", "QQQ"], key="market_etf")
    c1, c2, c3, c4 = st.columns(4)
    sector_metric = c1.selectbox("Sector strength on", ["RSI", "20-day return"])
    grace = c2.number_input("Ignore sell signals first N days", 0, 30, 0)
    fill = c3.selectbox("Trade at", ["next open", "signal close"])
    cost = c4.number_input("Cost per side %", 0.0, 2.0, 0.10, 0.05)

rules = Rules(
    rsi_level=rsi_level, drop_pct=drop_pct, drop_lookback=int(drop_lb), setup_window=setup_window,
    use_ema=use_ema, use_breakout=use_breakout, breakout_days=int(breakout_days),
    breakout_on=breakout_on, use_volume=use_volume, vol_mult=vol_mult,
    use_macd=use_macd, use_sma200=use_sma200, use_sma50=use_sma50,
    use_market=use_market, market_etf=market_etf, use_sector=use_sector,
    sector_metric=sector_metric, min_score=None if need >= n_checks else need,
    min_price=min_price, min_dollar_vol=min_dv * 1e6,
    exit_macd=exit_macd, exit_ema=exit_ema, exit_sma50=exit_sma50,
    stop_pct=stop or None, target_pct=target or None,
    max_hold=int(max_hold), grace_days=int(grace), fill=fill, cost_pct=cost,
)

trades = run(stamp, tuple(asdict(rules).items()))
last_c = m.c.iloc[-1]
rsi_now = m.ind["rsi"].iloc[-1]


# -- plain-English rule summary ----------------------------------------------

buy_bits = [label for on, label in (
    (use_ema, "closes above its 20-day EMA"),
    (use_breakout, f"closes above its {breakout_days}-day high"),
    (use_volume, f"trades {vol_mult:g}× normal volume"),
    (use_market, f"{market_etf} is above its 200-day"),
    (use_macd, "MACD is above its signal line"),
    (use_sma200, "it's above its 200-day SMA"),
    (use_sma50, "it's above its 50-day SMA"),
    (use_sector, "it's stronger than its sector ETF"),
) if on]
sell_bits = [label for on, label in (
    (exit_macd, "the MACD signal line turns down"),
    (exit_ema, "it closes below the 20-day EMA"),
    (exit_sma50, "it closes below the 50-day SMA"),
    (bool(stop), f"it falls {stop}%"),
    (bool(target), f"it gains {target}%"),
) if on] + [f"{max_hold} days pass"]
qual = "" if need >= n_checks else f" <b>{need} of these {n_checks}</b>:"
st.markdown(
    f'<div class="rules"><b>Buy</b> after a washout (RSI under {rsi_level} or {drop_pct}% off its high) '
    f'when{qual} {", ".join(buy_bits) or "—"}.<br>'
    f'<b>Sell</b> when {" or ".join(sell_bits)}.</div>', unsafe_allow_html=True)


# -- panel -------------------------------------------------------------------

@st.cache_data(show_spinner=False, max_entries=12)
def _panel_html(_df: pd.DataFrame, df_key: int, stamp: float, dark: bool, key: str):
    return chartpanel.build_panel(_df, long_bars, table_rows=18, chart_height=420,
                                  dark=dark, panel_id=key, open_first=False)


def results_panel(df: pd.DataFrame, key: str, empty: str) -> None:
    if df.empty:
        st.markdown(f'<div class="empty">{empty}</div>', unsafe_allow_html=True)
        return
    df_key = int(pd.util.hash_pandas_object(df, index=True).sum()) ^ hash(tuple(df.columns))
    html, height = _panel_html(df, df_key, stamp, dark_mode, key)
    components.html(html, height=height, scrolling=False)


def _date(s) -> pd.Series:
    return pd.to_datetime(pd.Series(s)).dt.strftime("%b %d, %Y")


# -- tabs --------------------------------------------------------------------

open_t = trades[trades["open"]] if not trades.empty else trades
held = open_t[open_t["reason"] == "open"] if not open_t.empty else open_t
asof = m.dates[-1]

within = S["within"]
cutoff = m.dates[max(0, len(m.dates) - within)]
recent = trades[(trades["signal"] >= cutoff) & trades["open"]] if not trades.empty else trades

tab_buy, tab_hold, tab_bt = st.tabs(["Buy signals", "Holding", "Backtest"])

with tab_buy:
    when = f"on the latest close ({asof:%b %d})" if within == 1 else f"in the last {within} trading days"
    st.markdown(f'<div class="lead"><b>What to buy.</b> These {len(recent)} stocks hit every buy rule {when}. '
                'Under the system you buy them at the next open. Click a name to see its chart.</div>',
                unsafe_allow_html=True)
    with st.expander("Show more days"):
        st.slider("Signals from the last N trading days", 1, 20, key="within")
    if not recent.empty:
        sig_close = [m.c.at[s, t] for s, t in zip(recent["signal"], recent["ticker"])]
        now = recent["ticker"].map(last_c).to_numpy()
        out = pd.DataFrame({
            "Ticker": recent["ticker"].values,
            "Name": recent["name"].values,
            "Sector": recent["sector"].values,
            "Signal": _date(recent["signal"].values).values,
            "Price": now,
            "Since signal %": (now / sig_close - 1) * 100,
            "RSI": recent["ticker"].map(rsi_now).values,
        }).iloc[::-1].reset_index(drop=True)
        if within == 1:
            out = out.drop(columns="Since signal %")
    else:
        out = pd.DataFrame()
    results_panel(out, "buy", "No signals in this window. Widen it under Filters, "
                              "or loosen the buy rules in the sidebar.")

with tab_hold:
    st.markdown('<div class="lead"><b>What you would own right now.</b> If you had taken every buy '
                f'signal, these are the {len(held)} stocks you would still be holding today: bought on a signal, '
                f'no sell rule hit yet. Gain % is from the buy price to the {asof:%b %d} close.</div>',
                unsafe_allow_html=True)
    if held.empty:
        out = pd.DataFrame()
    else:
        h = held.sort_values("ret", ascending=False)
        out = pd.DataFrame({
            "Ticker": h["ticker"].values, "Name": h["name"].values, "Sector": h["sector"].values,
            "Entered": _date(h["entry_date"].values).values, "Entry": h["entry"].values,
            "Price": h["exit"].values, "Gain %": h["ret"].values * 100,
            "Days": h["days"].values,
        })
    results_panel(out, "hold", "Nothing open right now.")

# -- backtest ----------------------------------------------------------------

def _pct(x, signed=True):
    return "—" if x is None or pd.isna(x) else (f"{x * 100:+.1f}%" if signed else f"{x * 100:.0f}%")


def _kpi(label, value, sub="", tone=""):
    return (f'<div class="kpi"><div class="k">{label}</div>'
            f'<div class="v {tone}">{value}</div><div class="s">{sub}</div></div>')


def trade_chart(ticker, entry, exit_):
    lo = max(0, m.dates.get_loc(entry) - 90)
    hi = len(m.dates) if exit_ is None else min(len(m.dates), m.dates.get_loc(exit_) + 40)
    d, sl = m.dates[lo:hi], slice(lo, hi)
    tk = theme.tokens(dark_mode)
    fig = go.Figure()
    fig.add_candlestick(x=d, open=m.o[ticker].iloc[sl], high=m.h[ticker].iloc[sl],
                        low=m.l[ticker].iloc[sl], close=m.c[ticker].iloc[sl], name=ticker,
                        increasing_line_color=tk["up"], decreasing_line_color=tk["down"])
    for key, name, color in (("ema20", "20 EMA", tk["brand"]), ("sma50", "50 SMA", tk["accent"])):
        fig.add_scatter(x=d, y=m.ind[key][ticker].iloc[sl], name=name, line=dict(width=1.3, color=color))
    for when, label, color in ((entry, "Buy", tk["up"]), (exit_, "Sell", tk["down"])):
        if when is not None:
            fig.add_vline(x=when, line=dict(color=color, dash="dot"))
            fig.add_annotation(x=when, y=1, yref="paper", text=label, showarrow=False,
                               font=dict(color=color))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=36, b=10),
                      paper_bgcolor=tk["panel"], plot_bgcolor=tk["panel"],
                      font=dict(color=tk["axis"]), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.1), title=f"{ticker} · {m.names.get(ticker, '')}")
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], gridcolor=tk["grid"])
    fig.update_yaxes(gridcolor=tk["grid"])
    st.plotly_chart(fig, use_container_width=True)


with tab_bt:
    span = f"{trades['signal'].min():%b %Y} to {asof:%b %d, %Y}" if not trades.empty else ""
    st.markdown(f'<div class="lead"><b>How it would have done.</b> This is the full test: every buy '
                f'signal across all {len(m.tickers):,} stocks from {span}, bought and sold exactly '
                'by the rules, 0.1% cost each way. It takes about a second because it only uses '
                'daily closing prices already saved on this computer. <b>SPY</b> is what SPY did over '
                'the same days each trade was held.</div>', unsafe_allow_html=True)
    if trades.empty:
        st.markdown('<div class="empty">These rules produced no trades.</div>', unsafe_allow_html=True)
    else:
        with st.expander("Pick a date range"):
            lo_d, hi_d = m.dates[0].date(), m.dates[-1].date()
            rng = st.slider("Signal dates", lo_d, hi_d, (lo_d, hi_d), format="MMM YYYY")
        t = trades[(trades["signal"].dt.date >= rng[0]) & (trades["signal"].dt.date <= rng[1])]
        s = engine.summary(t)
        if not s["trades"]:
            st.markdown('<div class="empty">No closed trades in this window.</div>', unsafe_allow_html=True)
        else:
            edge = s["avg"] - s["spy"]
            st.markdown('<div class="kpis">' + "".join([
                _kpi("Trades", f"{s['trades']:,}", f"{int((t['reason'] == 'open').sum())} still open"),
                _kpi("Win rate", _pct(s["win"], False), f"avg win {_pct(s['avg_win'])}"),
                _kpi("Avg trade", _pct(s["avg"]), f"median {_pct(s['median'])}",
                     "pos" if s["avg"] > 0 else "neg"),
                _kpi("SPY, same days", _pct(s["spy"]), f"system {'+' if edge >= 0 else '−'}"
                     f"{abs(edge) * 100:.1f} pts", "pos" if edge > 0 else "neg"),
                _kpi("Profit factor", f"{s['pf']:.2f}", f"avg loss {_pct(s['avg_loss'])}"),
                _kpi("Avg hold", f"{s['days']:.0f} days", f"best {_pct(s['best'])}"),
            ]) + "</div>", unsafe_allow_html=True)

            eq = engine.equity(t)
            if not eq.empty:
                tk = theme.tokens(dark_mode)
                st.markdown("**Running P&L, \\$1,000 into every trade**")
                st.line_chart(eq, height=280, color=[tk["brand"], tk["muted"]])

            st.markdown("**By year**")
            st.dataframe(engine.by_period(t, "Y").round(1), use_container_width=True)

            with st.expander("All trades"):
                tv = t.sort_values("signal", ascending=False)
                cols = ["ticker", "name", "sector", "signal", "entry_date", "entry",
                        "exit_date", "exit", "reason", "days", "ret", "spy"]
                st.download_button("Download CSV", tv[cols].to_csv(index=False),
                                   "rebound_scan_trades.csv", "text/csv")
                ev = st.dataframe(tv[cols], hide_index=True, use_container_width=True, height=360,
                                  on_select="rerun", selection_mode="single-row", key="trades_tbl",
                                  column_config={
                                      "ret": st.column_config.NumberColumn("return", format="percent"),
                                      "spy": st.column_config.NumberColumn("SPY", format="percent"),
                                      "entry": st.column_config.NumberColumn(format="$%.2f"),
                                      "exit": st.column_config.NumberColumn(format="$%.2f"),
                                      "signal": st.column_config.DateColumn(),
                                      "entry_date": st.column_config.DateColumn("bought"),
                                      "exit_date": st.column_config.DateColumn("sold")})
                rows = ev.selection.rows if ev and ev.selection else []
                if rows:
                    r_ = tv.iloc[rows[0]]
                    if pd.notna(r_["entry_date"]):
                        trade_chart(r_["ticker"], r_["entry_date"],
                                    None if r_["reason"] == "open" else r_["exit_date"])
                else:
                    st.caption("Click a row to chart that trade.")

            with st.expander("More breakdowns"):
                closed = t[t["reason"] != "open"]
                c1, c2 = st.columns(2)
                for col, key, title in ((c1, "reason", "By sell reason"), (c2, "sector", "By sector")):
                    g = closed.groupby(key)["ret"]
                    col.markdown(f"**{title}**")
                    col.dataframe(pd.DataFrame({
                        "Trades": g.size(), "Win %": g.apply(lambda x: (x > 0).mean() * 100),
                        "Avg %": g.mean() * 100}).sort_values("Trades", ascending=False).round(1),
                        use_container_width=True)
                st.markdown("**By month**")
                st.dataframe(engine.by_period(t, "M").round(1), use_container_width=True, height=300)
                st.caption("Universe is today's \\$1B+ list, so delisted names are missing "
                           "(survivorship bias flatters results).")


# -- live prices + intraday candles -------------------------------------------

# The charts' 1m/5m ranges ask this feed for candles (and it prices the rows
# during the session). Same feed as the Trading Desk Scan; only it reruns on
# its timer, so the tables and charts keep their state.
_live = set()
for _df in (recent, held):
    if not _df.empty:
        _live.update(_df["ticker"].head(chartpanel.MAX_EMBED).astype(str))
livequotes.mount(_live)
