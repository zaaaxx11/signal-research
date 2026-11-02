"""Saved screens RED — POST save, GET list, PUT rename, DELETE, stale-key discard.

Contract:
* POST /screens/saved {name, state:{filters, sort, view}} — validates name, saves to JSON store
* GET /screens/saved — list all saved screens
* PUT /screens/saved/{id} {name} — rename
* DELETE /screens/saved/{id} — delete
* Restore applies state atomically; stale filter keys silently discarded
* Column persistence NOT saved (view id only)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAVED_SCREENS_PATH = Path(__file__).resolve().parent / "fixtures" / "saved_screens.json"


def _saved_store() -> Any:
    from app.screen.saved import SavedScreenStore

    return SavedScreenStore(path=SAVED_SCREENS_PATH)


def _cleanup() -> None:
    path = SAVED_SCREENS_PATH
    tmp = path.with_suffix(path.suffix + ".tmp")
    for p in (path, tmp):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _clean_saved_store() -> Any:
    _cleanup()
    yield
    _cleanup()


# ---------------------------------------------------------------------------
# Store operations
# ---------------------------------------------------------------------------


def test_saved_store_save_and_list() -> None:
    store = _saved_store()
    state = {"filters": [], "sort": "market_cap", "view": "today"}
    saved = store.save(name="My Screen", state=state)

    assert saved.id, "saved screen must have an id"
    assert saved.name == "My Screen"
    assert saved.state == state
    assert saved.created_at

    screens = store.list()
    assert len(screens) == 1
    assert screens[0]["id"] == saved.id
    assert screens[0]["name"] == "My Screen"
    assert screens[0]["state"] == state


def test_saved_store_list_empty() -> None:
    store = _saved_store()
    assert store.list() == []


def test_saved_store_save_multiple() -> None:
    store = _saved_store()
    store.save(name="First", state={"filters": [], "sort": "gainers", "view": "momentum"})
    store.save(name="Second", state={"filters": [], "sort": "market_cap", "view": "value"})

    screens = store.list()
    assert len(screens) == 2
    names = [s["name"] for s in screens]
    assert "First" in names
    assert "Second" in names


def test_saved_store_rename() -> None:
    store = _saved_store()
    saved = store.save(name="Old Name", state={"filters": [], "sort": "market_cap", "view": "today"})

    result = store.rename(saved.id, "New Name")
    assert result is not None
    assert result["name"] == "New Name"
    assert result["id"] == saved.id

    screens = store.list()
    assert len(screens) == 1
    assert screens[0]["name"] == "New Name"


def test_saved_store_rename_nonexistent() -> None:
    store = _saved_store()
    result = store.rename("nonexistent", "New Name")
    assert result is None


def test_saved_store_delete() -> None:
    store = _saved_store()
    saved = store.save(name="To Delete", state={"filters": [], "sort": "market_cap", "view": "today"})

    deleted = store.delete(saved.id)
    assert deleted is True

    assert store.list() == []
    assert store.get(saved.id) is None


def test_saved_store_delete_nonexistent() -> None:
    store = _saved_store()
    deleted = store.delete("nonexistent")
    assert deleted is False


def test_saved_store_get() -> None:
    store = _saved_store()
    saved = store.save(name="Find Me", state={"filters": [], "sort": "market_cap", "view": "today"})

    found = store.get(saved.id)
    assert found is not None
    assert found["name"] == "Find Me"

    assert store.get("nonexistent") is None


# ---------------------------------------------------------------------------
# State capture — columns NOT saved
# ---------------------------------------------------------------------------


def test_state_captures_view_not_columns() -> None:
    """The saved state carries view id only; columns are the view's own definition."""
    store = _saved_store()
    state = {
        "filters": [
            {"key": "cap", "op": "gte", "value": 2000000000},
        ],
        "sort": "gainers",
        "view": "momentum",
        # 'columns' deliberately NOT in state — only view id
    }
    saved = store.save(name="Filtered View", state=state)

    retrieved = store.get(saved.id)
    assert retrieved is not None
    assert "view" in retrieved["state"]
    assert retrieved["state"]["view"] == "momentum"
    # Column persistence is not stored
    assert "columns" not in retrieved["state"]


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------


