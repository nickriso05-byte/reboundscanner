"""
Palette tokens for both themes.

Streamlit's own theme is fixed at start-up by config.toml, so a runtime toggle
cannot go through it. Instead every colour in the app - the shell, the sidebar
and the chart iframe - is read from one token set defined here, and the toggle
simply chooses which set gets emitted. That keeps the two themes honest: there
is no second stylesheet to drift out of step, only different values.
"""

from __future__ import annotations

# No logo: the app is unbranded for now, so the chart draws no watermark.
LOGO = ""

# A neutral slate-and-blue palette. Blue carries links, active states and the
# few marks that should catch the eye.
LIGHT = {
    "scheme": "light",        # read by the chart frames to follow the page
    "bg": "#f2f4f6",          # page behind the panels
    "panel": "#ffffff",       # cards, table, chart
    "panel_alt": "#f7f8fa",   # table header, toolbars
    "ink": "#00131f",         # primary text
    "ink_2": "#2c3d50",       # secondary text
    "muted": "#6a7682",       # labels, captions
    "line": "#dde2e7",        # borders
    "line_soft": "#eef1f4",   # row separators
    "accent": "#1d4ed8",      # links, active states
    "accent_soft": "#e8eefc", # selected row
    "accent_ink": "#ffffff",  # text on accent
    "brand": "#1d4ed8",       # emphasis
    "mast": "#1f2937",        # masthead bar
    "mast_ink": "#ffffff",
    "mast_muted": "#9fb3c8",
    "mast_brand": "#93c5fd",  # orange lifted to read on the navy bar
    "up": "#0f8f5f",
    "down": "#c0392b",
    "grid": "#eef0f3",        # chart gridlines
    "axis": "#2c3d50",        # chart axis text
    "shadow": "rgba(0,19,31,.07)",
    "wash": "rgba(29,78,216,.09)",       # measure band, navigator window
    "wash_edge": "rgba(29,78,216,.55)",
    "shade": "rgba(255,255,255,.66)",  # dims the navigator outside the window
    "watermark": "0",               # opacity of the logo behind the chart
    "toggle_off": "rgba(0,19,31,.2)",  # a switch's track when off
}

# Dark ground with the panels a step above it; the accent lifts to a lighter
# blue so it still reads.
DARK = {
    "scheme": "dark",
    "bg": "#00131f",
    "panel": "#03202f",
    "panel_alt": "#062a3d",
    "ink": "#eef2f6",
    "ink_2": "#bac7d4",
    "muted": "#7f93a6",
    "line": "#14384e",
    "line_soft": "#0b2b3d",
    "accent": "#8db3da",
    "accent_soft": "#0d3550",
    "accent_ink": "#00131f",
    "brand": "#60a5fa",
    "mast": "#000c14",
    "mast_ink": "#ffffff",
    "mast_muted": "#8197ad",
    "mast_brand": "#93c5fd",
    "up": "#3fb950",
    "down": "#f85149",
    "grid": "#0b2a3b",
    "axis": "#9aabbc",
    "shadow": "rgba(0,0,0,.4)",
    "wash": "rgba(141,179,218,.15)",
    "wash_edge": "rgba(141,179,218,.6)",
    "shade": "rgba(0,19,31,.66)",
    "watermark": "0",
    "toggle_off": "rgba(255,255,255,.22)",
}


# Sofia Sans for everything read at a glance.
FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Libre+Caslon+Text:wght@400;700'
    '&family=Sofia+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">'
)


def tokens(dark: bool) -> dict[str, str]:
    return DARK if dark else LIGHT


def css_vars(dark: bool) -> str:
    """The token set as a :root block."""
    return ":root{" + "".join(
        f"--{k.replace('_', '-')}:{v};" for k, v in tokens(dark).items()
    ) + "}"
