"""
Live prices for the names on screen, and today's candles for a charted name.

Without a websocket feed this polls, and it polls the cheapest thing that
answers the question: one Unusual Whales screener call takes a comma-separated
ticker list (up to 500), so every name across all three tabs comes back in a
single request. Measured during market hours: 0.5s round trip, quotes under a
second old on liquid names. The per-ticker quote endpoint was both slower in
aggregate (75 names took 3.8s across 10 workers) and no fresher.

The token never reaches the browser. Nothing here feeds the screens either:
they are defined on daily closes and stay that way. This is a display layer.

Why the vendor is polled off the page's tick
--------------------------------------------
The page ticks on a two-second fragment timer. When that tick fetched the
quotes itself, ticks bunched and stalled - measured gaps of 4 to 13 seconds -
because a tick that waits on the network holds up the next, and a chart
request's rerun cancels a tick in flight. So one background poller fetches the
quotes every two seconds for the union of every viewer's names, a pool fetches
candles, and a tick only reads what they already hold. A tick is then near
instant, the prices keep their pace whatever the charts are doing, and any
number of viewers still cost one call per interval.
"""

from __future__ import annotations

import calendar
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from zoneinfo import ZoneInfo

import streamlit as st


_ET = ZoneInfo("America/New_York")


def _market_now() -> datetime:
    return datetime.now(_ET)


def _today_str() -> str:
    return _market_now().date().isoformat()
_UTC = timezone.utc

POLL_SECONDS = 2          # while the regular session is open
IDLE_SECONDS = 30         # otherwise, only so the page notices the open
MAX_PER_CALL = 500        # the screener's row cap

# A viewer whose tick has not been seen for this long has gone; their names
# stop being polled, so a closed tab stops costing calls.
WATCH_TTL = 12

# A quote whose newest print is older than this, during market hours, means
# the market is not actually trading (a holiday, a halt-wide outage) rather
# than a quiet name.
FEED_STALE_MS = 120_000

# Intraday candles for a charted name: how often they are re-read from the
# vendor while the session is open, and after it. Between re-reads the live
# ticks grow the forming candle in the browser, so this is a re-sync, not the
# refresh rate the chart feels.
INTRADAY_LIVE_TTL = 15
INTRADAY_IDLE_TTL = 600
INTRADAY_SIZES = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}

# How many sessions each chart range shows, and the bar sizes it may ask for.
# Kept to what one or two vendor calls return (2,500 candles each, extended
# hours included): 1-minute bars run out after a couple of days, hourly after
# six months. The chart offers the same combinations.
SPANS = {"1D": 1, "2D": 2, "5D": 5, "1M": 21, "3M": 63, "6M": 126}
SPAN_SIZES = {"1m": ("1D", "2D"), "5m": ("1D", "2D", "5D"),
              "15m": ("1D", "2D", "5D", "1M"), "30m": ("1D", "2D", "5D", "1M"),
              "1h": ("1D", "2D", "5D", "1M", "3M", "6M")}
# Extra sessions fetched ahead of the window - roughly 200 bars' worth - so a
# 200-bar average is complete from the chart's left edge.
WARM = {"1m": 1, "5m": 3, "15m": 8, "30m": 16, "1h": 29}
MAX_BARS = 3000
_PAGES = 4
_OPEN = 9 * 3600 + 30 * 60          # 9:30, as Eastern wall-clock seconds into the day


def sessions_for(size: str, span: str) -> int:
    return SPANS[span] + WARM[size]


