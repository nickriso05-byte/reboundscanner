"""
Results table and chart in one component.

Two problems with the previous arrangement: selecting a row meant hitting a
checkbox rather than the name, and the chart rendered far below the table, so
every look cost a scroll and a server round-trip.

Here the table and the chart live in the same iframe with the bars embedded, so
clicking a ticker draws its chart immediately beneath that row - not beneath the
table - with no round trip at all, so a name and its chart are always read
together. The chart itself is the Chart Maker tool - legend, drag-to-measure,
history navigator, indicators and PNG export - fed from the local bar cache
instead of an HTTP API, which costs nothing and is faster than fetching.
"""

from __future__ import annotations

import json

import pandas as pd

import theme

# ~45 KB of JSON per ticker: deep daily bars plus a full weekly history that
# now carries OHLC so candles work on multi-year views. The row cap keeps the
# embed near 3 MB.
MAX_EMBED = 75


def weekly_payload(weekly: pd.DataFrame, tickers: list[str]) -> dict:
    """{ticker: {t,o,h,l,c,v}} full-history weekly bars, for long views."""
    if weekly.empty or not tickers:
        return {}
    wanted = set(tickers)
    sub = weekly[weekly["ticker"].isin(wanted)]
    out: dict[str, dict] = {}
    for ticker, g in sub.groupby("ticker"):
        g = g.sort_values("Date")
        dates = pd.to_datetime(g["Date"], errors="coerce")
        ok = dates.notna()
        if ok.sum() < 5:
            continue
        g, dates = g[ok], dates[ok]
        row = {"t": [int(d.timestamp()) for d in dates],
               "c": [round(float(x), 4) for x in g["Close"].fillna(0)]}
        for src, key in (("Open", "o"), ("High", "h"), ("Low", "l")):
            if src in g.columns:
                row[key] = [round(float(x), 4) for x in g[src].fillna(0)]
        if "Volume" in g.columns:
            row["v"] = [int(x) for x in g["Volume"].fillna(0)]
        out[str(ticker)] = row
    return out


def bars_payload(long: pd.DataFrame, tickers: list[str]) -> dict:
    """Compact {ticker: {t,o,h,l,c,v}} arrays - far smaller than row objects."""
    if long.empty or not tickers:
        return {}
    wanted = set(tickers)
    sub = long[long["ticker"].isin(wanted)]
    out: dict[str, dict] = {}
    for ticker, g in sub.groupby("ticker"):
        g = g.sort_values("Date")
        dates = pd.to_datetime(g["Date"], errors="coerce")
        ok = dates.notna()
        if ok.sum() < 5:
            continue
        g, dates = g[ok], dates[ok]
        out[str(ticker)] = {
            "t": [int(d.timestamp()) for d in dates],
            "o": [round(float(x), 4) for x in g["Open"].fillna(0)],
            "h": [round(float(x), 4) for x in g["High"].fillna(0)],
            "l": [round(float(x), 4) for x in g["Low"].fillna(0)],
            "c": [round(float(x), 4) for x in g["Close"].fillna(0)],
            "v": [int(x) for x in g["Volume"].fillna(0)],
        }
    return out


_TEMPLATE = r"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Libre+Caslon+Text:wght@400;700&family=Sofia+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
const THEMES = __THEMES__;
function pageScheme(){
  try {
    return getComputedStyle(window.parent.document.documentElement)
      .getPropertyValue("--scheme").trim() === "dark" ? "dark" : "light";
  } catch(err){ return "light"; }
}
function paintTokens(name){
  const T = THEMES[name], root = document.documentElement.style;
  for(const k in T) root.setProperty("--" + k.replace(/_/g, "-"), T[k]);
}
let SCHEME = pageScheme();
paintTokens(SCHEME);           // before the body draws, so nothing flashes
</script>
<style>
  __TOKENS__
  *{box-sizing:border-box}
  [hidden]{display:none !important}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:"Sofia Sans",-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
       font-size:14px;line-height:1.45}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px}

  /* ---------- results table ---------- */
  /* resize:vertical gives a native drag handle at the bottom-right, so the
     split between table and chart is adjustable without a rerun. */
  /* max-height, not height: with few matches the table shrinks to fit, so a
     filter visibly changes the list instead of leaving an identical window. */
  /* The box is sized for the whole panel, since the chart now opens inside it:
     with the chart folded away that room goes to rows instead of sitting
     blank, and the table itself never moves when a chart opens. */
  .tablewrap{max-height:__FULLH__px;overflow:auto;border-radius:0 0 10px 10px;
    resize:vertical}
  table{border-collapse:separate;border-spacing:0;width:100%;font-variant-numeric:tabular-nums}
  thead th{position:sticky;top:0;z-index:2;background:var(--panel-alt);text-align:right;
    font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;
    color:var(--muted);padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
  thead th.l{text-align:left}
  thead th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
  thead th.sortable:hover{background:var(--accent-soft);color:var(--ink)}
  thead th[aria-sort]{color:var(--accent)}
  .sortmark{display:inline-block;width:9px;margin-left:4px;font-size:9px;
    color:var(--accent);vertical-align:middle}
  tbody td{padding:4px 9px;font-size:13px;border-bottom:1px solid var(--line-soft);
    text-align:right;white-space:nowrap;line-height:1.5}
  tbody td.l{text-align:left}
  tbody tr{cursor:pointer}
  tbody tr:hover{background:var(--panel-alt)}
  tbody tr[aria-selected=true]{background:var(--accent-soft)}
  tbody tr[aria-selected=true] td{border-bottom-color:var(--accent)}
  .tk{font-weight:700;color:var(--accent)}
  tbody tr:hover .tk{text-decoration:underline}
  .nm{color:var(--muted);max-width:230px;overflow:hidden;text-overflow:ellipsis}
  .pos{color:var(--up)} .neg{color:var(--down)}
  .yes{color:var(--up);font-weight:500} .no{color:var(--muted)}
  .hint{padding:9px 12px;font-size:11.5px;color:var(--muted);border-bottom:1px solid var(--line);
    background:var(--panel-alt);border-radius:10px 10px 0 0;
    display:flex;align-items:center;gap:12px}
  .hint #hint{flex:1;min-width:0}

  /* ---------- live prices ---------- */
  .live{display:inline-flex;align-items:center;gap:6px;font-size:10.5px;font-weight:700;
    text-transform:uppercase;letter-spacing:.07em;white-space:nowrap;color:var(--muted)}
  .live i{width:7px;height:7px;border-radius:50%;background:currentColor;display:inline-block}
  .live.on{color:var(--up)} .live.on i{animation:livepulse 2s ease-out infinite}
  .live.lag{color:#d4a017} .live.down{color:var(--down)} .live.closed{color:var(--muted)}
  @keyframes livepulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--up) 60%,transparent)}
                       100%{box-shadow:0 0 0 6px transparent}}
  /* A price that changes silently reads as a static table; a brief wash on
     each tick is what makes it read as live. */
  td.tick-up{animation:tickup 1s ease-out}
  td.tick-dn{animation:tickdn 1s ease-out}
  @keyframes tickup{0%{background:color-mix(in srgb,var(--up) 30%,transparent)}100%{background:transparent}}
  @keyframes tickdn{0%{background:color-mix(in srgb,var(--down) 30%,transparent)}100%{background:transparent}}
  td.lp.stale{color:var(--muted)}
  /* Chart tab: the chart alone, with the symbol search pinned top-left. */
  .panel.chartonly .tablewrap{display:none}
  .panel.chartonly .chartwrap{border-top:0}
  .panel.chartonly .legend{top:56px}
  .symsearch{position:absolute;left:14px;top:8px;z-index:6;width:min(380px,calc(100% - 28px))}
  .symsearch .sbox{display:flex;align-items:center;gap:8px;height:40px;padding:0 11px;
    background:var(--panel);border:1.5px solid var(--accent);border-radius:9px;
    box-shadow:0 2px 10px var(--shadow);color:var(--accent)}
  .symsearch input{flex:1;min-width:0;border:0;outline:0;background:transparent;font:inherit;
    font-size:14.5px;font-weight:600;color:var(--ink)}
  .symsearch input::placeholder{color:var(--muted);font-weight:500}
  .symsearch .skey{font-size:10.5px;font-weight:600;color:var(--muted);white-space:nowrap}
  .symlist{margin-top:5px;background:var(--panel);border:1px solid var(--line);border-radius:9px;
    box-shadow:0 10px 28px var(--shadow);max-height:340px;overflow-y:auto}
  .so-opt{display:grid;grid-template-columns:auto 1fr auto;gap:1px 10px;align-items:baseline;
    padding:7px 12px;cursor:pointer;border-top:1px solid var(--line-soft)}
  .so-opt:first-child{border-top:0}
  .so-opt.on,.so-opt:hover{background:var(--accent-soft)}
  .so-sym{font-weight:700;color:var(--ink)}
  .so-name{font-size:12.5px;color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .so-tag{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:1px 6px;
    border-radius:4px;border:1px solid var(--line);color:var(--muted);background:var(--panel-alt)}
  .so-tag.fut{color:#b7791f;border-color:color-mix(in srgb,#b7791f 45%,var(--line))}
  .so-ex{grid-column:2 / -1;font-size:10.5px;color:var(--muted)}
  .so-none{padding:10px 12px;font-size:12px;color:var(--muted)}
  .chartmsg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    font-size:13px;color:var(--muted);background:var(--panel);z-index:4;text-align:center;padding:20px}
  .hint b.count{font-size:15px;font-weight:700;color:var(--accent);
    letter-spacing:-.01em;margin-right:2px}

  /* ---------- chart ---------- */
  /* The chart is a row of the table, inserted directly beneath the name that
     was clicked, so reading it never costs a scroll back up. Sticky at left:0
     keeps it in place when the table is scrolled sideways, and the explicit
     width (set in JS) keeps it to the visible width rather than the full span
     of the columns. */
  tbody tr.chartrow{cursor:default}
  tbody tr.chartrow:hover{background:transparent}
  tbody td.chartcell{padding:0;text-align:left;border-bottom:1px solid var(--line)}
  .chartwrap{padding:10px 14px 8px;position:sticky;left:0;
    background:var(--panel);border-top:1px solid var(--line)}
  .chartbox{position:relative}
  #chart{height:__CHARTH__px;width:100%;cursor:crosshair}
  /* The watermark (none while LOGO is empty), faint, centred on the plot - clear of the price scale
     on the right and the dates beneath. It sits over the canvas rather than
     under it (the chart paints an opaque ground), so it stays faint enough to
     read as part of the background and never takes a click. */
  .wmark{position:absolute;left:0;top:0;right:64px;bottom:30px;z-index:1;
    display:flex;align-items:center;justify-content:center;pointer-events:none;
    color:var(--ink);opacity:var(--watermark)}
  .wmark svg{width:min(44%,420px);height:auto}

  .legend{position:absolute;left:14px;top:8px;pointer-events:none;z-index:3}
  .legend .l1{font-size:19px;font-weight:700;letter-spacing:-.01em;line-height:1.25}
  .legend .l1 .nm2{font-size:13px;font-weight:400;color:var(--muted);margin-left:7px}
  .legend .l2{font-size:14px;font-weight:600;line-height:1.35}
  .legend .l2 .chg.up{color:var(--up)} .legend .l2 .chg.down{color:var(--down)}
  .legend .l3{display:flex;flex-wrap:wrap;gap:10px;margin-top:3px;font-size:11.5px;color:var(--muted)}
  .legend .l3 span{display:inline-flex;align-items:center;gap:5px}
  .legend .l3 i{width:8px;height:8px;border-radius:2px;display:inline-block}

  .measure{position:absolute;top:0;pointer-events:none;z-index:2;
    background:var(--wash);border-left:1px dashed var(--wash-edge);
    border-right:1px dashed var(--wash-edge)}
  .mlabel{position:absolute;top:8px;left:50%;transform:translateX(-50%);white-space:nowrap;
    background:var(--accent);color:var(--accent-ink);font-size:12px;font-weight:600;padding:5px 9px;border-radius:6px}
  .mlabel.down{background:var(--down)} .mlabel.up{background:var(--up)}
  .lines{position:absolute;left:0;top:0;pointer-events:none;z-index:2}

  /* The navigator strip is retired - the range buttons and dates move through
     time, and the chart itself drags. Kept in the page, hidden, since its
     listeners are wired at load. */
  .navbox{display:none !important}
  .navbox{position:relative;margin-top:6px;border-top:1px solid var(--line);padding-top:4px;
    cursor:pointer;user-select:none}
  #nav{height:48px;width:100%}
  .navwin{position:absolute;top:4px;bottom:14px;background:var(--wash);
    border-left:2px solid var(--accent);border-right:2px solid var(--accent);cursor:grab;z-index:2}
  .navshade{position:absolute;top:4px;bottom:14px;background:var(--shade);
    pointer-events:none;z-index:1}
  .navshade.l{left:0} .navshade.r{right:0}
  .navwin.dragging{cursor:grabbing}

  .toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:14px;
    margin:8px -14px -8px;padding:9px 14px;background:var(--panel-alt);
    border-top:1px solid var(--line);border-radius:0 0 9px 9px}
  .seg{display:flex;border:1px solid var(--line);border-radius:7px;overflow:hidden;background:var(--panel)}
  .seg button{font:inherit;font-size:12.5px;font-weight:600;border:0;background:var(--panel);color:var(--ink);
    padding:7px 11px;cursor:pointer;border-right:1px solid var(--line)}
  .seg button:last-child{border-right:0}
  .seg button:hover{background:var(--accent-soft)}
  .seg button[aria-pressed=true]{background:var(--accent);color:var(--accent-ink)}
  .checks{display:flex;flex-wrap:wrap;gap:7px}
  .chk{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;padding:6px 10px;
    border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink);cursor:pointer;user-select:none}
  .chk:hover{background:var(--accent-soft)}
  .chk input{margin:0;accent-color:var(--accent)}
  .chk .swatch{width:9px;height:9px;border-radius:2px;display:inline-block}
  .dates{display:flex;align-items:center;gap:6px}
  .dates input{font:inherit;font-size:12.5px;padding:6px 8px;border:1px solid var(--line);
    border-radius:7px;background:var(--panel);color:var(--ink);color-scheme:var(--scheme)}
  .dates input:focus{outline:2px solid var(--accent);outline-offset:-1px;border-color:transparent}
  .dates .dash{color:var(--muted)}
  .spacer{flex:1}
  .btn{font:inherit;font-size:13px;font-weight:600;border:0;border-radius:7px;padding:8px 15px;
    background:var(--accent);color:var(--accent-ink);cursor:pointer}
  .btn:hover{filter:brightness(1.12)}
  .btn:disabled{opacity:.45;cursor:default;filter:none}
  .empty{padding:26px;text-align:center;color:var(--muted);font-size:13px}