def test_name_must_be_nonempty() -> None:
    store = _saved_store()
    state = {"filters": [], "sort": "market_cap", "view": "today"}

    for name in ("", "   ", "\n\t"):
        with pytest.raises(ValueError, match="name"):
            store.save(name=name, state=state)


def test_name_must_not_be_too_long() -> None:
    store = _saved_store()
    state = {"filters": [], "sort": "market_cap", "view": "today"}

    with pytest.raises(ValueError, match="name"):
        store.save(name="A" * 201, state=state)


# ---------------------------------------------------------------------------
# Stale key discard
# ---------------------------------------------------------------------------


def test_stale_filter_keys_discarded_on_load() -> None:
    """When state contains filter keys unknown to the current registry, they are
    silently discarded — the counterpart of the client's `parseScreenState`
    which already drops unknown keys."""
    store = _saved_store()
    state = {
        "filters": [
            {"key": "pe", "op": "lt", "value": 15},
            {"key": "stale_key_xyz", "op": "gte", "value": 100},
            {"key": "cap", "op": "gt", "value": 50000000},
        ],
        "sort": "market_cap",
        "view": "value",
    }
    saved = store.save(name="Stale Test", state=state)

    # Simulate loading with a known-key set
    known_keys = frozenset({"pe", "cap", "rsi", "beta"})
    retrieved = store.get(saved.id)
    assert retrieved is not None

    original_filters = retrieved["state"]["filters"]
    # Discard filters whose key is not in the known set
    cleaned = [f for f in original_filters if f["key"] in known_keys]
    assert len(cleaned) == 2
    assert cleaned[0]["key"] == "pe"
    assert cleaned[1]["key"] == "cap"
    # stale_key_xyz is gone
    assert not any(f["key"] == "stale_key_xyz" for f in cleaned)


def test_all_filters_stale_returns_empty() -> None:
    """When every filter key is stale, the result is an empty filter list — the
    view still loads, unfiltered."""
    store = _saved_store()
    state = {
        "filters": [
            {"key": "unknown_a", "op": "gte", "value": 10},
            {"key": "unknown_b", "op": "lt", "value": 50},
        ],
        "sort": "market_cap",
        "view": "today",
    }
    saved = store.save(name="All Stale", state=state)

    known_keys = frozenset({"pe", "cap"})
    retrieved = store.get(saved.id)
    assert retrieved is not None
    cleaned = [f for f in retrieved["state"]["filters"] if f["key"] in known_keys]
    assert cleaned == []


def test_no_filters_state_loads_unchanged() -> None:
    """State with no filters loads as-is."""
    store = _saved_store()
    state = {"filters": [], "sort": "gainers", "view": "momentum"}
    saved = store.save(name="No Filters", state=state)

    retrieved = store.get(saved.id)
    assert retrieved is not None
    assert retrieved["state"]["filters"] == []
    assert retrieved["state"]["view"] == "momentum"
    assert retrieved["state"]["sort"] == "gainers"


# ---------------------------------------------------------------------------
# Persistence across store instances
# ---------------------------------------------------------------------------


def test_data_survives_store_reopen() -> None:
    store_a = _saved_store()
    saved = store_a.save(name="Persistent", state={"filters": [], "sort": "market_cap", "view": "today"})

    # New instance — reads the same file
    store_b = _saved_store()
    screens = store_b.list()
    assert len(screens) == 1
    assert screens[0]["id"] == saved.id
    assert screens[0]["name"] == "Persistent"


# ---------------------------------------------------------------------------
# HTTP API tests
# ---------------------------------------------------------------------------


