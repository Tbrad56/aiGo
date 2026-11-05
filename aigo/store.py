"""Favorites and teleport history, kept in one small JSON file."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

HISTORY_LIMIT = 50
COLLECTIONS = ("favorites", "history", "routes", "schedules")


def default_path() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / "aiGo"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aigo"
    return base / "data.json"


class Store:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_path()
        self.data: dict[str, list[dict[str, Any]]] = {k: [] for k in COLLECTIONS}
        self.settings: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if isinstance(loaded, dict):
            for key in COLLECTIONS:
                if isinstance(loaded.get(key), list):
                    self.data[key] = loaded[key]
            if isinstance(loaded.get("settings"), dict):
                self.settings = loaded["settings"]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.data, "settings": self.settings}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # favorites
    def favorites(self) -> list[dict[str, Any]]:
        return list(self.data["favorites"])

    def add_favorite(self, name: str, lat: float, lon: float) -> dict[str, Any]:
        fav = {"id": uuid.uuid4().hex[:12], "name": name.strip() or f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon}
        self.data["favorites"].append(fav)
        self._save()
        return fav

    def remove_favorite(self, fav_id: str) -> bool:
        before = len(self.data["favorites"])
        self.data["favorites"] = [f for f in self.data["favorites"] if f.get("id") != fav_id]
        changed = len(self.data["favorites"]) != before
        if changed:
            self._save()
        return changed

    # history
    def history(self) -> list[dict[str, Any]]:
        return list(self.data["history"])

    def add_history(self, lat: float, lon: float) -> None:
        hist = self.data["history"]
        if hist and abs(hist[0]["lat"] - lat) < 1e-7 and abs(hist[0]["lon"] - lon) < 1e-7:
            return
        hist.insert(0, {"lat": lat, "lon": lon, "at": int(time.time())})
        del hist[HISTORY_LIMIT:]
        self._save()

    # saved routes and schedules: lists of dicts with an "id"
    def items(self, kind: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self.data[kind]]

    def get(self, kind: str, item_id: str) -> Optional[dict[str, Any]]:
        return next((dict(x) for x in self.data[kind] if x.get("id") == item_id), None)

    def add(self, kind: str, item: dict[str, Any]) -> dict[str, Any]:
        item = {**item, "id": uuid.uuid4().hex[:12]}
        self.data[kind].append(item)
        self._save()
        return dict(item)

    def update(self, kind: str, item_id: str, item: dict[str, Any]) -> Optional[dict[str, Any]]:
        for i, x in enumerate(self.data[kind]):
            if x.get("id") == item_id:
                self.data[kind][i] = {**item, "id": item_id}
                self._save()
                return dict(self.data[kind][i])
        return None

    def remove(self, kind: str, item_id: str) -> bool:
        before = len(self.data[kind])
        self.data[kind] = [x for x in self.data[kind] if x.get("id") != item_id]
        changed = len(self.data[kind]) != before
        if changed:
            self._save()
        return changed

    # settings (home, start trips from home)
    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        if value is None:
            self.settings.pop(key, None)
        else:
            self.settings[key] = value
        self._save()