</style>

<div class="panel__PANELCLASS__">
  <div class="hint"><span id="hint">__HINT__</span><span class="live" id="live" hidden></span></div>
  <div class="tablewrap"><table>
    <thead><tr>__THEAD__</tr></thead>
    <tbody id="rows">__TBODY__</tbody>
  </table></div>

  <div class="chartwrap" id="chartwrap" hidden>
    <div class="chartbox">
      <div class="symsearch" id="symsearch" hidden>
        <div class="sbox">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
               stroke-linecap="round"><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.6-3.6"></path></svg>
          <input id="symq" type="text" autocomplete="off" spellcheck="false"
                 placeholder="Search ticker or commodity…" aria-label="Search ticker or commodity">
          <span class="skey">Enter ↵</span>
        </div>
        <div class="symlist" id="symlist" hidden></div>
      </div>
      <div id="chart" title="Drag to move the chart back and forth"></div>
      <div class="wmark" aria-hidden="true">__LOGO__</div>
      <div class="legend" id="legend">
        <div class="l1"><span id="l-sym">&mdash;</span><span class="nm2" id="l-nm"></span></div>
        <div class="l2"><span id="l-last"></span> <span class="chg" id="l-chg"></span></div>
        <div class="l3" id="l-ind"></div>
      </div>
      <div class="measure" id="measure" hidden><div class="mlabel" id="m-label"></div></div>
      <div class="chartmsg" id="chartmsg" hidden></div>
    </div>

    <div class="navbox" id="navbox" title="Drag the window — or its edges — to move through time">
      <div id="nav"></div>
      <div class="navshade l" id="navshade-l" hidden></div>
      <div class="navshade r" id="navshade-r" hidden></div>
      <div class="navwin" id="navwin" hidden></div>
    </div>

    <div class="toolbar">
      <div class="seg" id="range">
        <button data-v="1d">1D</button><button data-v="2d">2D</button><button data-v="5d">5D</button>
        <button data-v="21">1M</button><button data-v="63">3M</button>
        <button data-v="126">6M</button><button data-v="ytd">YTD</button>
        <button data-v="252" aria-pressed="true">1Y</button>
        <button data-v="all">2Y</button>
        <button data-v="w260">5Y</button><button data-v="wmax">Max</button>
      </div>
      <div class="seg" id="iv" title="Bar size"></div>
      <div class="dates">
        <input id="from" type="date"><span class="dash">&ndash;</span>
        <input id="to" type="date">
      </div>
      <div class="seg" id="type">
        <button data-v="candle" aria-pressed="true">Candles</button>
        <button data-v="line">Line</button>
      </div>
      <div class="seg" id="drawseg">
        <button id="measurebtn" aria-pressed="false"
                title="Measure a move: drag across the chart for the % change between two bars.">Measure</button>
        <button id="drawbtn" aria-pressed="false"
                title="Draw trend lines: drag across the chart. Click a line to select or move it; Delete removes it.">Draw</button>
        <button id="drawclear" title="Remove this name's lines" hidden>Clear</button>
      </div>
      <div class="checks" id="inds">
        <label class="chk"><input type="checkbox" data-v="sma50"><span class="swatch" style="background:#2f7fd1"></span><span class="lb" data-p="50">50-day</span></label>
        <label class="chk"><input type="checkbox" data-v="sma100"><span class="swatch" style="background:#17a2a2"></span><span class="lb" data-p="100">100-day</span></label>
        <label class="chk"><input type="checkbox" data-v="sma200"><span class="swatch" style="background:#e08b1f"></span><span class="lb" data-p="200">200-day</span></label>
        <label class="chk"><input type="checkbox" data-v="bb"><span class="swatch" style="background:#8e93a3"></span>Bollinger</label>
        <label class="chk"><input type="checkbox" data-v="vol"><span class="swatch" style="background:#b9c0cc"></span>Volume</label>
        <label class="chk"><input type="checkbox" data-v="rsi"><span class="swatch" style="background:#7c5cbf"></span>RSI</label>
      </div>
      <div class="spacer"></div>
      <button class="btn" id="dl">Download PNG</button>
    </div>
  </div>
</div>

<script>
const CHROME = Object.assign({}, THEMES[SCHEME]);
const DATA  = __DATA__;
const WEEK  = __WEEKLY__;
const NAMES = __NAMES__;
// Which tab this panel is, so its intraday request cannot clash with another's.
const PANEL = __PANEL__;
// Whether clicking the open name folds its chart away. Useful when scanning a
// long list of results; not when the chart was what was asked for.
const FOLDABLE = __FOLDABLE__;
// The Chart tab's panel: no table, a symbol search in the chart, and a data
// source that may be Yahoo (delayed) rather than the live feed.
const CHART_ONLY = __CHARTONLY__;
const SEARCH = __SEARCH__;
const SRC = __SRC__;
const SOURCE_LABEL = SRC === "yahoo" ? "Yahoo Finance" : "Unusual Whales";
// Captured before anything overwrites it, so folding the chart away can put
// the original line back.
const HINT = document.getElementById("hint").innerHTML;

const MAS = [
  {key:"sma50",  period:50,  color:"#2f7fd1"},
  {key:"sma100", period:100, color:"#17a2a2"},
  {key:"sma200", period:200, color:"#e08b1f"}
];
const COLORS = {};
function refreshColors(){
  Object.assign(COLORS, {bb:CHROME.muted, vol:CHROME.muted, rsi:"#7c5cbf",
                         up:CHROME.up, down:CHROME.down, line:CHROME.accent,
                         accent:CHROME.accent, credit:CHROME.muted});
}
refreshColors();

/* Each range offers the bar sizes charting software would for it. Intraday
   sizes come from the server; daily is the cached history, and weekly and
   monthly are the weekly record (or daily, folded up). The limits keep every
   view inside what one or two vendor calls return - 1-minute bars run out
   past a couple of days, hourly past six months. */
const RANGES = {
  "1d":  {iv:["1m","5m","15m","30m","1h"], def:"1m",  span:"1D", n:1},
  "2d":  {iv:["1m","5m","15m","30m","1h"], def:"5m",  span:"2D", n:2},
  "5d":  {iv:["5m","15m","30m","1h"],      def:"15m", span:"5D", n:5},
  "21":  {iv:["15m","30m","1h","D"],       def:"D",   span:"1M", n:21},
  "63":  {iv:["1h","D","W"],               def:"D",   span:"3M", n:63},
  "126": {iv:["1h","D","W"],               def:"D",   span:"6M", n:126},
  "ytd": {iv:["D","W"],     def:"D"},
  "252": {iv:["D","W"],     def:"D"},
  "all": {iv:["D","W","M"], def:"D"},
  "w260":{iv:["W","M"],     def:"W"},
  "wmax":{iv:["W","M"],     def:"W"}
};
const IV_LABEL = {"1m":"1m","5m":"5m","15m":"15m","30m":"30m","1h":"1H","D":"D","W":"W","M":"M"};
const IV_TITLE = {"1m":"1-minute bars","5m":"5-minute bars","15m":"15-minute bars",
                  "30m":"30-minute bars","1h":"Hourly bars","D":"Daily bars",
                  "W":"Weekly bars","M":"Monthly bars"};
const isIntraIv = iv => !(iv === "D" || iv === "W" || iv === "M");

const state = {ticker:null, type:"candle", range:"252", iv:"D", measure:null,
               ind:{sma50:false,sma100:false,sma200:false,bb:false,vol:false,rsi:false}};
let chart=null, mainSeries=null, navChart=null;
// daily carries deep history so a 200-period average is already complete on the
// first visible bar; weekly spans the whole record and drives the navigator.
let daily=[], weekly=[], base=[], view=[], onWeekly=false, hasWeeklyOHLC=false;
// What the chart series holds: every bar of the base when the window is a
// run of it, so dragging back shows earlier bars. plotOff is the window's
// first bar within it.
let plot=[], plotOff=0;
// What one bar of the drawn series is, and whether it carries a real open,
// high and low (a close-only weekly record is drawn as a line).
let unit="day", baseOHLC=true;
// The 1D view: today's intraday candles, from the server on request.
let onIntra=false;
// Every overlay with a readable number, so the legend can report values
// rather than just naming what is drawn.
let overlays=[], priceSeries=null;
const el = id => document.getElementById(id);

const fmtDate = ts => new Date(ts*1000).toLocaleDateString("en-US",
  {month:"short", day:"numeric", year:"numeric", timeZone:"UTC"});
const isoOf = ts => new Date(ts*1000).toISOString().slice(0,10);
const num = v => v.toLocaleString("en-US",{minimumFractionDigits:2, maximumFractionDigits:2});
const unitWord = () => onIntra ? "bar" : unit;

/* ---------------- maths ---------------- */
function sma(vals,p){
  const out=new Array(vals.length).fill(null); let s=0;
  for(let i=0;i<vals.length;i++){ s+=vals[i]; if(i>=p) s-=vals[i-p]; if(i>=p-1) out[i]=s/p; }
  return out;
}
function bollinger(vals,p,k){
  p=p||20;k=k||2;
  const mid=sma(vals,p), up=new Array(vals.length).fill(null), lo=up.slice();
  for(let i=p-1;i<vals.length;i++){
    let v=0; for(let j=i-p+1;j<=i;j++) v+=Math.pow(vals[j]-mid[i],2);
    const sd=Math.sqrt(v/p); up[i]=mid[i]+k*sd; lo[i]=mid[i]-k*sd;
  }
  return {mid:mid,up:up,lo:lo};
}
function rsi(vals,p){
  p=p||14;
  const out=new Array(vals.length).fill(null);
  if(vals.length<=p) return out;
  let g=0,l=0;
  for(let i=1;i<=p;i++){ const d=vals[i]-vals[i-1]; if(d>=0) g+=d; else l-=d; }
  g/=p;l/=p; out[p]= l===0?100:100-100/(1+g/l);
  for(let i=p+1;i<vals.length;i++){
    const d=vals[i]-vals[i-1];
    g=(g*(p-1)+(d>0?d:0))/p; l=(l*(p-1)+(d<0?-d:0))/p;
    out[i]= l===0?100:100-100/(1+g/l);
  }
  return out;
}
const toSeries=(bars,arr)=>bars.map((b,i)=>({time:b.time,value:arr[i]}))
                               .filter(d=>d.value!==null&&d.value!==undefined);

/* ---------------- the chart's place in the table ---------------- */
/* One chart element, moved to sit under whichever row is selected. Moving a
   node is far cheaper than building a second chart, and it keeps every
   listener wired at load time intact. */
const wrapEl = () => document.querySelector(".tablewrap");
let chartRow = null;

function mountChartUnder(tr){
  if(!chartRow){
    chartRow=document.createElement("tr");
    chartRow.className="chartrow";
    const td=document.createElement("td");
    td.className="chartcell";
    td.colSpan=document.querySelectorAll("thead th").length;
    td.appendChild(el("chartwrap"));
    chartRow.appendChild(td);
  }
  withRowSteady(tr, function(){
    if(tr.nextSibling!==chartRow) tr.parentNode.insertBefore(chartRow, tr.nextSibling);
    chartRow.hidden=false;
  });
  sizeChartCell();
}

/* Moving the chart row can add or remove a chart's worth of height above the
   clicked name, which would slide it out from under the pointer. Holding that
   one row still is what makes the table read as static: the chart opens and
   closes around it, and nothing else appears to move. */
function withRowSteady(tr, fn){
  const wrap=wrapEl(), top=()=>tr.getBoundingClientRect().top-wrap.getBoundingClientRect().top;
  const before=top();
  fn();
  wrap.scrollTop += top()-before;
}

/* Clicking the open name again folds the chart away. The row is hidden rather
   than removed: taking it out of the document would take the chart, its date
   inputs and its toolbar with it, and every lookup by id would then come back
   empty. */
function closeChart(){
  if(onIntra){ INTRA.want = null; askIntraday(); }
  if(chartRow) chartRow.hidden=true;
  document.querySelectorAll("#rows tr[data-t]").forEach(function(tr){
    tr.setAttribute("aria-selected","false");
  });
  state.ticker=null;
  el("hint").innerHTML=HINT;
}

