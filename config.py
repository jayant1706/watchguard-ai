"""
WatchGuard AI — configuration constants + API key persistence
"""

import json
from pathlib import Path

# ── Colours ──────────────────────────────────────────────────────────
BG_DARK  = "#080b12"      # deep navy-black (main window bg)
BG_MID   = "#0e1220"      # slightly lighter panels / sidebar
FG       = "#dde4f5"      # soft blue-white text
ACCENT   = "#7cffb2"      # mint-green primary accent
MUTED    = "#3a4260"      # blue-grey muted text / labels
GREEN    = "#7cffb2"      # positive / watching
RED      = "#ff5c7a"      # away / error
YELLOW   = "#ffd97a"      # resume / warning
CYAN     = "#4fd9ff"      # secondary accent (bridge, info)
PURPLE   = "#b57bff"      # tertiary accent (decorative)

# ── Detection defaults ────────────────────────────────────────────────
DEFAULT_CHECK_INTERVAL  = 500   # ms between detection ticks
DEFAULT_SENSITIVITY     = 0.6   # face-detection confidence threshold (0.1–1.0)
DEFAULT_AWAY_THRESHOLD  = 5     # seconds before "away" is declared

# ── API key persistence ───────────────────────────────────────────────
_CONFIG_FILE = Path.home() / ".watchguard_config.json"


def load_api_key(backend: str = "groq") -> str:
    """Load saved API key for the given backend ('groq', 'grok', or 'claude'). Returns '' if not set."""
    try:
        data = json.loads(_CONFIG_FILE.read_text())
        if backend == "groq":
            return data.get("groq_api_key", "")
        elif backend == "grok":
            return data.get("grok_api_key", "")
        elif backend == "claude":
            return data.get("claude_api_key", "")
        return ""
    except Exception:
        return ""


def save_api_key(key: str, backend: str = "groq"):
    """Persist the API key for the given backend ('groq', 'grok', or 'claude') to disk."""
    try:
        data = {}
        if _CONFIG_FILE.exists():
            try:
                data = json.loads(_CONFIG_FILE.read_text())
            except Exception:
                pass
        field = {"groq": "groq_api_key", "grok": "grok_api_key", "claude": "claude_api_key"}.get(backend, "groq_api_key")
        data[field] = key.strip()
        _CONFIG_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[Config] Could not save API key: {e}")