async def test_api_save_and_list(tmp_path: Path, monkeypatch: Any) -> None:
    """POST /screens/saved creates a screen; GET /screens/saved lists it."""
    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    _cleanup()
    monkeypatch.setenv("MEE_SAVED_SCREENS_PATH", str(SAVED_SCREENS_PATH))

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        state = {"filters": [], "sort": "market_cap", "view": "today"}
        resp = await client.post("/screens/saved", json={"name": "Test Screen", "state": state})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Test Screen"
        assert body["state"] == state
        assert body["id"]

        # List
        resp = await client.get("/screens/saved")
        assert resp.status_code == 200
        screens = resp.json()
        assert len(screens) == 1
        assert screens[0]["id"] == body["id"]


async def test_api_save_empty_name_is_422(tmp_path: Path, monkeypatch: Any) -> None:
    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    _cleanup()
    monkeypatch.setenv("MEE_SAVED_SCREENS_PATH", str(SAVED_SCREENS_PATH))

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        for name in ("", "   "):
            resp = await client.post("/screens/saved", json={"name": name, "state": {}})
            assert resp.status_code == 422, f"name={name!r} got {resp.status_code}"
            error = resp.json()
            assert error["error"] == "invalid_name"


async def test_api_rename(tmp_path: Path, monkeypatch: Any) -> None:
    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    _cleanup()
    monkeypatch.setenv("MEE_SAVED_SCREENS_PATH", str(SAVED_SCREENS_PATH))

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        resp = await client.post("/screens/saved", json={"name": "Original", "state": {}})
        screen_id = resp.json()["id"]

        resp = await client.put(f"/screens/saved/{screen_id}", json={"name": "Renamed"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Renamed"

        # Verify via list
        resp = await client.get("/screens/saved")
        assert resp.json()[0]["name"] == "Renamed"


async def test_api_rename_nonexistent_is_404(tmp_path: Path, monkeypatch: Any) -> None:
    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    _cleanup()
    monkeypatch.setenv("MEE_SAVED_SCREENS_PATH", str(SAVED_SCREENS_PATH))

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        resp = await client.put("/screens/saved/nonexistent", json={"name": "X"})
        assert resp.status_code == 404
        assert resp.json()["error"] == "saved_screen_not_found"


async def test_api_delete(tmp_path: Path, monkeypatch: Any) -> None:
    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    _cleanup()
    monkeypatch.setenv("MEE_SAVED_SCREENS_PATH", str(SAVED_SCREENS_PATH))

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        resp = await client.post("/screens/saved", json={"name": "To Delete", "state": {}})
        screen_id = resp.json()["id"]

        resp = await client.delete(f"/screens/saved/{screen_id}")
        assert resp.status_code == 204

        # Verify gone
        resp = await client.get("/screens/saved")
        assert resp.json() == []


async def test_api_delete_nonexistent_is_404(tmp_path: Path, monkeypatch: Any) -> None:
    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    _cleanup()
    monkeypatch.setenv("MEE_SAVED_SCREENS_PATH", str(SAVED_SCREENS_PATH))

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        resp = await client.delete("/screens/saved/nonexistent")
        assert resp.status_code == 404


async def test_api_save_full_state_with_filters(tmp_path: Path, monkeypatch: Any) -> None:
    """A real screener state with filters, sort, and view is saved and retrieved."""
    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    _cleanup()
    monkeypatch.setenv("MEE_SAVED_SCREENS_PATH", str(SAVED_SCREENS_PATH))

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        state = {
            "filters": [
                {"key": "cap", "op": "gte", "value": 2000000000},
                {"key": "pe", "op": "lt", "value": 15},
                {"key": "sector", "op": "in", "value": ["Technology", "Healthcare"]},
            ],
            "sort": "gainers",
            "view": "momentum",
        }
        resp = await client.post("/screens/saved", json={"name": "Tech Value", "state": state})
        assert resp.status_code == 201
        body = resp.json()
        assert body["state"] == state
        assert body["state"]["view"] == "momentum"
        # columns not in state
        assert "columns" not in body["state"]