/* The cell spans every column, which can be far wider than the box; the chart
   is held to the visible width instead. A width of zero means the panel is not
   on screen yet - an inactive tab - so leave it alone rather than pinning the
   chart shut, and let the observer below set it when the box has a size. */
function sizeChartCell(){
  if(!chartRow) return false;
  const w=wrapEl().clientWidth;
  if(w<40) return false;
  const cur=parseFloat(el("chartwrap").style.width)||0;
  if(Math.abs(cur-w)<1) return false;
  el("chartwrap").style.width=w+"px";
  return true;
}

/* ---------------- selection ---------------- */
function selectTicker(t){
  if(!DATA[t]) return;
  state.ticker=t; state.measure=null;
  document.querySelectorAll("#rows tr[data-t]").forEach(function(tr){
    tr.setAttribute("aria-selected", String(tr.dataset.t===t));
  });
  const d=DATA[t];
  daily = d.t.map(function(ts,i){
    return {time:ts, open:d.o[i], high:d.h[i], low:d.l[i], close:d.c[i], volume:d.v[i]};
  });
  const w=WEEK[t];
  weekly = w ? w.t.map(function(ts,i){
    return {time:ts,
            open:  w.o ? w.o[i] : w.c[i],
            high:  w.h ? w.h[i] : w.c[i],
            low:   w.l ? w.l[i] : w.c[i],
            close: w.c[i],
            volume: w.v ? w.v[i] : 0};
  }) : [];
  hasWeeklyOHLC = !!(w && w.o && w.h && w.l);
  // Prices may have ticked since the page loaded; fold today's bar in before
  // drawing, so switching names never shows a stale last candle.
  if(typeof mergeLive === "function") mergeLive(t);
  setDateBounds();
  el("chartwrap").hidden=false;
  el("hint").textContent = FOLDABLE
    ? "Showing " + t + " — click " + t + " again to close it, "
      + "another name to switch. Drag the chart to move it back and forth."
    : "Showing " + t
      + (document.querySelectorAll("#rows tr[data-t]").length > 1 ? " — click another name to switch." : ".")
      + " Drag the chart to move it back and forth.";
  const tr=document.querySelector('#rows tr[data-t="'+t.replace(/"/g,'\\"')+'"]');
  if(tr && !CHART_ONLY) mountChartUnder(tr);
  applyRange(state.range);
}

/* Weekly bars back the long ranges, because a decade of daily bars is far more
   than the embed can carry. Indicators are always computed on the whole base
   series and then trimmed, so a 200-period line is complete from the first
   visible bar rather than starting 200 bars into the view. */
function applyRange(v){
  state.range = v;
  const R = RANGES[v] || RANGES["252"];
  // Keep the bar size across ranges where it still applies, as charting
  // software does: 5D on 15m to 1M stays on 15m.
  if(R.iv.indexOf(state.iv) < 0) state.iv = R.def;
  renderIv();
  rememberIntraday();
  if(isIntraIv(state.iv)){ enterIntraday(); return; }
  if(onIntra) leaveIntraday();
  if(!daily.length) return;

  base = seriesFor(state.iv);
  view = sliceFor(v, base);
  if(view.length<2) view=base.slice();
  syncDateInputs();
  draw();
}

function renderIv(){
  const R = RANGES[state.range] || RANGES["252"];
  el("iv").innerHTML = R.iv.map(function(v){
    return '<button data-v="' + v + '" title="' + IV_TITLE[v] + '" aria-pressed="'
      + (v === state.iv) + '">' + IV_LABEL[v] + '</button>';
  }).join("");
}

/* Bars folded into longer ones: each starts with the first bar of its period. */
function aggregate(src, keyOf){
  const out = [];
  src.forEach(function(b){
    const k = keyOf(b.time), o = out[out.length - 1];
    if(!o || o.time !== k){
      out.push({time:k, open:b.open, high:b.high, low:b.low, close:b.close, volume:b.volume});
    } else {
      o.high = Math.max(o.high, b.high); o.low = Math.min(o.low, b.low);
      o.close = b.close; o.volume += b.volume;
    }
  });
  return out;
}
const weekOf = t => t - (t % 86400) - ((new Date(t*1000).getUTCDay() + 6) % 7) * 86400;
const monthOf = t => { const d = new Date(t*1000); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000; };

/* Months from daily bars where the daily history covers the whole month,
   and from the weekly record before that. */
function monthlyBars(){
  if(!weekly.length) return aggregate(daily, monthOf);
  if(!daily.length) return aggregate(weekly, monthOf);
  const d0 = new Date(daily[0].time*1000);
  const cut = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth() + 1, 1) / 1000;
  return aggregate(weekly.filter(b => b.time < cut), monthOf)
    .concat(aggregate(daily.filter(b => b.time >= cut), monthOf));
}

function seriesFor(iv){
  if(iv === "W"){
    unit = "week"; onWeekly = true;
    baseOHLC = weekly.length ? hasWeeklyOHLC : true;
    return weekly.length ? weekly : aggregate(daily, weekOf);
  }
  if(iv === "M"){
    unit = "month"; onWeekly = true;
    baseOHLC = hasWeeklyOHLC || !weekly.length;
    return monthlyBars();
  }
  unit = "day"; onWeekly = false; baseOHLC = true;
  return daily;
}

/* The window a range button means, on whichever series is drawn. A weekly or
   monthly view starts on the bar whose period holds the window's first day. */
function sliceFor(v, s){
  if(!s.length || v === "wmax") return s.slice();
  const last = daily.length ? daily[daily.length-1].time : s[s.length-1].time;
  let cut;
  if(v === "w260") cut = last - Math.round(5 * 365.25) * 86400;
  else if(v === "all") cut = daily.length ? daily[0].time : s[0].time;
  else if(v === "ytd") cut = Date.UTC(new Date(last*1000).getUTCFullYear(), 0, 1) / 1000;
  else {
    const n = Number(v) || 252;
    cut = daily.length ? daily[Math.max(0, daily.length - n)].time : s[0].time;
  }
  if(s === daily) return daily.filter(b => b.time >= cut);
  let i = s.length - 1;
  while(i > 0 && s[i].time > cut) i--;
  return s.slice(i);
}

function syncDateInputs(){
  if(!view.length) return;
  el("from").value=isoOf(view[0].time);
  el("to").value=isoOf(view[view.length-1].time);
}

/* Bound the pickers to the history actually held, so a chosen date is always
   one the chart can honour instead of quietly doing nothing. */
function setDateBounds(){
  const earliest = (weekly.length ? weekly[0].time : (daily.length ? daily[0].time : null));
  const latest = (daily.length ? daily[daily.length-1].time
                               : (weekly.length ? weekly[weekly.length-1].time : null));
  if(earliest===null||latest===null) return;
  ["from","to"].forEach(function(id){
    el(id).min=isoOf(earliest);
    el(id).max=isoOf(latest);
  });
}

/* A typed range picks whichever series can actually cover it: daily when the
   window falls inside the deep daily history, weekly when it reaches further. */
function applyDates(fromISO, toISO){
  if(!fromISO || !toISO || fromISO>toISO) return;
  let f=Date.parse(fromISO+"T00:00:00Z")/1000, t=Date.parse(toISO+"T00:00:00Z")/1000;

  // Clamp into the held history rather than returning empty-handed, which is
  // what made a typed date look like it had been ignored.
  const lo = weekly.length ? weekly[0].time : (daily.length ? daily[0].time : f);
  const hi = daily.length ? daily[daily.length-1].time
                          : (weekly.length ? weekly[weekly.length-1].time : t);
  f=Math.max(f, lo); t=Math.min(t, hi);
  if(t<=f) return;

  // Daily where the window fits inside the deep daily history; weekly beyond.
  const inDaily = daily.length && f >= daily[0].time;
  const src = inDaily ? daily : (weekly.length ? weekly : daily);
  onWeekly = !inDaily && weekly.length>0;
  unit = onWeekly ? "week" : "day"; baseOHLC = onWeekly ? hasWeeklyOHLC : true;
  base = src;
  let sel = src.filter(b=>b.time>=f && b.time<=t);
  if(sel.length<2 && weekly.length){
    // Too few bars at this resolution - fall back to the fuller series.
    base = weekly; onWeekly = true; unit = "week"; baseOHLC = hasWeeklyOHLC;
    sel = weekly.filter(b=>b.time>=f && b.time<=t);
  }
  if(sel.length<2) return;
  view = sel;
  Array.prototype.forEach.call(el("range").children,function(b){
    b.setAttribute("aria-pressed","false");
  });
  state.iv = onWeekly ? "W" : "D";
  Array.prototype.forEach.call(el("iv").children,function(b){
    b.setAttribute("aria-pressed", String(b.dataset.v === state.iv));
  });
  draw();
}

el("rows").addEventListener("click", function(e){
  const tr=e.target.closest("tr[data-t]");
  if(!tr) return;
  // The open name is a toggle: clicking it again folds the chart away -
  // unless this panel is a chart request, where the chart is the point.
  if(tr.dataset.t===state.ticker){ if(FOLDABLE) closeChart(); }
  else { selectTicker(tr.dataset.t); revealChart(tr); }
});

/* A click on a name brings its chart into view - within the table first, then
   on the page - so a name near the bottom no longer opens its chart out of
   sight. A chart already wholly on screen is left exactly where it is. */
function revealChart(tr){
  if(!chartRow || chartRow.hidden) return;
  const wrap=wrapEl(), w=wrap.getBoundingClientRect();
  const head=document.querySelector("thead").offsetHeight;
  const top=tr.getBoundingClientRect().top, bot=chartRow.getBoundingClientRect().bottom;
  const room=w.height-head;
  let dy=0;
  if(top < w.top+head || bot > w.bottom){
    dy = bot-top <= room ? (top+bot)/2 - (w.top+head+room/2) : top-(w.top+head);
    const max=wrap.scrollHeight-wrap.clientHeight;
    dy = Math.min(Math.max(wrap.scrollTop+dy, 0), max) - wrap.scrollTop;
    wrap.scrollTo({top:wrap.scrollTop+dy, behavior:"smooth"});
  }
  revealOnPage(top-dy, bot-dy);   // where the block lands once the table settles
}

/* The frame sits in the Streamlit page, which scrolls on its own. Centre the
   row and its chart in the window, below Streamlit's top bar; a block taller
   than the window starts at its row instead. */
function revealOnPage(top, bot){
  let frame, pwin;
  try { frame=window.frameElement; pwin=window.parent; } catch(err){ return; }
  if(!frame || pwin===window) return;
  const pdoc=pwin.document, f=frame.getBoundingClientRect();
  const bar=pdoc.querySelector('[data-testid="stHeader"]');
  const lo=bar ? Math.max(0, bar.getBoundingClientRect().bottom) : 0, hi=pwin.innerHeight;
  const a=f.top+top, b=f.top+bot;
  if(a>=lo && b<=hi) return;
  const dy = b-a <= hi-lo ? (a+b)/2 - (lo+hi)/2 : a-lo-12;
  let box=frame.parentElement;
  while(box && box!==pdoc.body){
    if(/(auto|scroll|overlay)/.test(pwin.getComputedStyle(box).overflowY)
       && box.scrollHeight>box.clientHeight) break;
    box=box.parentElement;
  }
  if(!box || box===pdoc.body) box=pdoc.scrollingElement || pdoc.documentElement;
  box.scrollBy({top:dy, behavior:"smooth"});
}

/* ---------------- sorting ---------------- */
/* Sorting is a reorder of rows already in the page - no rerun, and the chart
   and selection are untouched. Blanks sink to the bottom either way, so an
   empty cell never outranks a real number. */
const sortState={col:null, dir:1};

function applySort(){
  const tbody=el("rows");
  // Only the data rows sort; the chart row is re-seated afterwards.
  const rows=[...tbody.querySelectorAll("tr[data-t]")];
  const col=sortState.col, dir=sortState.dir;
  const numeric=document.querySelector('th[data-col="'+col+'"]').dataset.num==="1";

  rows.sort(function(a,b){
    const av=a.children[col].dataset.v, bv=b.children[col].dataset.v;
    const ae=(av===""||av===undefined), be=(bv===""||bv===undefined);
    if(ae&&be) return 0;
    if(ae) return 1;          // blanks last, regardless of direction
    if(be) return -1;
    if(numeric) return (parseFloat(av)-parseFloat(bv))*dir;
    return av.localeCompare(bv)*dir;
  });
  const frag=document.createDocumentFragment();
  rows.forEach(function(r){ frag.appendChild(r); });
  tbody.appendChild(frag);

  // The chart follows its name to wherever the new order puts it.
  if(chartRow && state.ticker){
    const tr=tbody.querySelector('tr[data-t="'+state.ticker.replace(/"/g,'\\"')+'"]');
    if(tr) mountChartUnder(tr);
  }

  document.querySelectorAll("th.sortable").forEach(function(th){
    const on=Number(th.dataset.col)===col;
    th.querySelector(".sortmark").textContent = on ? (dir===1?"▲":"▼") : "";
    if(on) th.setAttribute("aria-sort", dir===1?"ascending":"descending");
    else th.removeAttribute("aria-sort");
  });
}

document.querySelector("thead").addEventListener("click", function(e){
  const th=e.target.closest("th.sortable");
  if(!th) return;
  const col=Number(th.dataset.col);
  if(sortState.col===col){
    sortState.dir=-sortState.dir;
  } else {
    sortState.col=col;
    // Numbers open on the largest, names on A-Z: the useful end in each case.
    sortState.dir = th.dataset.num==="1" ? -1 : 1;
  }
  applySort();
});
["from","to"].forEach(function(id){
  el(id).addEventListener("change", function(){
    applyDates(el("from").value, el("to").value);
  });
});

/* ---------------- draw ---------------- */
function draw(){
  if(chart){ chart.remove(); chart=null; mainSeries=null; }
  overlays=[];
  clearMeasure();
  if(!view.length) return;

  const showVol=state.ind.vol, showRsi=state.ind.rsi;
  const bottomPad=(showVol?0.16:0)+(showRsi?0.16:0);

  chart = LightweightCharts.createChart(el("chart"), {
    layout:{background:{color:CHROME.panel}, textColor:CHROME.axis,
            fontFamily:"'Sofia Sans', Helvetica, Arial, sans-serif", fontSize:12},
    grid:{vertLines:{color:CHROME.grid}, horzLines:{color:CHROME.grid}},
    // Room at the top for the legend - and, in the Chart tab, the search above it.
    rightPriceScale:{borderColor:CHROME.line, scaleMargins:{top:CHART_ONLY ? 0.27 : 0.16, bottom:bottomPad||0.06}},
    // The right edge is free, so the chart can be dragged on past the last bar
    // for room beside it.
    // A resize keeps the dates in view, so the window still fills the chart
    // when its box is sized after drawing.
    timeScale:{borderColor:CHROME.line, fixLeftEdge:true, fixRightEdge:false,
      lockVisibleTimeRangeOnResize:true,
      timeVisible:onIntra, secondsVisible:false,
      tickMarkFormatter:function(time,type){
        const dt=new Date(time*1000);
        // Intraday times arrive as Eastern wall-clock seconds, so UTC fields
        // read as the exchange's own clock.
        const M=dt.toLocaleDateString("en-US",{month:"short",timeZone:"UTC"});
        // Over several sessions, each new day is marked with its date.
        if(onIntra) return type<3 ? M+" "+dt.getUTCDate() : hhmm(time);
        if(type===0) return String(dt.getUTCFullYear());
        if(type===1) return M;
        return M+" "+dt.getUTCDate();
      }},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
    // Dragging moves through time; the wheel is left to scroll the page.
    handleScroll:{mouseWheel:false, pressedMouseMove:true, horzTouchDrag:true, vertTouchDrag:false},
    handleScale:false,
    // The crosshair's time label changes only in 1D; daily keeps the default.
    localization:Object.assign({locale:"en-US", priceFormatter:function(v){ return v<0?"":num(v); }},
      onIntra ? {timeFormatter:function(t){
        return (state.range === "1d" ? "" : fmtDate(t).replace(/, \d{4}$/, "") + "  ") + hhmm(t) + " ET";
      }} : {})
  });


  // The series holds the whole base where the window is a run of it, and opens
  // on the window; otherwise just the window, as before.
  plotOff = base.indexOf(view[0]);
  const contiguous = plotOff >= 0 && base[plotOff + view.length - 1] === view[view.length - 1];
  plot = contiguous ? base : view;
  if(!contiguous) plotOff = 0;

  // Indicators run over the whole base series, then get trimmed to what is
  // plotted, which is what makes a 200-period line complete on the first bar.
  const closes = base.map(b=>b.close);
  const from = plot[0].time;
  const trim = arr => toSeries(base,arr).filter(d=>d.time>=from);

  if(state.ind.bb){
    const bb=bollinger(closes);
    [bb.up,bb.lo].forEach(function(a){
      chart.addLineSeries({color:COLORS.bb,lineWidth:1,priceLineVisible:false,lastValueVisible:false})
           .setData(trim(a));
    });
    chart.addLineSeries({color:COLORS.bb,lineWidth:1,lineStyle:2,priceLineVisible:false,lastValueVisible:false})
         .setData(trim(bb.mid));
  }

  if(state.type==="candle" && baseOHLC){
    mainSeries=chart.addCandlestickSeries({
      upColor:COLORS.up,downColor:COLORS.down,borderUpColor:COLORS.up,
      borderDownColor:COLORS.down,wickUpColor:COLORS.up,wickDownColor:COLORS.down,
      priceLineVisible:false,lastValueVisible:false});
    mainSeries.setData(plot.map(b=>({time:b.time,open:b.open,high:b.high,low:b.low,close:b.close})));
  } else {
    mainSeries=chart.addLineSeries({color:COLORS.line,lineWidth:2,
      priceLineVisible:false,lastValueVisible:false});
    mainSeries.setData(plot.map(b=>({time:b.time,value:b.close})));
  }

  MAS.forEach(function(m){
    if(!state.ind[m.key]) return;
    const ser=chart.addLineSeries({color:m.color,lineWidth:2,
      priceLineVisible:false,lastValueVisible:false});
    ser.setData(trim(sma(closes,m.period)));
    overlays.push({label:m.period+"-"+unitWord(), color:m.color, series:ser, dp:2});
  });

  if(showVol && baseOHLC){
    const s=chart.addHistogramSeries({priceFormat:{type:"volume"},priceScaleId:"vol",
      priceLineVisible:false,lastValueVisible:false});
    s.setData(plot.map(b=>({time:b.time,value:b.volume,
      color:(b.close>=b.open?"#cfe3d8":"#efd3cf")})));
    chart.priceScale("vol").applyOptions({scaleMargins:{top:showRsi?0.68:0.84,bottom:showRsi?0.16:0},visible:false});
  }
  if(showRsi){
    const s=chart.addLineSeries({color:COLORS.rsi,lineWidth:1.5,priceScaleId:"rsi",
      priceLineVisible:false,lastValueVisible:true});
    s.setData(trim(rsi(closes)));
    chart.priceScale("rsi").applyOptions({scaleMargins:{top:0.84,bottom:0},visible:false});
    // Labelled 30/70 bands, so the strip reads as an oscillator with a scale
    // instead of an unlabelled squiggle sitting under the price.
    [[30,"30"],[70,"70"]].forEach(function(pair){
      s.createPriceLine({price:pair[0],color:"#d6d0e6",lineWidth:1,lineStyle:2,
                         axisLabelVisible:true,title:pair[1]});
    });
    overlays.push({label:"RSI", color:COLORS.rsi, series:s, dp:1});
  }

  priceSeries = mainSeries;
  chart.timeScale().setVisibleLogicalRange({from: plotOff - 0.5, to: plotOff + view.length - 0.5});
  // Room past the last bar, but only a little - a third of the window at most;
  // left free, the chart drags on until the last bar reaches the far edge.
  chart.timeScale().subscribeVisibleLogicalRangeChange(function(r){
    renderMeasure();
    if(!r || !chart) return;
    const end = plot.length - 1 + Math.max(5, Math.round(view.length / 3));
    if(r.to > end + 0.5) chart.timeScale().setVisibleLogicalRange({from: r.from - (r.to - end), to: end});
  });
  updateLegend();
  wireCrosshair();
  el("dl").disabled=false;
}

/* The legend reports numbers, not just names: hovering reads each overlay at
   the crosshair, and with the pointer away it falls back to the latest bar. */
function renderLegend(bar, values){
  if(!bar) return;
  let ref = view[0].close;
  if(onIntra){
    ref = state.range === "1d" ? prevCloseFor(state.ticker) : null;
    if(!ref){ const i = base.indexOf(view[0]); ref = i > 0 ? base[i-1].close : view[0].open; }
  }
  const chg=(bar.close-ref)/ref*100;
  el("l-sym").textContent=state.ticker;
  el("l-nm").textContent=NAMES[state.ticker]||"";
  el("l-last").textContent=num(bar.close);
  const c=el("l-chg");
  c.textContent=(chg>=0?"+":"")+chg.toFixed(1)+"%";
  c.className="chg "+(chg>=0?"up":"down");

  let html="";
  if(bar.open!==undefined && bar.open!==bar.close){
    html+='<span>O '+num(bar.open)+'</span><span>H '+num(bar.high)+
          '</span><span>L '+num(bar.low)+'</span>';
  }
  (values||[]).forEach(function(v){
    const shown = (v.value===null||v.value===undefined) ? "-" : v.value.toFixed(v.dp);
    html+='<span style="color:'+v.color+'"><i style="background:'+v.color+'"></i>'+
          v.label+' <b style="color:'+v.color+'">'+shown+'</b></span>';
  });
  el("l-ind").innerHTML=html;
}

function lastValues(){
  return overlays.map(function(o){
    const d=o.series.dataByIndex
      ? o.series.dataByIndex(plotOff+view.length-1, -1)
      : null;
    return {label:o.label, color:o.color, dp:o.dp,
            value:d && d.value!==undefined ? d.value : null};
  });
}

function updateLegend(){
  if(!view.length) return;
  renderLegend(view[view.length-1], lastValues());
}

function wireCrosshair(){
  chart.subscribeCrosshairMove(function(param){
    if(!param || !param.time || !param.seriesData){ updateLegend(); return; }
    const p=priceSeries ? param.seriesData.get(priceSeries) : null;
    if(!p){ updateLegend(); return; }
    const bar = (p.open!==undefined)
      ? {open:p.open, high:p.high, low:p.low, close:p.close}
      : {close:p.value};
    const values=overlays.map(function(o){
      const d=param.seriesData.get(o.series);
      return {label:o.label, color:o.color, dp:o.dp,
              value:d && d.value!==undefined ? d.value : null};
    });
    renderLegend(bar, values);
  });
}

/* ---------------- measure (a mode, from its button) ---------------- */
const plotWidth  = () => el("chart").clientWidth-(chart?chart.priceScale("right").width():0);
const plotHeight = () => el("chart").clientHeight-(chart?chart.timeScale().height():0);

function indexAt(x){
  const lg=chart.timeScale().coordinateToLogical(x);
  if(lg===null) return null;
  return Math.max(0,Math.min(plot.length-1,Math.round(lg)));
}
function clearMeasure(){ state.measure=null; el("measure").hidden=true; }

/* A bar's x on screen, from the spacing of two bars in view. The chart's own
   answer pins anything left of the data to its edge, and once the chart has
   been dragged the bars a measurement or a line reaches can be off screen. */
function xOfLogical(lg){
  if(!chart||plot.length<2) return null;
  const ts=chart.timeScale(), r=ts.getVisibleLogicalRange();
  if(!r) return null;
  const a=Math.max(0, Math.min(plot.length-2, Math.ceil(r.from)));
  const xa=ts.logicalToCoordinate(a), xb=ts.logicalToCoordinate(a+1);
  if(xa===null||xb===null) return null;
  return xa+(lg-a)*(xb-xa);
}

function renderMeasure(){
  const m=state.measure;
  if(!m||!chart){ el("measure").hidden=true; return; }
  let x1=xOfLogical(Math.min(m.i1,m.i2)), x2=xOfLogical(Math.max(m.i1,m.i2));
  if(x1===null||x2===null){ el("measure").hidden=true; return; }
  x1=Math.max(0,x1); x2=Math.min(plotWidth(),x2);
  if(x2<=x1){ el("measure").hidden=true; return; }     // dragged out of view
  const a=plot[Math.min(m.i1,m.i2)], b=plot[Math.max(m.i1,m.i2)];
  const pct=(b.close-a.close)/a.close*100, delta=b.close-a.close, bars=Math.abs(m.i2-m.i1);
  const box=el("measure");
  box.hidden=false; box.style.left=x1+"px";
  box.style.width=Math.max(1,x2-x1)+"px"; box.style.height=plotHeight()+"px";
  const label=el("m-label");
  label.className="mlabel "+(pct>=0?"up":"down");
  label.textContent=(pct>=0?"+":"")+pct.toFixed(2)+"%  ·  "+(delta>=0?"+":"")+delta.toFixed(2)+
    "  ·  "+fmtDate(a.time)+" → "+fmtDate(b.time)+"  ·  "+bars+" "+unitWord()+(bars===1?"":"s");
}

let measureMode=false;

/* A plain drag moves the chart. A press on a line, in draw mode or in measure
   mode belongs to that instead, and is kept from the chart: it pans on mouse
   and touch events, which follow the pointer event, so those are stopped on
   their way down to it. */
(function wireMeasure(){
  const box=el("chart");
  let dragging=false, startX=0, own=false;
  box.addEventListener("pointerdown",function(e){
    own=false;
    if(e.button!==0||!chart||!view.length) return;
    const x=e.clientX-box.getBoundingClientRect().left;
    if(x>plotWidth()) return;
    if(linePointerDown(e)){ own=true; return; }   // a line was grabbed, or one is being drawn
    if(!measureMode) return;
    own=true;
    dragging=true; startX=x;
    box.setPointerCapture(e.pointerId);
    const i=indexAt(x); state.measure={i1:i,i2:i};
  }, true);
  ["mousedown","touchstart"].forEach(function(type){
    box.addEventListener(type, function(e){ if(own) e.stopPropagation(); }, true);
  });
  box.addEventListener("pointerup", function(){ own=false; });
  box.addEventListener("pointermove",function(e){
    if(!dragging) return;
    state.measure.i2=indexAt(e.clientX-box.getBoundingClientRect().left);
    renderMeasure();
  });
  box.addEventListener("pointerup",function(e){
    if(!dragging) return;
    dragging=false;
    const x=e.clientX-box.getBoundingClientRect().left;
    if(Math.abs(x-startX)<6) clearMeasure();
    else { state.measure.i2=indexAt(x); renderMeasure(); }
  });
  window.addEventListener("keydown",function(e){ if(e.key==="Escape") clearMeasure(); });
})();

/* ---------------- trend lines ---------------- */
/* Lines are pinned to a time and a price rather than to pixels, so they stay
   on the bars they were drawn against through every range, bar size and
   resize. Daily and intraday times share one clock (seconds, exchange wall
   time), so a line drawn on the 1Y chart lands in the right place on 5D too.
   They are kept per name in this browser: a line on AAPL shows in every
   screen and is still there after a reload. */
const LINES_KEY="tds-lines";
let LINES={}, selLine=null, drawMode=false, lineDrag=null, lineCanvas=null, linesPainted=false;
function loadLines(){
  try { LINES=JSON.parse(localStorage.getItem(LINES_KEY)||"{}")||{}; } catch(err){ LINES={}; }
}
function saveLines(){
  Object.keys(LINES).forEach(function(t){ if(!LINES[t].length) delete LINES[t]; });
  try { localStorage.setItem(LINES_KEY, JSON.stringify(LINES)); } catch(err){}
}
loadLines();
// Another screen on this page drew or removed a line.
window.addEventListener("storage", function(e){ if(e.key===LINES_KEY){ loadLines(); selLine=null; } });
const linesFor = () => (state.ticker && LINES[state.ticker]) || [];

/* Bar index <-> time, fractional between bars and extended past either end
   at the recent bar spacing, so a line can run off the edge of the window. */
function spacing(fromEnd){
  const n=plot.length, k=Math.min(10, n-1);
  return fromEnd ? (plot[n-1].time-plot[n-1-k].time)/k : (plot[k].time-plot[0].time)/k;
}
function timeToLogical(t){
  const n=plot.length;
  if(n<2) return 0;
  if(t<=plot[0].time) return (t-plot[0].time)/spacing(false);
  if(t>=plot[n-1].time) return n-1+(t-plot[n-1].time)/spacing(true);
  let lo=0, hi=n-1;
  while(hi-lo>1){ const mid=(lo+hi)>>1; if(plot[mid].time<=t) lo=mid; else hi=mid; }
  return lo+(t-plot[lo].time)/(plot[hi].time-plot[lo].time);
}
function logicalToTime(lg){
  const n=plot.length, i=Math.round(lg);
  if(n<2) return plot.length ? plot[0].time : 0;
  if(i<0) return plot[0].time+i*spacing(false);
  if(i>n-1) return plot[n-1].time+(i-n+1)*spacing(true);
  return plot[i].time;                     // on the chart, ends snap to a bar
}
function pointAt(e){
  const r=el("chart").getBoundingClientRect(), x=e.clientX-r.left, y=e.clientY-r.top;
  const lg=chart.timeScale().coordinateToLogical(x), p=mainSeries.coordinateToPrice(y);
  if(lg===null||p===null) return null;
  return {lg:Math.round(lg), t:logicalToTime(lg), p:p, x:x, y:y};
}
function linePixels(){
  if(!chart||!mainSeries||plot.length<2) return [];
  return linesFor().map(function(L){
    const x1=xOfLogical(timeToLogical(L.t1)), x2=xOfLogical(timeToLogical(L.t2));
    const y1=mainSeries.priceToCoordinate(L.p1), y2=mainSeries.priceToCoordinate(L.p2);
    if([x1,x2,y1,y2].some(function(v){ return v===null||v===undefined; })) return null;
    return {L:L, x1:x1, y1:y1, x2:x2, y2:y2};
  }).filter(Boolean);
}
function hitLine(x, y){
  let best=null;
  linePixels().forEach(function(q){
    if(Math.hypot(x-q.x1,y-q.y1)<9){ best={L:q.L,end:1}; return; }
    if(Math.hypot(x-q.x2,y-q.y2)<9){ best={L:q.L,end:2}; return; }
    if(best) return;
    const dx=q.x2-q.x1, dy=q.y2-q.y1, len2=dx*dx+dy*dy;
    const u=len2 ? Math.max(0,Math.min(1,((x-q.x1)*dx+(y-q.y1)*dy)/len2)) : 0;
    if(Math.hypot(x-(q.x1+u*dx), y-(q.y1+u*dy))<6) best={L:q.L,end:0};
  });
  return best;
}

function renderLines(){
  const box=el("chart");
  if(!lineCanvas){
    lineCanvas=document.createElement("canvas");
    lineCanvas.className="lines";
    box.parentNode.insertBefore(lineCanvas, box.nextSibling);
  }
  const dpr=window.devicePixelRatio||1, w=box.clientWidth, h=box.clientHeight;
  if(lineCanvas.width!==Math.round(w*dpr) || lineCanvas.height!==Math.round(h*dpr)){
    lineCanvas.width=Math.round(w*dpr); lineCanvas.height=Math.round(h*dpr);
    lineCanvas.style.width=w+"px"; lineCanvas.style.height=h+"px";
  }
  const g=lineCanvas.getContext("2d");
  g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,w,h);
  const pts=linePixels();
  linesPainted=pts.length>0;
  if(!pts.length) return;
  g.save();
  g.beginPath(); g.rect(0,0,plotWidth(),plotHeight()); g.clip();
  g.lineCap="round";
  pts.forEach(function(q){
    const on=q.L===selLine;
    g.strokeStyle=CHROME.brand; g.lineWidth=on?2.5:2;
    g.beginPath(); g.moveTo(q.x1,q.y1); g.lineTo(q.x2,q.y2); g.stroke();
    if(on) [[q.x1,q.y1],[q.x2,q.y2]].forEach(function(c){
      g.beginPath(); g.arc(c[0],c[1],4.5,0,Math.PI*2);
      g.fillStyle=CHROME.panel; g.fill(); g.lineWidth=2; g.stroke();
    });
  });
  g.restore();
}

/* Repainted every frame while there is something to show: the chart rescales
   itself on live ticks and resizes without telling anyone, and a handful of
   lines costs nothing to redraw. */
(function lineLoop(){
  if(chart && (linesPainted || linesFor().length)) renderLines();
  const clear=el("drawclear"), none=!linesFor().length;
  if(clear.hidden!==none) clear.hidden=none;
  requestAnimationFrame(lineLoop);
})();

/* Draw and Measure each take over the drag; with neither on, it moves the chart. */
function setMode(draw, measure){
  drawMode=draw; measureMode=measure;
  el("drawbtn").setAttribute("aria-pressed", String(draw));
  el("measurebtn").setAttribute("aria-pressed", String(measure));
  el("chart").title = draw ? "Drag to draw a line"
    : measure ? "Drag across the chart to measure a move" : "Drag to move the chart back and forth";
  if(!measure) clearMeasure();
}
el("drawbtn").addEventListener("click", function(){ setMode(!drawMode, false); });
el("measurebtn").addEventListener("click", function(){ setMode(false, !measureMode); });
el("drawclear").addEventListener("click", function(){
  if(state.ticker) delete LINES[state.ticker];
  selLine=null; saveLines(); renderLines();
});

/* Grabbing a line works in either mode; starting a new one only in draw mode.
   Returns true when the press belongs to the lines, so measuring stays out. */
function linePointerDown(e){
  const at=pointAt(e);
  if(!at) return false;
  const hit=hitLine(at.x, at.y);
  if(hit){
    selLine=hit.L;
    lineDrag={L:hit.L, end:hit.end, from:at, orig:Object.assign({}, hit.L), fresh:false};
  } else if(drawMode){
    const L={t1:at.t, p1:at.p, t2:at.t, p2:at.p};
    (LINES[state.ticker]=LINES[state.ticker]||[]).push(L);
    selLine=L;
    lineDrag={L:L, end:2, from:at, fresh:true};
  } else {
    selLine=null;
    return false;
  }
  clearMeasure();
  el("chart").setPointerCapture(e.pointerId);
  renderLines();
  return true;
}
el("chart").addEventListener("pointermove", function(e){
  if(!chart||!mainSeries) return;
  const at=pointAt(e);
  if(!at) return;
  if(!lineDrag){
    const hit=hitLine(at.x, at.y);
    el("chart").style.cursor = hit ? (hit.end ? "grab" : "move") : "";
    return;
  }
  const L=lineDrag.L, o=lineDrag.orig;
  if(lineDrag.end===1){ L.t1=at.t; L.p1=at.p; }
  else if(lineDrag.end===2){ L.t2=at.t; L.p2=at.p; }
  else {
    // The whole line moves by whole bars, so its ends stay on bars.
    const dl=at.lg-lineDrag.from.lg, dp=at.p-lineDrag.from.p;
    L.t1=logicalToTime(Math.round(timeToLogical(o.t1))+dl); L.p1=o.p1+dp;
    L.t2=logicalToTime(Math.round(timeToLogical(o.t2))+dl); L.p2=o.p2+dp;
  }
  renderLines();
});
el("chart").addEventListener("pointerup", function(e){
  if(!lineDrag) return;
  const d=lineDrag; lineDrag=null;
  // A click in draw mode without a drag leaves no stray dot behind.
  if(d.fresh && d.L.t1===d.L.t2 && Math.abs(e.clientY-el("chart").getBoundingClientRect().top-d.from.y)<4){
    const arr=LINES[state.ticker]||[], i=arr.indexOf(d.L);
    if(i>=0) arr.splice(i,1);
    selLine=null;
  }
  saveLines(); renderLines();
});
window.addEventListener("keydown", function(e){
  const tag=(e.target && e.target.tagName)||"";
  if(tag==="INPUT"||tag==="TEXTAREA") return;
  if((e.key==="Delete"||e.key==="Backspace") && selLine){
    const arr=linesFor(), i=arr.indexOf(selLine);
    if(i>=0){ arr.splice(i,1); saveLines(); }
    selLine=null; renderLines(); e.preventDefault();
  } else if(e.key==="Escape"){
    if(drawMode || measureMode) setMode(false, false);
    selLine=null; renderLines();
  }
});

/* ---------------- navigator ---------------- */
function navSeriesData(){
  // The navigator shows the entire record, not a copy of the window above it.
  return weekly.length ? weekly : daily;
}

function drawNav(){
  if(navChart){ navChart.remove(); navChart=null; }
  if(!navSeriesData().length) return;
  navChart=LightweightCharts.createChart(el("nav"),{
    height:48,
    layout:{background:{color:CHROME.panel},textColor:CHROME.muted,
            fontFamily:"'Sofia Sans', Helvetica, Arial, sans-serif",fontSize:10},
    grid:{vertLines:{visible:false},horzLines:{visible:false}},
    rightPriceScale:{visible:false}, leftPriceScale:{visible:false},
    timeScale:{borderVisible:false,fixLeftEdge:true,fixRightEdge:true,
      tickMarkFormatter:function(time,type){
        const dt=new Date(time*1000);
        return type<=1?String(dt.getUTCFullYear()):"";
      }},
    crosshair:{vertLine:{visible:false,labelVisible:false},horzLine:{visible:false,labelVisible:false}},
    handleScroll:false, handleScale:false
  });
  navChart.addAreaSeries({lineColor:"#7d93ad",topColor:"rgba(31,78,121,.20)",
    bottomColor:"rgba(31,78,121,.02)",lineWidth:1,priceLineVisible:false,
    lastValueVisible:false,crosshairMarkerVisible:false})
    .setData(navSeriesData().map(b=>({time:b.time,value:b.close})));
  navChart.timeScale().fitContent();
  syncNavWindow();
}

const navIndexOfTime = t => {
  const nav=navSeriesData();
  let lo=0, hi=nav.length-1;
  while(lo<hi){ const mid=(lo+hi)>>1; if(nav[mid].time<t) lo=mid+1; else hi=mid; }
  return lo;
};

function syncNavWindow(){
  const win=el("navwin");
  if(!navChart||!navSeriesData().length||!view.length){
    win.hidden=el("navshade-l").hidden=el("navshade-r").hidden=true; return;
  }
  const ts=navChart.timeScale();
  let x1=ts.logicalToCoordinate(navIndexOfTime(view[0].time));
  let x2=ts.logicalToCoordinate(navIndexOfTime(view[view.length-1].time));
  if(x1===null||x2===null){
    win.hidden=el("navshade-l").hidden=el("navshade-r").hidden=true; return;
  }
  const w=el("nav").clientWidth;
  x1=Math.max(0,Math.min(w,x1)); x2=Math.max(0,Math.min(w,x2));
  const left=Math.min(x1,x2), width=Math.max(6,Math.abs(x2-x1));
  win.hidden=false; win.style.left=left+"px"; win.style.width=width+"px";
  paintShades(left,width,w);
}
function paintShades(left,width,w){
  const l=el("navshade-l"), r=el("navshade-r");
  l.hidden=r.hidden=false;
  l.style.width=Math.max(0,left)+"px";
  r.style.width=Math.max(0,w-(left+width))+"px";
}
function navIndexAt(x){
  const lg=navChart.timeScale().coordinateToLogical(x);
  if(lg===null) return 0;
  return Math.max(0,Math.min(navSeriesData().length-1,Math.round(lg)));
}

(function wireNav(){
  const box=el("navbox"), win=el("navwin");
  let mode=null, grabDX=0;
  const xIn=e=>e.clientX-el("nav").getBoundingClientRect().left;
  box.addEventListener("pointerdown",function(e){
    if(!navChart||!navSeriesData().length) return;
    const x=xIn(e);
    const left=parseFloat(win.style.left)||0, width=parseFloat(win.style.width)||0;
    try{ box.setPointerCapture(e.pointerId); }catch(err){}
    if(!win.hidden&&Math.abs(x-left)<=6) mode="l";
    else if(!win.hidden&&Math.abs(x-(left+width))<=6) mode="r";
    else if(!win.hidden&&x>left&&x<left+width){ mode="move"; grabDX=x-left; }
    else { mode="move"; grabDX=width/2; win.style.left=Math.max(0,x-width/2)+"px"; }
    win.classList.add("dragging");
  });
  box.addEventListener("pointermove",function(e){
    if(!mode) return;
    const x=xIn(e), w=el("nav").clientWidth;
    let left=parseFloat(win.style.left), width=parseFloat(win.style.width);
    if(mode==="move") left=Math.max(0,Math.min(w-width,x-grabDX));
    else if(mode==="l"){ const r=left+width; left=Math.max(0,Math.min(r-12,x)); width=r-left; }
    else { width=Math.max(12,Math.min(w-left,x-left)); }
    win.style.left=left+"px"; win.style.width=width+"px";
    paintShades(left,width,w);
  });
  box.addEventListener("pointerup",function(){
    if(!mode) return;
    mode=null; win.classList.remove("dragging");
    const left=parseFloat(win.style.left), width=parseFloat(win.style.width);
    const nav=navSeriesData();
    const i1=navIndexAt(left), i2=navIndexAt(left+width);
    if(i2-i1<2) return;
    // Hand the dragged window to the date path so it picks daily or weekly.
    applyDates(isoOf(nav[i1].time), isoOf(nav[Math.min(i2,nav.length-1)].time));
  });
})();

/* ---------------- toolbar ---------------- */
function segment(id,fn){
  el(id).addEventListener("click",function(e){
    const b=e.target.closest("button");
    if(!b) return;
    Array.prototype.forEach.call(e.currentTarget.children,function(x){
      x.setAttribute("aria-pressed",String(x===b));
    });
    fn(b.dataset.v);
  });
}
segment("range",function(v){ applyRange(v); });
segment("type", function(v){ state.type=v; draw(); });
el("inds").addEventListener("change",function(e){
  state.ind[e.target.dataset.v]=e.target.checked;
  draw();
});

/* ---------------- PNG export ---------------- */
/* The saved image carries the watermark where it sits on screen. The mark
   is SVG, so it is drawn through an image - which loads asynchronously - with
   its colour written in, since an image cannot inherit currentColor. */
function exportPng(){
  if(!chart||!view.length) return;
  const svg=document.querySelector(".wmark svg");
  if(!svg){ exportPngWith(null); return; }
  const r=svg.getBoundingClientRect(), c=el("chart").getBoundingClientRect();
  const clone=svg.cloneNode(true);
  clone.setAttribute("xmlns","http://www.w3.org/2000/svg");
  clone.setAttribute("width", r.width); clone.setAttribute("height", r.height);
  const text=new XMLSerializer().serializeToString(clone).replace(/currentColor/g, CHROME.ink);
  const img=new Image();
  img.onload=function(){ exportPngWith({img:img, x:r.left-c.left, y:r.top-c.top, w:r.width, h:r.height}); };
  img.onerror=function(){ exportPngWith(null); };
  img.src="data:image/svg+xml;charset=utf-8,"+encodeURIComponent(text);
}

function exportPngWith(mark){
  const src=chart.takeScreenshot();
  const s=src.width/el("chart").clientWidth;
  const pad=Math.round(20*s), footH=Math.round(42*s);
  const w=src.width+pad*2, h=src.height+footH+pad*2;
  const c=document.createElement("canvas");
  c.width=w; c.height=h;
  const g=c.getContext("2d");
  const F='"Sofia Sans", "Segoe UI", Helvetica, Arial, sans-serif';
  g.fillStyle=CHROME.panel; g.fillRect(0,0,w,h);
  g.drawImage(src,pad,pad);
  if(mark && mark.w){
    g.save();
    g.globalAlpha=parseFloat(CHROME.watermark)||0.06;
    g.drawImage(mark.img, pad+mark.x*s, pad+mark.y*s, mark.w*s, mark.h*s);
    g.restore();
  }

  const last=view[view.length-1], first=view[0];
  const chg=(last.close-first.close)/first.close*100;

  if(state.measure&&!el("measure").hidden){
    const box=el("measure");
    const bx=pad+box.offsetLeft*s, bw=box.offsetWidth*s, bh=plotHeight()*s;
    g.fillStyle="rgba(31,78,121,.10)"; g.fillRect(bx,pad,bw,bh);
    g.strokeStyle="rgba(31,78,121,.55)";
    g.setLineDash([Math.round(4*s),Math.round(3*s)]);
    g.lineWidth=Math.max(1,Math.round(s));
    g.beginPath(); g.moveTo(bx,pad); g.lineTo(bx,pad+bh);
    g.moveTo(bx+bw,pad); g.lineTo(bx+bw,pad+bh); g.stroke();
    g.setLineDash([]);
    const text=el("m-label").textContent;
    g.font="600 "+Math.round(12*s)+"px "+F;
    const tw2=g.measureText(text).width, bxw=tw2+Math.round(18*s), bxh=Math.round(24*s);
    let lx2=Math.max(pad,Math.min(w-pad-bxw,bx+bw/2-bxw/2));
    const ly=pad+Math.round(8*s);
    const a=plot[Math.min(state.measure.i1,state.measure.i2)];
    const b2=plot[Math.max(state.measure.i1,state.measure.i2)];
    g.fillStyle=(b2.close-a.close)>=0?COLORS.up:COLORS.down;
    g.beginPath();
    if(g.roundRect) g.roundRect(lx2,ly,bxw,bxh,Math.round(6*s)); else g.rect(lx2,ly,bxw,bxh);
    g.fill();
    g.fillStyle="#ffffff"; g.textBaseline="middle";
    g.fillText(text,lx2+Math.round(9*s),ly+bxh/2);
    g.textBaseline="alphabetic";
  }

  const drawn=linePixels();
  if(drawn.length){
    g.save();
    g.beginPath(); g.rect(pad,pad,plotWidth()*s,plotHeight()*s); g.clip();
    g.strokeStyle=CHROME.brand; g.lineWidth=2*s; g.lineCap="round";
    drawn.forEach(function(q){
      g.beginPath(); g.moveTo(pad+q.x1*s,pad+q.y1*s); g.lineTo(pad+q.x2*s,pad+q.y2*s); g.stroke();
    });
    g.restore();
  }

  let y=pad+Math.round(28*s);
  const lx=pad+Math.round(14*s);
  g.fillStyle=CHROME.ink; g.font="700 "+Math.round(19*s)+"px "+F;
  g.fillText(state.ticker,lx,y);
  const tw=g.measureText(state.ticker).width;
  const nm=NAMES[state.ticker]||"";
  if(nm){
    g.fillStyle=CHROME.muted; g.font="400 "+Math.round(13*s)+"px "+F;
    g.fillText(nm,lx+tw+Math.round(8*s),y);
  }
  y+=Math.round(21*s);
  g.fillStyle=CHROME.ink; g.font="600 "+Math.round(14*s)+"px "+F;
  const priceText=num(last.close);
  g.fillText(priceText,lx,y);
  const pw=g.measureText(priceText).width;
  g.fillStyle=chg>=0?COLORS.up:COLORS.down;
  g.fillText((chg>=0?"+":"")+chg.toFixed(1)+"%",lx+pw+Math.round(8*s),y);

  // The export carries the same values the legend shows, not just the names,
  // so a saved chart is readable without the app beside it.
  const inds=lastValues();
  if(inds.length){
    y+=Math.round(18*s);
    g.font="400 "+Math.round(11.5*s)+"px "+F;
    let x=lx;
    inds.forEach(function(item){
      const text=item.label+" "+
        (item.value===null||item.value===undefined ? "-" : item.value.toFixed(item.dp));
      g.fillStyle=item.color;
      g.fillRect(x,y-Math.round(7.5*s),Math.round(8*s),Math.round(8*s));
      g.fillStyle=CHROME.muted;
      g.fillText(text,x+Math.round(12*s),y);
      x+=Math.round(12*s)+g.measureText(text).width+Math.round(14*s);
    });
  }

  const fy=pad+src.height+Math.round(26*s);
  g.strokeStyle=CHROME.line; g.lineWidth=Math.max(1,Math.round(s));
  g.beginPath();
  g.moveTo(pad,pad+src.height+Math.round(11*s));
  g.lineTo(w-pad,pad+src.height+Math.round(11*s));
  g.stroke();
  g.fillStyle=COLORS.credit;
  g.font="600 "+Math.round(10.5*s)+"px "+F;
  const credit="";
  let cx=w-pad;
  for(let i=credit.length-1;i>=0;i--){
    const ch=credit[i];
    cx-=g.measureText(ch).width+(i===credit.length-1?0:Math.round(1.8*s));
    g.fillText(ch,cx,fy);
  }

  c.toBlob(function(blob){
    const a=document.createElement("a");
    a.href=URL.createObjectURL(blob);
    a.download=state.ticker+"-"+view.length+"bars.png";
    a.click();
    setTimeout(function(){ URL.revokeObjectURL(a.href); },4000);
  },"image/png");
}
el("dl").addEventListener("click",exportPng);

function refit(){
  // While the chart is folded away its container measures zero, and resizing
  // the chart to that would leave it collapsed when it comes back.
  // The Chart tab has no table row: its chart is always open.
  if(!CHART_ONLY && (!chartRow || chartRow.hidden)) return;
  if(!CHART_ONLY) sizeChartCell();
  if(!el("chart").clientWidth) return;      // still hidden; the observer calls again
  // Height too: a chart created while hidden measured its box as zero tall.
  if(chart){ chart.applyOptions({width:el("chart").clientWidth, height:el("chart").clientHeight});
             renderMeasure(); }
  if(navChart){
    navChart.applyOptions({width:el("nav").clientWidth});
    navChart.timeScale().fitContent();
    syncNavWindow();
  }
}
window.addEventListener("resize", refit);

/* Follow the page's theme. The toggle reruns the page but leaves this frame
   alone, so the frame watches for the change itself and recolours in place -
   the table, its sort and the open chart all stay as they were. */
setInterval(function(){
  const s = pageScheme();
  if(s === SCHEME) return;
  SCHEME = s;
  paintTokens(s);
  Object.assign(CHROME, THEMES[s]);
  refreshColors();
  if(chart) draw();
}, 250);

/* A window resize is not the only way this panel changes width: switching to
   another momentum tab hides it and switching back reveals it, and neither
   fires a resize. Watching the box itself catches both, so a chart drawn while
   the tab was hidden still lays out correctly once it is shown. */
if(window.ResizeObserver){
  // The Chart tab hides its table, so there it is the chart's own box.
  new ResizeObserver(function(){ refit(); }).observe(
    document.querySelector(CHART_ONLY ? ".chartbox" : ".tablewrap"));
}

/* ---------------- live prices ---------------- */
/* Prices arrive from the page around this frame (livequotes.py), never from
   the browser calling the data vendor, so the API token stays on the server.
   Nothing here touches the screens: they are built on daily closes, and this
   only refreshes the numbers shown against them. */
const LIVE = {rows: {}, q: null, gotAt: 0, open: false, ok: true, fresh: true,
              every: 2, day: null, err: "", srvAge: null};
document.querySelectorAll("#rows tr[data-t]").forEach(function(tr){
  LIVE.rows[tr.dataset.t] = {price: tr.querySelector("td.lp"), chg: tr.querySelector("td.lc")};
});
const fmtPx = v => v.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});

