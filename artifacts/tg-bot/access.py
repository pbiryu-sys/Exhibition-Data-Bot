"""
Access control for the private Telegram bot.

Lifecycle:
  1. access.json starts with owner_id=null, allowed_users=[].
  2. In this state is_allowed() returns True for everyone (setup mode).
  3. First authorized person sends /setowner → they become owner and
     are added to allowed_users.  From this point strict mode is active.
  4. Owner uses /adduser and /removeuser to manage the allowlist.
"""
import json
import os

ACCESS_PATH = os.path.join(os.path.dirname(__file__), "access.json")


def _load() -> dict:
    if not os.path.exists(ACCESS_PATH):
        data: dict = {"owner_id": None, "allowed_users": []}
        _save(data)
        return data
    with open(ACCESS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(ACCESS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_setup_mode() -> bool:
    """True while no users have been configured yet."""
    return not _load().get("allowed_users")


def is_allowed(user_id: int) -> bool:
    """
    Returns True if the user may use the bot.
    In setup mode (empty list) everyone is allowed so the owner can bootstrap.
    """
    data = _load()
    allowed = data.get("allowed_users", [])
    if not allowed:
        return True
    return user_id in allowed


def get_owner_id() -> int | None:
    return _load().get("owner_id")


def is_owner(user_id: int) -> bool:
    oid = get_owner_id()
    return oid is not None and user_id == oid


def set_owner(user_id: int) -> None:
    data = _load()
    data["owner_id"] = user_id
    allowed = data.setdefault("allowed_users", [])
    if user_id not in allowed:
        allowed.append(user_id)
    _save(data)


def add_user(user_id: int) -> bool:
    """Add user to allowlist. Returns False if already present."""
    data = _load()
    allowed = data.setdefault("allowed_users", [])
    if user_id in allowed:
        return False
    allowed.append(user_id)
    _save(data)
    return True


def remove_user(user_id: int) -> str:
    """
    Remove user from allowlist.
    Returns 'ok', 'not_found', or 'is_owner'.
    """
    data = _load()
    if user_id == data.get("owner_id"):
        return "is_owner"
    allowed = data.get("allowed_users", [])
    if user_id not in allowed:
        return "not_found"
    data["allowed_users"] = [u for u in allowed if u != user_id]
    _save(data)
    return "ok"


def list_users() -> list:
    return _load().get("allowed_users", [])
