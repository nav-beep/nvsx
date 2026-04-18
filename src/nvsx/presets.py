"""Visual presets: colors, icons, stage ordering."""
from __future__ import annotations

# Narrow palette — chosen to survive H.264 compression on social video.
C_GREEN = "#82E0AA"
C_YELLOW = "#F4D03F"
C_RED = "#E74C3C"
C_BLUE = "#5DADE2"
C_GREY = "#7F8C8D"
C_DIM = "#566573"
C_WHITE = "#ECF0F1"
C_CYAN = "#48C9B0"

# Glyphs
ICON_DONE = "●"
ICON_PENDING = "○"
ICON_ACTIVE = "◆"
ICON_FAIL = "✗"
ICON_CHECK = "✓"
ICON_BAR_FULL = "▓"
ICON_BAR_EMPTY = "░"
ICON_ARROW = "→"

# Canonical stage order. Runbooks don't have to use all of these,
# but if they do, the engine renders them in this order.
CANONICAL_STAGES = [
    "preflight",
    "baseline",
    "inject",
    "detect",
    "quarantine",
    "drain",
    "remediate",
    "recover",
    "postmortem",
]

STATUS_STYLES = {
    "pending":  {"color": C_GREY,   "icon": ICON_PENDING, "label": "PENDING"},
    "watching": {"color": C_YELLOW, "icon": ICON_ACTIVE,  "label": "WATCHING"},
    "running":  {"color": C_BLUE,   "icon": ICON_ACTIVE,  "label": "RUNNING"},
    "pass":     {"color": C_GREEN,  "icon": ICON_DONE,    "label": "PASS"},
    "fail":     {"color": C_RED,    "icon": ICON_FAIL,    "label": "FAIL"},
    "skipped":  {"color": C_DIM,    "icon": ICON_PENDING, "label": "SKIPPED"},
    "timeout":  {"color": C_RED,    "icon": ICON_FAIL,    "label": "TIMEOUT"},
}


def style_for(status: str) -> dict:
    return STATUS_STYLES.get(status.lower(), STATUS_STYLES["pending"])