function setChg(cell, pct){
  if(!cell) return;
  if(pct === null || !isFinite(pct)){ cell.innerHTML = "&ndash;"; cell.dataset.v = ""; cell.className = "lc"; return; }
  cell.textContent = (pct >= 0 ? "+" : "−") + Math.abs(pct).toFixed(2) + "%";
  cell.dataset.v = String(pct);
  cell.className = "lc " + (pct >= 0 ? "pos" : "neg");
}

// Until the first tick - and all evening - the change shown is the last session's.
Object.keys(LIVE.rows).forEach(function(t){
  const d = DATA[t];
  if(!d || !d.c || d.c.length < 2) return;
  const a = d.c[d.c.length - 2], b = d.c[d.c.length - 1];
  if(a) setChg(LIVE.rows[t].chg, (b / a - 1) * 100);
});

function flash(cell, up){
  cell.classList.remove("tick-up", "tick-dn");
  void cell.offsetWidth;                       // restart the animation
  cell.classList.add(up ? "tick-up" : "tick-dn");
}

/* Today's bar, from the same tick. The screener carries the session's open,
   high and low as well as the last trade, so the candle is exact, not a guess.
   Returns "same" when today's bar was updated, "new" when it was added. */
function mergeLive(t){
  const q = LIVE.q && LIVE.q[t];
  if(!q || !LIVE.open || !LIVE.day || !daily.length) return null;
  const p = LIVE.day.split("-");
  const ts = Date.UTC(+p[0], +p[1] - 1, +p[2]) / 1000;
  const last = q[0];
  const o = q[2] || last, h = Math.max(q[3] || last, last), l = Math.min(q[4] || last, last);
  const tail = daily[daily.length - 1];
  if(tail.time === ts){
    tail.open = o; tail.high = h; tail.low = l; tail.close = last; tail.volume = q[5] || tail.volume;
    return "same";
  }
  if(tail.time < ts){
    daily.push({time: ts, open: o, high: h, low: l, close: last, volume: q[5] || 0});
    return "new";
  }
  return null;
}

