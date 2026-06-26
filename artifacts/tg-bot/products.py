import json
import os
import uuid
import time

PRODUCTS_PATH = os.path.join(os.path.dirname(__file__), "products.json")


def _load() -> dict:
    if not os.path.exists(PRODUCTS_PATH):
        return {}
    with open(PRODUCTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(PRODUCTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_product(user_id: int, product_data: dict) -> dict:
    """Append a new product. Returns the stored record with generated id."""
    data = _load()
    uid = str(user_id)
    if uid not in data:
        data[uid] = []
    product = {"id": str(uuid.uuid4()), "created_at": time.time(), **product_data}
    data[uid].append(product)
    _save(data)
    return product


def get_products(user_id: int) -> list[dict]:
    return list(_load().get(str(user_id), []))


def get_product(user_id: int, product_id: str) -> dict | None:
    for p in get_products(user_id):
        if p["id"] == product_id:
            return p
    return None


def delete_product(user_id: int, product_id: str) -> bool:
    data = _load()
    uid = str(user_id)
    before = len(data.get(uid, []))
    data[uid] = [p for p in data.get(uid, []) if p["id"] != product_id]
    if len(data.get(uid, [])) < before:
        _save(data)
        return True
    return False


def update_product(user_id: int, product_id: str, updates: dict) -> bool:
    data = _load()
    uid = str(user_id)
    for i, p in enumerate(data.get(uid, [])):
        if p["id"] == product_id:
            data[uid][i].update(updates)
            _save(data)
            return True
    return False


def add_attachment(user_id: int, product_id: str, attachment: dict) -> bool:
    data = _load()
    uid = str(user_id)
    for p in data.get(uid, []):
        if p["id"] == product_id:
            if "attachments" not in p:
                p["attachments"] = []
            p["attachments"].append(attachment)
            _save(data)
            return True
    return False


def delete_attachment(user_id: int, product_id: str, idx: int) -> bool:
    data = _load()
    uid = str(user_id)
    for p in data.get(uid, []):
        if p["id"] == product_id:
            attachments = p.get("attachments", [])
            if 0 <= idx < len(attachments):
                attachments.pop(idx)
                p["attachments"] = attachments
                _save(data)
                return True
    return False
