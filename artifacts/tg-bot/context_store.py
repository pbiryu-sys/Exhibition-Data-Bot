import json
import os

CONTEXT_PATH = os.path.join(os.path.dirname(__file__), "user_context.json")
SHARED_PATH  = os.path.join(os.path.dirname(__file__), "shared_data.json")


# ─── User context (per-user active factory/exhibition) ────────────────────────

def _load_ctx() -> dict:
    if not os.path.exists(CONTEXT_PATH):
        return {}
    with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_ctx(data: dict) -> None:
    with open(CONTEXT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_user_context(user_id: int) -> dict:
    """Returns active factory/exhibition for this user. Both are None if not set."""
    data = _load_ctx()
    user = data.get(str(user_id), {})
    return {
        "active_factory":    user.get("active_factory"),
        "active_exhibition": user.get("active_exhibition"),
    }


def save_user_context(user_id: int, active_factory, active_exhibition) -> None:
    data = _load_ctx()
    uid  = str(user_id)
    if uid not in data:
        data[uid] = {}
    data[uid]["active_factory"]    = active_factory
    data[uid]["active_exhibition"] = active_exhibition
    _save_ctx(data)


def clear_user_context(user_id: int) -> None:
    data = _load_ctx()
    data.pop(str(user_id), None)
    _save_ctx(data)


# ─── Shared data (global factory + exhibition lists) ─────────────────────────

def _load_shared() -> dict:
    if not os.path.exists(SHARED_PATH):
        return {"factories": [], "exhibitions": []}
    with open(SHARED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_shared(data: dict) -> None:
    with open(SHARED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_to_shared() -> None:
    """
    One-time migration: extract factories/exhibitions from per-user user_context.json
    into the global shared_data.json. Runs automatically on first import when
    shared_data.json does not yet exist.
    """
    if os.path.exists(SHARED_PATH):
        return
    ctx              = _load_ctx()
    seen_factories   = []
    seen_exhibitions = []
    for uid, user_data in ctx.items():
        for f in user_data.get("factories", []):
            if f not in seen_factories:
                seen_factories.append(f)
        for ex in user_data.get("exhibitions", []):
            if not any(
                e["name"] == ex["name"] and e.get("year") == ex.get("year")
                for e in seen_exhibitions
            ):
                seen_exhibitions.append({"name": ex["name"], "year": ex.get("year")})
    _save_shared({"factories": seen_factories, "exhibitions": seen_exhibitions})


_migrate_to_shared()


# ─── Factory list (global) ────────────────────────────────────────────────────

def get_factories() -> list[str]:
    return list(_load_shared().get("factories", []))


def add_factory(name: str, added_by: int | None = None) -> None:
    shared    = _load_shared()
    factories = shared.get("factories", [])
    if name not in factories:
        factories.append(name)
    shared["factories"] = factories
    _save_shared(shared)


def delete_factory(index: int, deleted_by: int | None = None) -> str | None:
    """Delete factory by 0-based index. Returns deleted name or None."""
    shared    = _load_shared()
    factories = shared.get("factories", [])
    if 0 <= index < len(factories):
        removed             = factories.pop(index)
        shared["factories"] = factories
        _save_shared(shared)
        return removed
    return None


# ─── Exhibition list (global) ─────────────────────────────────────────────────

def get_exhibitions() -> list[dict]:
    """Returns list of {name, year} dicts."""
    return list(_load_shared().get("exhibitions", []))


def add_exhibition(name: str, year: str | None = None, added_by: int | None = None) -> None:
    shared      = _load_shared()
    exhibitions = shared.get("exhibitions", [])
    entry       = {"name": name, "year": year or None}
    if not any(e["name"] == name and e.get("year") == entry["year"] for e in exhibitions):
        exhibitions.append(entry)
    shared["exhibitions"] = exhibitions
    _save_shared(shared)


def delete_exhibition(index: int, deleted_by: int | None = None) -> dict | None:
    """Delete exhibition by 0-based index. Returns deleted entry or None."""
    shared      = _load_shared()
    exhibitions = shared.get("exhibitions", [])
    if 0 <= index < len(exhibitions):
        removed               = exhibitions.pop(index)
        shared["exhibitions"] = exhibitions
        _save_shared(shared)
        return removed
    return None


def exhibition_display(ex: dict) -> str:
    """Format exhibition for display: 'Name Year' or 'Name'."""
    if ex.get("year"):
        return f"{ex['name']} {ex['year']}"
    return ex["name"]