function liveChart(t){
  const tail = daily[daily.length - 1];
  const atEnd = !onWeekly && view.length && view[view.length - 1] === tail;
  const what = mergeLive(t);
  if(what === "new"){
    // First tick of a new session: the bar is appended, so redraw once.
    if(atEnd) view.push(daily[daily.length - 1]);
    setDateBounds();
    if(atEnd) draw();
    return;
  }
  if(what !== "same" || !atEnd || !mainSeries) return;
  const bar = daily[daily.length - 1];
  if(state.type === "candle")
    mainSeries.update({time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close});
  else
    mainSeries.update({time: bar.time, value: bar.close});
  updateLegend();
}

function applyQuotes(d){
  if(!d) return;
  if(SRC === "yahoo"){ applyYahoo(d); return; }
  LIVE.gotAt = Date.now(); LIVE.open = !!d.open; LIVE.ok = d.ok !== false;
  LIVE.fresh = d.fresh !== false; LIVE.every = d.every || 2; LIVE.day = d.day || null;
  LIVE.err = d.err || "";
  LIVE.srvAge = (d.age === undefined) ? 0 : d.age;
  if(d.open && d.q) LIVE.q = d.q;
  paintLive();
  takeIntraday(d);
  if(!d.open || !d.q) return;
  Object.keys(d.q).forEach(function(t){
    const r = LIVE.rows[t];
    if(!r) return;
    const q = d.q[t], last = q[0], prev = q[1], age = q[7];
    if(r.price && last){
      const was = parseFloat(r.price.dataset.v);
      if(isFinite(was) && Math.abs(was - last) > 1e-9) flash(r.price, last > was);
      r.price.textContent = fmtPx(last);
      r.price.dataset.v = String(last);
      // A name that has not traded for two minutes is quiet, not broken - say so.
      const quiet = age !== null && age !== undefined && age > 120;
      r.price.classList.toggle("stale", quiet);
      r.price.title = quiet ? "No trade for " + Math.round(age) + "s" : "";
    }
    if(prev && last) setChg(r.chg, (last / prev - 1) * 100);
  });
  if(state.ticker && d.q[state.ticker]){
    if(onIntra) mergeIntraTick(d.q[state.ticker], d.et_offset);
    else liveChart(state.ticker);
  }
}