def last_sessions(bars: list, n: int) -> list:
    """The bars on the last n trading days present, capped at MAX_BARS."""
    if not bars:
        return bars
    days = sorted({b[0] // 86400 for b in bars})[-n:]
    first = days[0]
    return [b for b in bars if b[0] // 86400 >= first][-MAX_BARS:]


def hourly(bars: list) -> list:
    """Hourly candles that open at 9:30, folded from half-hour ones. The
    vendor's own hourly candles start on the clock hour, so its 9:00 candle
    holds the first half hour of trading under a pre-market label - and
    filtering to regular hours drops the open altogether."""
    out: dict[int, list] = {}
    for t, o, h, lo, c, v in bars:
        day = t - t % 86400
        k = day + _OPEN + ((t - day - _OPEN) // 3600) * 3600
        b = out.get(k)
        if b is None:
            out[k] = [k, o, h, lo, c, v]
        else:
            b[2], b[3], b[4], b[5] = max(b[2], h), min(b[3], lo), c, b[5] + v
    return [out[k] for k in sorted(out)]

FEED_KEY = "tds_live_feed"

# Names typed into the Chart tab. That tab is a fragment, so its names cannot
# reach the set the main script mounts the feed with; they are read from here
# on every tick instead.
EXTRA_KEY = "tds_extra_names"

# A Chart-tab symbol that Unusual Whales does not carry - futures, an index,
# FX, crypto, a non-US listing - priced and charted from Yahoo instead.
YAHOO_KEY = "tds_yahoo_symbols"
YAHOO_TTL = 15

# The page listens for this. Registered once at import - re-registering per
# rerun is what the component docs warn against.
#
# It also carries requests the other way. A chart that switches to 1D asks for
# that name's intraday candles by dispatching `tds-want` on the page; the feed
# merges each chart's request (keyed by its tab, so one tab cannot cancel
# another's) and hands the set to Python as component state, which reruns only
# this fragment.
_FEED_JS = """
export default function(component) {
  const { data, setStateValue } = component;
  window.__tdsSet = setStateValue;            // newest handle, for the listener below
  if (!window.__tdsWantBound) {
    window.__tdsWantBound = true;
    window.__tdsWants = window.__tdsWants || {};
    window.addEventListener('tds-want', function(e) {
      const d = e.detail || {};
      if (!d.panel) return;
      // A copy, not the chart's own object: that belongs to the chart's frame,
      // and a filter change destroys the frame and can leave it unreadable.
      if (d.want) window.__tdsWants[d.panel] = JSON.parse(JSON.stringify(d.want));
      else delete window.__tdsWants[d.panel];
      if (window.__tdsSet) window.__tdsSet('want', Object.assign({}, window.__tdsWants));
    });
  }
  if (!data) return;
  // Kept on the window as well as dispatched, so a table that renders after a
  // tick - a filter change rebuilds it - picks up the latest prices at once.
  window.__tdsQuotes = data;
  window.dispatchEvent(new CustomEvent('tds-quotes', { detail: data }));
}
"""


def _register():
    return st.components.v2.component("tds_live_feed", js=_FEED_JS)


_FEED = _register()


def _mount_feed(data) -> None:
    """Mount the feed, re-registering it if the registry it was put in is gone.
    Streamlit keeps that registry on its Runtime, and with no Runtime running -
    its own test harness, for one - each lookup gets a fresh, empty one."""
    global _FEED
    try:
        _FEED(data=data, key=FEED_KEY, on_want_change=lambda: None)
    except ValueError as exc:
        if "not registered" not in str(exc):
            raise
        _FEED = _register()
        _FEED(data=data, key=FEED_KEY, on_want_change=lambda: None)


def market_open(now=None) -> bool:
    """Regular session, weekdays, Eastern time."""
    now = now or _market_now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None      # drop NaN


# ── quotes: one poller for every viewer ─────────────────────────────────────

_Q_LOCK = threading.Lock()
_SNAPSHOT: dict = {"at": 0.0, "quotes": {}, "err": None}
_WATCH: dict[frozenset, float] = {}          # a viewer's names -> last tick seen
_WAKE = threading.Event()
_POLLER: threading.Thread | None = None


def _fetch_quotes(session, tickers: list[str]) -> dict:
    """{ticker: [last, prev_close, open, high, low, volume, quote_ms]}."""
    import uw   # local, so a Yahoo-only deployment never needs a token

    out: dict[str, list] = {}
    for start in range(0, len(tickers), MAX_PER_CALL):
        chunk = tickers[start:start + MAX_PER_CALL]
        payload = uw._get(session, uw.SCREENER_PATH,
                          {"ticker": ",".join(chunk), "limit": MAX_PER_CALL})
        for row in uw._screener_rows(payload):
            ticker = str(row.get("ticker", "")).upper()
            last = _num(row.get("close"))
            if not ticker or last is None:
                continue
            out[ticker] = [last, _num(row.get("prev_close")), _num(row.get("open")),
                           _num(row.get("high")), _num(row.get("low")),
                           _num(row.get("stock_volume")), _num(row.get("quote_time"))]
    return out


def _poll_loop() -> None:
    import uw

    session = None
    while True:
        started = time.time()
        with _Q_LOCK:
            for key in [k for k, seen in _WATCH.items() if started - seen > WATCH_TTL]:
                del _WATCH[key]
            names = sorted(set().union(*_WATCH)) if _WATCH else []
        if names and market_open():
            try:
                session = session or uw._session(uw.get_token())
                quotes = _fetch_quotes(session, names)
                with _Q_LOCK:
                    _SNAPSHOT.update(at=time.time(), quotes=quotes, err=None)
            except Exception as exc:  # noqa: BLE001 - reported through the tick
                session = None        # a broken connection is rebuilt next time
                with _Q_LOCK:
                    _SNAPSHOT["err"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            wait = max(0.0, POLL_SECONDS - (time.time() - started))
        else:
            wait = 5.0                # nobody watching, or the market is shut
        _WAKE.wait(timeout=wait)
        _WAKE.clear()


def _watch(names: frozenset) -> None:
    """Note a viewer's names, start the poller if needed, and fetch at once
    if these names are new to it rather than leave them blank for a cycle."""
    global _POLLER
    with _Q_LOCK:
        held = set().union(*_WATCH) if _WATCH else set()
        _WATCH[names] = time.time()
        if _POLLER is None or not _POLLER.is_alive():
            _POLLER = threading.Thread(target=_poll_loop, name="tds-quotes", daemon=True)
            _POLLER.start()
    if not names <= held:
        _WAKE.set()


# ── candles: fetched in the background, regular session only ───────────────

def _et_wall(iso: str) -> int:
    """An ISO UTC timestamp as Eastern wall-clock seconds. The chart library
    draws times as UTC, so shifting here makes the axis read 9:30, not 13:30."""
    stamp = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return calendar.timegm(stamp.astimezone(_ET).replace(tzinfo=None).timetuple())


_INTRA_LOCK = threading.Lock()
_LOCAL = threading.local()
_INTRA: dict[tuple[str, str, str], dict] = {}   # (ticker, size, span) -> {"at", "bars", "err"}
_INTRA_BUSY: set[tuple[str, str, str]] = set()
_INTRA_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tds-intraday")
_INTRA_MAX = 128
_YAHOO: set[str] = set()        # symbols whose candles and quote come from Yahoo


def _candles(ticker: str, size: str, span: str) -> list:
    """Regular-session candles from Unusual Whales for the range's sessions plus
    its warm-up, oldest first: [t, open, high, low, close, volume]."""
    import uw

    # One connection per worker thread, kept open: a fresh one per fetch paid a
    # TLS handshake each time, which was most of a cold fetch's 1.5 seconds.
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = _LOCAL.session = uw._session(uw.get_token())
    need = sessions_for(size, span)
    fetch = "30m" if size == "1h" else size
    # Calendar days, with room for weekends and holidays.
    params = {"timeframe": f"{int(need * 1.45) + 4}D", "limit": 2500}
    out: dict[int, list] = {}
    for _ in range(_PAGES):
        raw = uw._get(session, f"/api/stock/{ticker}/ohlc/{fetch}", params)
        rows = raw.get("data", []) if isinstance(raw, dict) else (raw or [])
        for row in rows:
            if row.get("market_time") not in (None, "r"):
                continue              # regular hours only
            try:
                t = _et_wall(row["start_time"])
                out[t] = [t, float(row["open"]), float(row["high"]), float(row["low"]),
                          float(row["close"]), float(row.get("volume") or 0)]
            except (KeyError, TypeError, ValueError):
                continue
        days = {t // 86400 for t in out}
        if len(rows) < 2500 or len(days) >= need or not rows:
            break
        # A full page stopped partway back: carry on from its oldest day. That
        # day is asked for again, since the page may hold only part of it.
        oldest = min(str(r.get("start_time", "9999")) for r in rows)[:10]
        if params.get("end_date") == oldest:
            break
        params = {**params, "end_date": oldest}
    bars = [out[t] for t in sorted(out)]
    if size == "1h":
        bars = hourly(bars)
    return last_sessions(bars, need)


# The span a Yahoo symbol's quote is fetched under: its latest session in
# 1-minute bars, which carries the quote in the same call.
QUOTE_SPAN = "Q"


def _fetch_candles(key: tuple[str, str, str]) -> dict:
    """Candles - and for a Yahoo symbol's quote key, its quote and session date
    too, from the same call, so the chart and the price never disagree."""
    ticker, size, span = key
    with _INTRA_LOCK:
        from_yahoo = ticker in _YAHOO
    if not from_yahoo:
        return {"bars": _candles(ticker, size, span), "quote": None, "day": None}
    import yahoo

    if span != QUOTE_SPAN:
        n = sessions_for(size, span)
        return {"bars": last_sessions(yahoo.intraday_span(ticker, size, n), n),
                "quote": None, "day": None}
    bars, quote = yahoo.intraday(ticker, size)
    day = (datetime.fromtimestamp(bars[-1][0], tz=_UTC).date().isoformat() if bars else None)
    return {"bars": bars, "quote": quote, "day": day}


def _job(key: tuple[str, str, str]) -> None:
    try:
        entry = {"at": time.time(), "err": None, **_fetch_candles(key)}
    except Exception as exc:  # noqa: BLE001 - reported to the chart, not raised
        _LOCAL.session = None
        entry = {"at": time.time(), "bars": None,
                 "err": f"{type(exc).__name__}: {str(exc)[:120]}"}
    with _INTRA_LOCK:
        old = _INTRA.get(key)
        if entry["bars"] is None and old and old.get("bars"):
            # Keep the last good candles through a failed refresh.
            entry = {**old, "err": entry["err"], "at": time.time()}
        _INTRA[key] = entry
        _INTRA_BUSY.discard(key)
        if len(_INTRA) > _INTRA_MAX:
            for stale in sorted(_INTRA, key=lambda k: _INTRA[k]["at"])[:len(_INTRA) - _INTRA_MAX]:
                _INTRA.pop(stale, None)


# After the close there are no live prices for a wait to hold up, and ticks
# come only every IDLE_SECONDS - so "the next tick delivers" would mean a 1D
# chart taking up to half a minute to appear. Then, and only then, a first
# request waits this long for its candles.
CLOSED_WAIT = 3.0

# During the session a first request waits this long too. Measured: the fetch
# takes about 0.1s (about 1s on a worker's first, cold connection), so without
# a wait a new chart sat out most of the next tick's two seconds. This blocks
# only the run a chart's first request triggers, never the regular ticks, and
# a slower fetch still falls back to the next tick.
OPEN_WAIT = 1.5


def _candles_for(ticker: str, size: str, span: str, is_open: bool,
                 ttl: float | None = None) -> dict | None:
    """Held candles for this name, refreshed in the background once stale.
    Only a first request waits, and only briefly (OPEN_WAIT / CLOSED_WAIT); a
    refresh never does - the held candles go out while it runs."""
    key = (ticker, size, span)
    if ttl is None:
        ttl = INTRADAY_LIVE_TTL if is_open else INTRADAY_IDLE_TTL
    future = None
    with _INTRA_LOCK:
        entry = _INTRA.get(key)
        stale = entry is None or time.time() - entry["at"] > ttl
        if stale and key not in _INTRA_BUSY:
            _INTRA_BUSY.add(key)
            future = _INTRA_POOL.submit(_job, key)
    if entry is None and future is not None:
        try:
            future.result(timeout=OPEN_WAIT if is_open else CLOSED_WAIT)
        except Exception:  # noqa: BLE001 - timed out or failed; the next tick delivers
            pass
        with _INTRA_LOCK:
            entry = _INTRA.get(key)
    return entry


def _intraday(wants: dict, allowed: set, is_open: bool,
              yahoo_syms: frozenset = frozenset()) -> tuple[dict, dict]:
    """
    Candles for every chart that asked: at most one fetch per name, size and
    range per re-sync interval, however many viewers ask. Only names on screen
    are honoured, so the request cannot be steered at arbitrary tickers.

    A chart says which candle it already holds (`have`). While that is still the
    newest, it is sent only the forming candle - the rest is unchanged - which
    keeps a six-month hourly chart from resending a thousand candles a tick.
    """
    echoed, asks = {}, {}
    for panel, want in (wants or {}).items():
        if not isinstance(want, dict):
            continue
        ticker = str(want.get("ticker", "")).upper()
        size = str(want.get("size", ""))
        span = str(want.get("span") or "1D")
        if ticker not in allowed or span not in SPAN_SIZES.get(size, ()):
            continue
        echoed[str(panel)] = {"ticker": ticker, "size": size, "span": span}
        ask = asks.setdefault(f"{ticker}|{size}|{span}", {"key": (ticker, size, span),
                                                            "haves": set()})
        ask["haves"].add(want.get("have"))
    out = {}
    for name, ask in asks.items():
        ticker, size, span = ask["key"]
        # Futures and other Yahoo names trade outside US hours, so they keep a
        # short refresh rather than the after-hours one.
        entry = (_candles_for(ticker, size, span, is_open, ttl=YAHOO_TTL) if ticker in yahoo_syms
                 else _candles_for(ticker, size, span, is_open))
        if entry is None:
            continue                     # still loading; the chart keeps its message
        bars = entry.get("bars")
        if bars is None:
            out[name] = {"err": entry.get("err") or "unavailable"}
            continue
        stamp = bars[-1][0] if bars else None
        tail = bool(bars) and all(h == stamp for h in ask["haves"])
        out[name] = {"bars": bars[-1:] if tail else bars, "tail": tail,
                     "stamp": stamp, "sec": INTRADAY_SIZES[size]}
    return echoed, out


# ── the tick ─────────────────────────────────────────────────────────────────

def payload(tickers, wants: dict | None = None, yahoo_syms=()) -> dict:
    """What the page receives each tick. Reads only what is held; never raises."""
    now = _market_now()
    base = {"day": _today_str(), "open": market_open(now),
            "every": POLL_SECONDS, "ok": True, "fresh": True, "q": {},
            # Every tick differs, even when no price moved. The component only
            # runs its JavaScript when its data changes - measured: identical
            # data, no calls at all; changing data, one call every 2.0s - so
            # without this a quiet stretch would look like a stalled feed.
            "t": round(time.time(), 3),
            # How old the held quotes are, by the server's own clock. Ticks now
            # arrive whether or not the vendor answers, so the page needs this
            # to tell a stalled feed from a live one.
            "age": None,
            # Eastern offset, so the browser can drop a tick into the right
            # intraday bucket without trusting its own clock or timezone.
            "et_offset": int(now.utcoffset().total_seconds())}
    ysyms = frozenset(str(t).upper() for t in yahoo_syms)
    names = frozenset(str(t).upper() for t in tickers) - ysyms
    with _INTRA_LOCK:
        _YAHOO.update(ysyms)
    base["want"], base["intraday"] = _intraday(wants or {}, names | ysyms,
                                               base["open"], ysyms)
    # Yahoo names are priced whatever the US session is doing.
    base["yq"], base["yerr"] = {}, None
    for sym in ysyms:
        entry = _candles_for(sym, "1m", QUOTE_SPAN, base["open"], ttl=YAHOO_TTL)
        if entry and entry.get("quote"):
            base["yq"][sym] = {"q": entry["quote"], "day": entry.get("day"),
                               "age": round(time.time() - entry["at"], 1)}
        elif entry and entry.get("err"):
            base["yerr"] = entry["err"]
    if not base["open"] or not names:
        return base

    _watch(names)
    with _Q_LOCK:
        held, at, err = _SNAPSHOT["quotes"], _SNAPSHOT["at"], _SNAPSHOT["err"]
        quotes = {t: list(held[t]) for t in names if t in held}
    base["age"] = round(time.time() - at, 1) if at else None
    if err and not quotes:
        return {**base, "ok": False, "err": err}

    stamps = [q[6] for q in quotes.values() if q[6]]
    newest = max(stamps) if stamps else None
    # Ages are measured against the newest print in the batch rather than this
    # machine's clock, which can be off by seconds (one dev box ran 12s fast).
    for q in quotes.values():
        q.append(round((newest - q[6]) / 1000, 1) if newest and q[6] else None)
    fresh = (not newest) or (time.time() * 1000 - newest) < FEED_STALE_MS
    return {**base, "fresh": fresh, "q": quotes}


def mount(tickers) -> None:
    """
    Push to the page as a fragment, so only this reruns on the timer - the table
    and chart are never rebuilt, and keep their selection, scroll and zoom.
    """
    names = tuple(sorted({str(t).upper() for t in tickers}))
    every = POLL_SECONDS if market_open() else IDLE_SECONDS

    @st.fragment(run_every=every)
    def _tick():
        # What the charts last asked for. Read before mounting, so a request
        # is answered on the run it triggers rather than a tick later.
        state = st.session_state.get(FEED_KEY)
        wants = getattr(state, "want", None) if state is not None else None
        if wants is None and isinstance(state, dict):
            wants = state.get("want")
        extra = st.session_state.get(EXTRA_KEY) or ()
        watched = set(names) | {str(t).upper() for t in extra}
        _mount_feed(payload(watched, wants, st.session_state.get(YAHOO_KEY) or ()))

    _tick()
