import json
import os

CONTEXT_PATH = os.path.join(os.path.dirname(__file__), "user_context.json")


def _load_all() -> dict:
    if not os.path.exists(CONTEXT_PATH):
        return {}
    with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_all(data: dict) -> None:
    with open(CONTEXT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── Active context ──────────────────────────────────────────────────────────

def load_user_context(user_id: int) -> dict:
    """Returns active factory/exhibition. Both are None if not explicitly set."""
    all_data = _load_all()
    user = all_data.get(str(user_id), {})
    return {
        "active_factory":    user.get("active_factory"),
        "active_exhibition": user.get("active_exhibition"),
    }


def save_user_context(user_id: int, active_factory, active_exhibition) -> None:
    all_data = _load_all()
    uid = str(user_id)
    if uid not in all_data:
        all_data[uid] = {}
    all_data[uid]["active_factory"]    = active_factory
    all_data[uid]["active_exhibition"] = active_exhibition
    _save_all(all_data)


def clear_user_context(user_id: int) -> None:
    """Remove all stored data for this user. Global settings are untouched."""
    all_data = _load_all()
    all_data.pop(str(user_id), None)
    _save_all(all_data)


# ─── Message ID registry ─────────────────────────────────────────────────────

def add_message_id(user_id: int, message_id: int) -> None:
    """Register a bot message ID so it can be bulk-deleted later."""
    all_data = _load_all()
    uid = str(user_id)
    if uid not in all_data:
        all_data[uid] = {}
    ids = all_data[uid].get("message_ids", [])
    ids.append(message_id)
    all_data[uid]["message_ids"] = ids
    _save_all(all_data)


def get_message_ids(user_id: int) -> list[int]:
    all_data = _load_all()
    return list(all_data.get(str(user_id), {}).get("message_ids", []))


def clear_message_ids(user_id: int) -> None:
    """Remove only the message_ids registry; preserves factory/exhibition data."""
    all_data = _load_all()
    uid = str(user_id)
    if uid in all_data:
        all_data[uid].pop("message_ids", None)
        _save_all(all_data)


# ─── Factory list ────────────────────────────────────────────────────────────

def get_factories(user_id: int) -> list[str]:
    all_data = _load_all()
    return list(all_data.get(str(user_id), {}).get("factories", []))


def add_factory(user_id: int, name: str) -> None:
    all_data = _load_all()
    uid = str(user_id)
    if uid not in all_data:
        all_data[uid] = {}
    factories = all_data[uid].get("factories", [])
    if name not in factories:
        factories.append(name)
    all_data[uid]["factories"] = factories
    _save_all(all_data)


def delete_factory(user_id: int, index: int) -> str | None:
    """Delete factory by 0-based index. Returns deleted name or None."""
    all_data = _load_all()
    uid = str(user_id)
    factories = all_data.get(uid, {}).get("factories", [])
    if 0 <= index < len(factories):
        removed = factories.pop(index)
        all_data[uid]["factories"] = factories
        _save_all(all_data)
        return removed
    return None


# ─── Exhibition list ─────────────────────────────────────────────────────────

def get_exhibitions(user_id: int) -> list[dict]:
    """Returns list of {name, year} dicts."""
    all_data = _load_all()
    return list(all_data.get(str(user_id), {}).get("exhibitions", []))


def add_exhibition(user_id: int, name: str, year: str | None = None) -> None:
    all_data = _load_all()
    uid = str(user_id)
    if uid not in all_data:
        all_data[uid] = {}
    exhibitions = all_data[uid].get("exhibitions", [])
    entry = {"name": name, "year": year or None}
    if entry not in exhibitions:
        exhibitions.append(entry)
    all_data[uid]["exhibitions"] = exhibitions
    _save_all(all_data)


def delete_exhibition(user_id: int, index: int) -> dict | None:
    """Delete exhibition by 0-based index. Returns deleted entry or None."""
    all_data = _load_all()
    uid = str(user_id)
    exhibitions = all_data.get(uid, {}).get("exhibitions", [])
    if 0 <= index < len(exhibitions):
        removed = exhibitions.pop(index)
        all_data[uid]["exhibitions"] = exhibitions
        _save_all(all_data)
        return removed
    return None


def exhibition_display(ex: dict) -> str:
    """Format exhibition for display: 'Name Year' or 'Name'."""
    if ex.get("year"):
        return f"{ex['name']} {ex['year']}"
    return ex["name"]