/* A Yahoo-sourced name: futures, an index, FX, crypto or a non-US listing.
   Its price arrives on the same feed, refreshed about every 15 seconds. */
function applyYahoo(d){
  const t = state.ticker;
  const y = d.yq && t ? d.yq[t] : null;
  LIVE.gotAt = Date.now();
  LIVE.ok = !!y || !d.yerr;
  LIVE.err = d.yerr || "";
  takeIntraday(d);
  if(y && y.q && y.q[0]){
    LIVE.open = true; LIVE.day = y.day; LIVE.q = {}; LIVE.q[t] = y.q;
    const r = LIVE.rows[t], last = y.q[0], prev = y.q[1];
    if(r && r.price){
      const was = parseFloat(r.price.dataset.v);
      r.price.textContent = fmtPx(last); r.price.dataset.v = String(last);
      if(isFinite(was) && Math.abs(was - last) > 1e-9) flash(r.price, last > was);
    }
    if(r && prev) setChg(r.chg, (last / prev - 1) * 100);
    if(!onIntra) liveChart(t);
  }
  paintYahoo();
}

function paintYahoo(){
  // No live or delayed badge for these names.
  el("live").hidden = true;
}

/* The state of the feed, always visible, so a stall never passes for a quiet
   market: green while ticks arrive, amber when they lag, red when they stop. */
