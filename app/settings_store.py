"""
Local persistence for workspace settings (OpenAI API key).
Stored beside the workspace database under data/app_settings.json.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

# Deferred import to avoid circular import at module load
def _settings_path() -> str:
    from app import research_db

    d = os.path.dirname(research_db.DB_PATH)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "app_settings.json")


def _read_raw() -> dict[str, Any]:
    path = _settings_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_raw(data: dict[str, Any]) -> None:
    path = _settings_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def get_saved_openai_key() -> str:
    key = (_read_raw().get("openai_api_key") or "").strip()
    return key


def set_openai_api_key(key: str) -> None:
    key = (key or "").strip()
    data = _read_raw()
    if key:
        data["openai_api_key"] = key
    else:
        data.pop("openai_api_key", None)
    _write_raw(data)


def clear_openai_api_key() -> None:
    data = _read_raw()
    data.pop("openai_api_key", None)
    _write_raw(data)


def effective_openai_key() -> Optional[str]:
    """Saved app key wins; otherwise OPENAI_API_KEY from environment."""
    saved = get_saved_openai_key()
    if saved:
        return saved
    env = (os.environ.get("OPENAI_API_KEY") or "").strip()
    return env or None


def openai_status() -> dict[str, Any]:
    """
    Non-secret status for UI.
    source: saved | env | none
    """
    if get_saved_openai_key():
        return {"configured": True, "source": "saved", "label": "Saved in this app"}
    env = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if env:
        return {"configured": True, "source": "env", "label": "Environment variable"}
    return {"configured": False, "source": "none", "label": "Not configured"}


def public_nav_hint() -> str:
    st = openai_status()
    if st["configured"]:
        return "AI on"
    return "AI off"
