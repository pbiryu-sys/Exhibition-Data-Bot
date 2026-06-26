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


def load_user_context(user_id: int) -> dict:
    """Returns user context. factory/exhibition are None if not explicitly set."""
    all_data = _load_all()
    user = all_data.get(str(user_id), {})
    return {
        "active_factory":    user.get("active_factory"),    # None if not set
        "active_exhibition": user.get("active_exhibition"), # None if not set
    }


def save_user_context(user_id: int, active_factory, active_exhibition) -> None:
    all_data = _load_all()
    all_data[str(user_id)] = {
        "active_factory":    active_factory,
        "active_exhibition": active_exhibition,
    }
    _save_all(all_data)


def clear_user_context(user_id: int) -> None:
    """Remove all stored data for this user. Global settings are untouched."""
    all_data = _load_all()
    all_data.pop(str(user_id), None)
    _save_all(all_data)