function paintLive(){
  if(SRC === "yahoo"){ paintYahoo(); return; }
  const box = el("live");
  box.hidden = false;
  // Ticks keep arriving even if the vendor stops answering, so the age that
  // matters is the held quotes': how old they were when sent, plus the time
  // since this tick arrived.
  const since = (Date.now() - LIVE.gotAt) / 1000;
  const age = LIVE.srvAge === null ? null : LIVE.srvAge + since;
  let cls = "on", text = "Live · every " + LIVE.every + "s", tip = "";
  if(!LIVE.open){ cls = "closed"; text = "Market closed · last close"; }
  else if(!LIVE.ok){ cls = "down"; text = "Feed paused"; tip = LIVE.err; }
  else if(age === null){ cls = "lag"; text = "Connecting"; tip = "Waiting for the first prices"; }
  else if(!LIVE.fresh){ cls = "lag"; text = "No live trades"; tip = "Quotes are not moving — the market may be shut today."; }
  else if(age > Math.max(30, LIVE.every * 10)){ cls = "down"; text = "Feed paused"; tip = "No update for " + Math.round(age) + "s"; }
  else if(age > Math.max(6, LIVE.every * 3)){ cls = "lag"; text = "Live · lagging"; tip = "Last update " + Math.round(age) + "s ago"; }
  box.className = "live " + cls;
  box.title = tip;
  box.innerHTML = "<i></i>" + text;
}
setInterval(function(){ if(LIVE.gotAt) paintLive(); }, 1000);


/* ---------------- 1D: today's intraday candles ---------------- */
/* The candles come from the server, which holds the API token: this panel asks
   by dispatching `tds-want` on the page, and the feed answers on its next run.
   Every tick the server echoes what it is serving this panel, and a mismatch
   is simply asked again - so a request lost at start-up corrects itself
   within one tick instead of leaving the chart waiting. */
