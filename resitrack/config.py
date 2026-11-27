"""App paths and persisted settings (stored in %APPDATA%\\ResiTrack)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "ResiTrack"


def assets_dir() -> Path:
    """Return path to bundled assets directory (supports PyInstaller onefile via _MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1] / "assets"


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or str(Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    try:
        p = base / APP_NAME
        p.mkdir(parents=True, exist_ok=True)
        test_file = p / ".test_write"
        test_file.touch(exist_ok=True)
        test_file.unlink(missing_ok=True)
        return p
    except Exception:
        if getattr(sys, "frozen", False):
            local_p = Path(sys.executable).resolve().parent / "data"
        else:
            local_p = Path(__file__).resolve().parents[1] / "data"
        local_p.mkdir(parents=True, exist_ok=True)
        return local_p


DB_PATH = data_dir() / "resitrack.db"
SETTINGS_PATH = data_dir() / "settings.json"

DEFAULTS: dict = {
    # Aggregator (needed for JNE & POS because their public sites are behind Cloudflare Turnstile)
    "binderbyte_api_key": "",
    "use_binderbyte_fallback": True,      # also try BinderByte when SPX/Anteraja direct fails
    # Shopee Open Platform
    "shopee_partner_id": "",
    "shopee_partner_key": "",
    "shopee_shop_id": "",
    "shopee_access_token": "",
    "shopee_refresh_token": "",
    "shopee_token_expire_at": 0,
    "shopee_redirect_url": "https://www.google.com",
    "shopee_use_tracking_api": True,       # use logistics.get_tracking_info for Shopee-synced orders
    "shopee_sandbox": False,
    # Behaviour
    "theme": "light",                     # "light" | "dark"
    "inspector_open": True,
    "auto_refresh_minutes": 60,
    "request_delay_seconds": 1.0,
}


def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def save_settings(s: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")
