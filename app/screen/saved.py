"""Saved screen presets — one JSON file, atomic writes.

A saved screen captures the screener state the reader wants to keep:
``{name, state: {filters, sort, view}}``.  Columns are deliberately NOT saved
(view id only) — the view's own column set is the one true list, and saving
a column selection would silently override the view's definition the next time
the catalogue changes.

Stale-key discard lives in the client (the screen state parser already drops
unknown keys — see :func:`parseScreenState`), so the REST layer answers the
raw document and the caller reconciles it against its own filter registry.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SAVED_SCREENS_FILENAME = "saved_screens.json"
_MAX_NAME_LENGTH = 200


def _store_path() -> Path:
    """The JSON file, resolved from ``MEE_SAVED_SCREENS_PATH`` or the screen data dir."""
    env = os.environ.get("MEE_SAVED_SCREENS_PATH", "")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent / "data" / _SAVED_SCREENS_FILENAME


@dataclass(frozen=True, slots=True)
class SavedScreen:
    id: str
    name: str
    state: dict[str, Any]
    created_at: str


class SavedScreenStore:
    """A JSON file of saved screener presets, written atomically.

    The file is a single ``{"screens": [...]}`` object.  Reads parse the file
    in full (the set is small — dozens, not thousands) and writes replace it.
    The ``rename`` temporary + atomic rename pattern keeps the file whole even
    when the writer is interrupted.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _store_path()

    # -- I/O ---------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"screens": []}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"screens": []}

    def _write(self, document: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # -- operations --------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> None:
        stripped = name.strip()
        if not stripped:
            raise ValueError("name must not be empty")
        if len(stripped) > _MAX_NAME_LENGTH:
            raise ValueError(f"name must not exceed {_MAX_NAME_LENGTH} characters")

    def save(self, *, name: str, state: dict[str, Any]) -> SavedScreen:
        self._validate_name(name)
        document = self._read()
        identifier = uuid.uuid4().hex[:12]
        # Using a simple clock for created_at; tests may replace it.
        from datetime import UTC, datetime
        created = datetime.now(UTC).isoformat(timespec="microseconds")
        screen = {
            "id": identifier,
            "name": name,
            "state": state,
            "created_at": created,
        }
        document["screens"].append(screen)
        self._write(document)
        return SavedScreen(
            id=identifier,
            name=name,
            state=state,
            created_at=created,
        )

    def list(self) -> list[dict[str, Any]]:
        document = self._read()
        return list(document.get("screens", []))

    def get(self, identifier: str) -> dict[str, Any] | None:
        for entry in self.list():
            if entry["id"] == identifier:
                return entry
        return None

    def rename(self, identifier: str, name: str) -> dict[str, Any] | None:
        self._validate_name(name)
        document = self._read()
        for entry in document.get("screens", []):
            if entry["id"] == identifier:
                entry["name"] = name
                self._write(document)
                return dict(entry)
        return None

    def delete(self, identifier: str) -> bool:
        document = self._read()
        before = len(document.get("screens", []))
        document["screens"] = [
            entry for entry in document.get("screens", [])
            if entry["id"] != identifier
        ]
        if len(document["screens"]) == before:
            return False
        self._write(document)
        return True


__all__ = ["SavedScreen", "SavedScreenStore"]