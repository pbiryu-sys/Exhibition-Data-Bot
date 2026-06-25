import json
import os

CONTEXT_PATH = os.path.join(os.path.dirname(__file__), "user_context.json")

DEFAULT_FACTORY = "DHZ"
DEFAULT_EXHIBITION = "CF26"


def _load_all() -> dict:
    if not os.path.exists(CONTEXT_PATH):
        return {}
    with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_all(data: dict) -> None:
    with open(CONTEXT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_user_context(user_id: int) -> dict:
    all_data = _load_all()
    user = all_data.get(str(user_id), {})
    return {
        "active_factory": user.get("active_factory", DEFAULT_FACTORY),
        "active_exhibition": user.get("active_exhibition", DEFAULT_EXHIBITION),
    }


def save_user_context(user_id: int, active_factory: str, active_exhibition: str) -> None:
    all_data = _load_all()
    all_data[str(user_id)] = {
        "active_factory": active_factory,
        "active_exhibition": active_exhibition,
    }
    _save_all(all_data)
