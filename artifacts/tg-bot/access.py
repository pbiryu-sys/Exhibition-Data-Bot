"""
Access control for the private Telegram bot.

Storage: allowed_users.json  — list of {id, role} objects.
Roles: "owner" | "manager"

Lifecycle:
  1. File starts empty ([]) — setup mode; everyone passes.
  2. First person sends /setowner → becomes owner, strict mode activates.
  3. Owner uses /adduser / /removeuser or the inline admin panel.

Auto-migration: if legacy access.json is found it is converted automatically.
"""
import json
import os

USERS_PATH  = os.path.join(os.path.dirname(__file__), "allowed_users.json")
LEGACY_PATH = os.path.join(os.path.dirname(__file__), "access.json")


# ── Internal I/O ─────────────────────────────────────────────────────────────

def _load() -> list:
    """Returns list of {id: int, role: str} dicts."""
    if os.path.exists(USERS_PATH):
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    # Auto-migrate from legacy access.json
    if os.path.exists(LEGACY_PATH):
        with open(LEGACY_PATH, "r", encoding="utf-8") as f:
            legacy = json.load(f)
        owner_id = legacy.get("owner_id")
        users = []
        for uid in legacy.get("allowed_users", []):
            users.append({"id": uid, "role": "owner" if uid == owner_id else "manager"})
        _save(users)
        return users
    return []


def _save(users: list) -> None:
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


# ── Public API ────────────────────────────────────────────────────────────────

def is_setup_mode() -> bool:
    """True while no users are configured — open bootstrap window."""
    return len(_load()) == 0


def is_allowed(user_id: int) -> bool:
    """True if user may use the bot. In setup mode everyone is allowed."""
    users = _load()
    if not users:
        return True
    return any(u["id"] == user_id for u in users)


def get_owner_id() -> int | None:
    for u in _load():
        if u.get("role") == "owner":
            return u["id"]
    return None


def is_owner(user_id: int) -> bool:
    return user_id == get_owner_id()


def get_user_role(user_id: int) -> str | None:
    for u in _load():
        if u["id"] == user_id:
            return u.get("role", "manager")
    return None


def set_owner(user_id: int) -> None:
    """Register the first owner; also adds them to allowed list."""
    users = _load()
    # Only one owner allowed — strip any existing owner role
    for u in users:
        if u.get("role") == "owner":
            u["role"] = "manager"
    existing = next((u for u in users if u["id"] == user_id), None)
    if existing:
        existing["role"] = "owner"
    else:
        users.insert(0, {"id": user_id, "role": "owner"})
    _save(users)


def add_user(user_id: int, role: str = "manager") -> bool:
    """Add user with given role. Returns False if already present."""
    users = _load()
    if any(u["id"] == user_id for u in users):
        return False
    users.append({"id": user_id, "role": role})
    _save(users)
    return True


def remove_user(user_id: int) -> str:
    """
    Remove user from allowlist.
    Returns 'ok' | 'not_found' | 'is_owner'.
    """
    users = _load()
    target = next((u for u in users if u["id"] == user_id), None)
    if target is None:
        return "not_found"
    if target.get("role") == "owner":
        return "is_owner"
    _save([u for u in users if u["id"] != user_id])
    return "ok"


def list_users() -> list:
    """Returns list of plain user IDs."""
    return [u["id"] for u in _load()]


def list_users_with_roles() -> list:
    """Returns full list of {id, role} dicts."""
    return _load()