const INTRA = {want: null, bars: [], key: null, stamp: null, sec: 60};
const hhmm = t => { const d = new Date(t * 1000);
  return String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0"); };

function askIntraday(){
  try { window.parent.dispatchEvent(new CustomEvent("tds-want", {detail: {panel: PANEL, want: INTRA.want}})); }
  catch(err){}
}

function showMsg(text){
  const m = el("chartmsg");
  m.hidden = !text;
  if(text) m.textContent = text;
}

function setIntraUI(on){
  document.querySelector(".dates").hidden = on;
  el("navbox").hidden = on;
  document.querySelectorAll("#inds .lb").forEach(function(s){
    s.textContent = s.dataset.p + "-" + (on ? "bar" : "day");
  });
}

/* `have` is the newest candle this chart already holds. While it matches the
   server's, each tick carries only the forming candle rather than the whole
   set again - which for hourly bars over six months is a thousand of them. */
function enterIntraday(){
  if(!state.ticker) return;
  const R = RANGES[state.range];
  onIntra = true;
  setIntraUI(true);
  const key = state.ticker + "|" + state.iv + "|" + R.span;
  const held = INTRA.key === key && INTRA.bars.length > 0;
  INTRA.want = {ticker: state.ticker, size: state.iv, span: R.span, have: held ? INTRA.stamp : null};
  askIntraday();
  if(held){ showMsg(null); drawIntraday(); }
  else {
    INTRA.key = key; INTRA.bars = []; INTRA.stamp = null;
    if(chart){ chart.remove(); chart = null; mainSeries = null; }
    showMsg("Loading " + IV_TITLE[state.iv].toLowerCase() + "\u2026");
  }
  // The latest payload may already carry them.
  try { if(window.parent.__tdsQuotes) takeIntraday(window.parent.__tdsQuotes); } catch(err){}
}

function leaveIntraday(){
  onIntra = false;
  INTRA.want = null;
  askIntraday();
  setIntraUI(false);
  showMsg(null);
}

/* Every candle held is the base, so averages are complete from the left
   edge; the view is the range's last N sessions. */
function drawIntraday(){
  const n = (RANGES[state.range] || {}).n || 1;
  base = INTRA.bars; view = lastSessions(INTRA.bars, n);
  onWeekly = false; unit = "bar"; baseOHLC = true;
  draw();
}

function lastSessions(bars, n){
  let i = bars.length - 1, seen = 0, day = null;
  for(; i >= 0; i--){
    const d = Math.floor(bars[i].time / 86400);
    if(d !== day){ if(++seen > n) break; day = d; }
  }
  return i < 0 ? bars : bars.slice(i + 1);
}

function takeIntraday(d){
  const echo = d.want && d.want[PANEL];
  if(!onIntra || !INTRA.want){
    if(echo) askIntraday();          // the server still serves a request this panel dropped
    return;
  }
  if(!echo || echo.ticker !== INTRA.want.ticker || echo.size !== INTRA.want.size
     || (echo.span || "1D") !== INTRA.want.span){
    askIntraday();
    return;
  }
  const key = INTRA.want.ticker + "|" + INTRA.want.size + "|" + INTRA.want.span;
  const pack = d.intraday && d.intraday[key];
  if(!pack) return;
  if(pack.err){ if(!INTRA.bars.length) showMsg("Couldn\u2019t load the candles \u2014 " + pack.err); return; }
  if(!pack.bars || !pack.bars.length){
    showMsg(state.range === "1d" ? "No trades yet today." : "No candles for this range.");
    return;
  }
  INTRA.sec = pack.sec || 60;
  const fresh = pack.bars.map(b => ({time: b[0], open: b[1], high: b[2], low: b[3],
                                     close: b[4], volume: b[5]}));
  if(INTRA.key === key && INTRA.stamp === pack.stamp && INTRA.bars.length){
    // Same bucket as last re-sync: take the vendor's numbers for the forming
    // candle in place, rather than redraw and lose a measurement in progress.
    const tail = INTRA.bars[INTRA.bars.length - 1], v = fresh[fresh.length - 1];
    if(tail.time === v.time){
      // The vendor's open, extremes and volume are authoritative, but its
      // close can be fifteen seconds old; a live tick's is at most two. Taking
      // the vendor's close here made the chart step back in time.
      tail.open = v.open; tail.high = Math.max(tail.high, v.high); tail.low = Math.min(tail.low, v.low);
      if(!tail.ticked) tail.close = v.close;
      tail.volume = v.volume;
      if(mainSeries) updateSeriesBar(tail);
    }
    return;
  }
  // Only the forming candle, for a set this chart no longer holds: the full
  // set follows once the server sees the request.
  if(pack.tail) return;
  // A new batch: carry the live close across, and keep a bucket the ticks
  // have already opened that the vendor has not sent yet.
  const prev = (INTRA.key === key && INTRA.bars.length) ? INTRA.bars[INTRA.bars.length - 1] : null;
  if(prev && prev.ticked){
    const nt = fresh[fresh.length - 1];
    if(prev.time === nt.time){
      nt.close = prev.close; nt.high = Math.max(nt.high, prev.high); nt.low = Math.min(nt.low, prev.low);
      nt.ticked = true;
    } else if(prev.time > nt.time){
      fresh.push(prev);
    }
  }
  INTRA.key = key; INTRA.stamp = pack.stamp; INTRA.bars = fresh;
  if(INTRA.want.have !== pack.stamp){ INTRA.want.have = pack.stamp; askIntraday(); }
  showMsg(null);
  drawIntraday();
}

function updateSeriesBar(bar){
  if(state.type === "candle")
    mainSeries.update({time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close});
  else
    mainSeries.update({time: bar.time, value: bar.close});
  updateLegend();
}

/* A tick grows the forming candle, or opens the next one when its bucket rolls
   over. The bucket comes from the quote's own timestamp shifted to Eastern by
   the server's offset, so this machine's clock and timezone play no part. */
function mergeIntraTick(q, offset){
  if(!INTRA.bars.length || !mainSeries || !q || !q[6]) return;
  const sec = INTRA.sec || 60;
  const et = Math.floor(q[6] / 1000) + (offset || 0);
  const last = INTRA.bars[INTRA.bars.length - 1], px = q[0];
  const bucket = last.time + Math.floor((et - last.time) / sec) * sec;
  if(!px || bucket < last.time) return;
  let bar = last;
  if(bucket === last.time){
    last.high = Math.max(last.high, px); last.low = Math.min(last.low, px); last.close = px;
  } else {
    bar = {time: bucket, open: px, high: px, low: px, close: px, volume: 0};
    INTRA.bars.push(bar);
    if(view !== INTRA.bars && view[view.length - 1] === last) view.push(bar);
  }
  bar.ticked = true;                   // its close is live; a re-sync must not wind it back
  updateSeriesBar(bar);
}

/* The previous session's close, for the 1D legend's change. */
function prevCloseFor(t){
  const q = LIVE.q && LIVE.q[t];
  if(q && q[1]) return q[1];
  const d = DATA[t];
  if(!d || !d.c || d.c.length < 2) return null;
  const p = (LIVE.day || "").split("-");
  const today = p.length === 3 ? Date.UTC(+p[0], +p[1] - 1, +p[2]) / 1000 : null;
  return d.t[d.t.length - 1] === today ? d.c[d.c.length - 2] : d.c[d.c.length - 1];
}

/* A filter change rebuilds this panel. Remember the range and bar size on
   the page, so the chart comes back where it was rather than at the default. */
function rememberIntraday(){
  try {
    const host = window.parent;
    host.__tdsIntraday = host.__tdsIntraday || {};
    host.__tdsIntraday[PANEL] = {range: state.range, iv: state.iv};
  } catch(err){}
}

segment("iv", function(v){
  state.iv = v;
  applyRange(state.range);
});

// A rebuilt panel starts by clearing any request its predecessor left behind,
// then restores 1D if that was where the chart was.
askIntraday();
try {
  const saved = window.parent.__tdsIntraday && window.parent.__tdsIntraday[PANEL];
  if(saved){
    state.range = RANGES[saved.range] ? saved.range : "1d";
    state.iv = saved.iv || saved.size || RANGES[state.range].def;
    if(RANGES[state.range].iv.indexOf(state.iv) < 0) state.iv = RANGES[state.range].def;
    [...el("range").children].forEach(b => b.setAttribute("aria-pressed", String(b.dataset.v === state.range)));
  }
} catch(err){}
renderIv();

/* ---------------- symbol search (Chart tab) ---------------- */
/* Typing asks the server - which calls Yahoo's symbol search - by dispatching
   `tds-chart-search` on the page; answers come back as `tds-chart-results`.
   Picking one dispatches `tds-chart-pick`, the server loads that name, and
   this panel is replaced by its chart with the box empty and focused, so the
   next name can be typed straight away. */
(function symbolSearch(){
  if(!SEARCH) return;
  let host;
  try { host = window.parent; void host.document; } catch(err){ return; }
  const wrap = el("symsearch"), input = el("symq"), list = el("symlist");
  wrap.hidden = false;
  let results = [], active = 0, timer = null, answered = "", errorSeq = null, searchErr = null;
  const esc = v => String(v == null ? "" : v).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

  function render(){
    const q = input.value.trim();
    if(!q){ list.hidden = true; return; }
    if(!results.length){
      list.innerHTML = '<div class="so-none">' + (answered !== q ? 'Searching\u2026'
        : searchErr ? esc(searchErr)
        : 'No matches \u2014 press Enter to chart \u201c' + esc(q.toUpperCase()) + '\u201d') + '</div>';
      list.hidden = false;
      return;
    }
    list.innerHTML = results.map(function(r, i){
      return '<div class="so-opt' + (i === active ? ' on' : '') + '" data-i="' + i + '">'
        + '<span class="so-sym">' + esc(r.symbol) + '</span>'
        + '<span class="so-name">' + esc(r.name) + '</span>'
        + '<span class="so-tag' + (r.type === "Futures" ? ' fut' : '') + '">' + esc(r.type) + '</span>'
        + (r.exchange ? '<span class="so-ex">' + esc(r.exchange) + '</span>' : '')
        + '</div>';
    }).join("");
    list.hidden = false;
  }

  function ask(q){ try { host.dispatchEvent(new CustomEvent("tds-chart-search", {detail: q})); } catch(err){} }

  function pick(r){
    list.hidden = true;
    input.value = "";
    showMsg("Loading " + r.symbol + "\u2026");
    try {
      host.dispatchEvent(new CustomEvent("tds-chart-pick", {detail:
        {symbol: r.symbol, source: r.source || "auto", name: r.name || "", type: r.type || ""}}));
    } catch(err){}
  }

  input.addEventListener("input", function(){
    results = []; active = 0;
    clearTimeout(timer);
    const q = input.value.trim();
    // Already answered this session: show it now, without the typing pause.
    let known = null;
    try { known = q && host.__tdsSearchCache && host.__tdsSearchCache[q.toLowerCase()]; } catch(err){}
    if(known){ answered = q; results = known; searchErr = null; render(); return; }
    render();
    if(q) timer = setTimeout(function(){ ask(q); }, 220);
  });
  input.addEventListener("keydown", function(e){
    if(e.key === "ArrowDown" || e.key === "ArrowUp"){
      if(results.length){
        active = (active + (e.key === "ArrowDown" ? 1 : results.length - 1)) % results.length;
        render();
      }
      e.preventDefault();
    } else if(e.key === "Enter"){
      const q = input.value.trim();
      if(!q) return;
      // The highlighted match when it answers what is typed; otherwise the
      // text itself, which the server resolves the same way search would.
      if(results.length && answered === q) pick(results[active]);
      else pick({symbol: q.replace(/^\$/, "").toUpperCase(), source: "auto"});
      e.preventDefault();
    } else if(e.key === "Escape"){
      list.hidden = true;
    }
  });
  list.addEventListener("mousedown", function(e){
    const o = e.target.closest(".so-opt");
    if(o){ e.preventDefault(); pick(results[+o.dataset.i]); }
  });
  input.addEventListener("focus", render);
  input.addEventListener("blur", function(){ setTimeout(function(){ list.hidden = true; }, 150); });

  const alive = () => window.frameElement && window.frameElement.isConnected;
  const onResults = function(e){
    if(!alive()){ host.removeEventListener("tds-chart-results", onResults); return; }
    const d = e.detail || {};
    if((d.q || "") !== input.value.trim()) return;      // an answer to an older query
    answered = d.q || ""; results = d.results || []; searchErr = d.error || null; active = 0;
    render();
  };
  // A pick that found nothing: the chart stays as it was, and says why.
  const onError = function(e){
    if(!alive()){ host.removeEventListener("tds-chart-error", onError); return; }
    const d = e.detail || {};
    if(!d.error || d.errorSeq === errorSeq) return;
    errorSeq = d.errorSeq;
    showMsg(null);
    list.innerHTML = '<div class="so-none">' + esc(d.error) + '</div>';
    list.hidden = false;
    setTimeout(function(){ if(!input.value.trim()) list.hidden = true; }, 3500);
  };
  host.addEventListener("tds-chart-results", onResults);
  host.addEventListener("tds-chart-error", onError);
  window.addEventListener("pagehide", function(){
    host.removeEventListener("tds-chart-results", onResults);
    host.removeEventListener("tds-chart-error", onError);
  });
  // An error already reported before this frame loaded is not news.
  try { if(host.__tdsChartError) errorSeq = host.__tdsChartError.errorSeq; } catch(err){}
  setTimeout(function(){ try { input.focus({preventScroll: true}); } catch(err){} }, 60);
})();

(function subscribe(){
  let host;
  try { host = window.parent; void host.document; } catch(err){ return; }   // no same-origin parent: no feed
  const handler = function(e){
    // A filter change replaces this frame. Drop the listener once it is gone,
    // rather than leave it firing into a discarded document.
    if(!window.frameElement || !window.frameElement.isConnected){
      host.removeEventListener("tds-quotes", handler); return;
    }
    try { applyQuotes(e.detail); } catch(err){}
  };
  host.addEventListener("tds-quotes", handler);
  window.addEventListener("pagehide", function(){ host.removeEventListener("tds-quotes", handler); });
  if(host.__tdsQuotes) applyQuotes(host.__tdsQuotes);
})();

__AUTOSELECT__
</script>
"""


def _fmt(value, col: str) -> tuple[str, str]:
    """Return (display text, css class) for a results cell."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if pd.isna(value):
            return "-", ""
        cls = ""
        if col in ("% from High", "% from 52W Hi", "MACD Hist", "% above Last HL") or col.endswith("%"):
            cls = "pos" if value >= 0 else "neg"
        if col == "RSI":
            cls = "neg" if value >= 70 else ("pos" if value <= 35 else "")
        digits = 1 if col in ("RSI", "% from High", "% above Last HL",
                              "% from 52W Hi", "$ Vol (M)") else 2
        return f"{value:,.{digits}f}", cls
    text = str(value)
    low = text.lower()
    cls = "yes" if low in ("yes", "up", "expanding") else ("no" if low in ("no", "down", "-") else "")
    return text, cls


# Measured row height at the current density, so "20 rows" really shows 20.
ROW_PX = 29

# Change since the previous close: the last session's, then today's once live.
LIVE_COL = "Chg %"
HEADER_PX = 42


def build_panel(
    results: pd.DataFrame,
    long_bars: pd.DataFrame,
    weekly_bars: pd.DataFrame | None = None,
    table_rows: int = 18,
    chart_height: int = 420,
    dark: bool = False,
    panel_id: str = "",
    foldable: bool = True,
    chart_only: bool = False,
    search: bool = False,
    source: str = "uw",
    open_first: bool = True,
) -> tuple[str, int]:
    """Return (html, iframe height) for the combined table + chart panel."""
    if results.empty:
        return '<div style="padding:26px;text-align:center;color:#6b7280;'\
               'font:14px Sofia Sans,sans-serif">No matches.</div>', 90

    table_height = HEADER_PX + table_rows * ROW_PX

    shown = results.head(MAX_EMBED)
    tickers = shown["Ticker"].astype(str).tolist()
    payload = bars_payload(long_bars, tickers)
    weekly = weekly_payload(
        weekly_bars if weekly_bars is not None else pd.DataFrame(), tickers)

    names = {}
    if "Name" in shown.columns:
        names = {str(r["Ticker"]): str(r["Name"]) for _, r in shown.iterrows()}

    cols = [c for c in shown.columns if c != "Name"]
    left_cols = {"Ticker", "Sector", "Exch", "Vol Fade", "MACD", "MACD Rising",
                 "BB Squeeze", "Above 200 SMA"}

    rest = [c for c in cols if c != "Ticker"]
    ordered = ["Ticker"] + (["Name"] if "Name" in shown.columns else []) + rest
    # The change since the previous close sits beside the price it describes.
    # It is filled in the browser: the last session's move from the embedded
    # bars, then today's move as live prices arrive.
    live_col = "Price" in rest
    if live_col:
        ordered.insert(ordered.index("Price") + 1, LIVE_COL)

    # Numeric columns compare as numbers, everything else as text. Deciding it
    # here from the dtype beats guessing in the browser from formatted strings.
    numeric = {c for c in ordered
               if c in shown.columns
               and pd.api.types.is_numeric_dtype(shown[c])
               and not pd.api.types.is_bool_dtype(shown[c])}
    if live_col:
        numeric.add(LIVE_COL)

    head = ""
    for i, c in enumerate(ordered):
        side = "l" if c in left_cols or c == "Name" else ""
        head += (f'<th class="{side} sortable" data-col="{i}" '
                 f'data-num="{1 if c in numeric else 0}">'
                 f'{c}<span class="sortmark"></span></th>')

    def _raw(value, col: str) -> str:
        """The value the browser sorts on, free of formatting."""
        if col in numeric:
            try:
                return "" if pd.isna(value) else repr(float(value))
            except (TypeError, ValueError):
                return ""
        return str(value).lower()

    body = []
    for _, row in shown.iterrows():
        ticker = str(row["Ticker"])
        charted = ticker in payload
        cells = [f'<td class="l" data-v="{ticker.lower()}">'
                 f'<span class="tk">{ticker}</span></td>']
        if "Name" in shown.columns:
            name = str(row["Name"])
            cells.append(f'<td class="l nm" data-v="{name.lower()}">{name}</td>')
        for c in rest:
            text, cls = _fmt(row[c], c)
            side = "l " if c in left_cols else ""
            # The price cell is the one live ticks rewrite.
            live = " lp" if c == "Price" else ""
            cells.append(f'<td class="{side}{cls}{live}" data-v="{_raw(row[c], c)}">{text}</td>')
            if c == "Price" and live_col:
                cells.append('<td class="lc" data-v="">&ndash;</td>')
        title = "" if charted else ' title="No cached bars for this ticker"'
        style = "" if charted else ' style="opacity:.5"'
        body.append(f'<tr data-t="{ticker}"{title}{style}>{"".join(cells)}</tr>')

    # Open on the first row that actually has bars, not simply the first row -
    # a pool built before the bar cache existed can be largely uncharted.
    first = next((t for t in tickers if t in payload), "")
    autoselect = f'selectTicker("{first}");' if first and open_first else ""

    missing = len(tickers) - len(payload)
    hint = (f'<b class="count">{len(results):,}</b> matches · click any name '
            f"to open its chart, click it again to close.")
    if len(results) > MAX_EMBED:
        hint += f" (charts embedded for the top {MAX_EMBED})"
    if missing:
        hint += (f" · {missing} of these have no cached bars yet — "
                 f"press Rebuild pool to fetch them.")

    # `dark` no longer shapes the HTML: the frame reads the page's theme.
    themes = {"light": theme.tokens(False), "dark": theme.tokens(True)}


    html = (
        _TEMPLATE
        .replace("__TOKENS__", theme.css_vars(False))
        .replace("__THEMES__", json.dumps(themes, separators=(",", ":")))
        .replace("__LOGO__", theme.LOGO)
        .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
        .replace("__WEEKLY__", json.dumps(weekly, separators=(",", ":")))
        .replace("__NAMES__", json.dumps(names, separators=(",", ":")))
        .replace("__PANEL__", json.dumps(panel_id or "panel"))
        .replace("__FOLDABLE__", "true" if foldable else "false")
        .replace("__CHARTONLY__", "true" if chart_only else "false")
        .replace("__SEARCH__", "true" if search else "false")
        .replace("__SRC__", json.dumps("yahoo" if source == "yahoo" else "uw"))
        .replace("__PANELCLASS__", " chartonly" if chart_only else "")
        .replace("__THEAD__", head)
        .replace("__TBODY__", "".join(body))
        .replace("__HINT__", hint)
        .replace("__CHARTH__", str(chart_height))
        # With the chart inside the table, the scroll box takes the room the
        # chart used to occupy below it - so the iframe height is unchanged.
        .replace("__FULLH__", str(table_height + chart_height + 190))
        .replace("__AUTOSELECT__", autoselect)
    )
    # table + chart + navigator + toolbar + padding, plus slack so the
    # table can be dragged taller without clipping the chart.
    height = table_height + chart_height + 240
    if chart_only:
        # No table: the chart, its navigator and toolbar, and the top bar.
        height = chart_height + 210
    return html, height
