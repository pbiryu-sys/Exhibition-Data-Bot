import json
import os
import uuid
import time

PRODUCTS_PATH = os.path.join(os.path.dirname(__file__), "products.json")


def _load() -> list:
    if not os.path.exists(PRODUCTS_PATH):
        return []
    with open(PRODUCTS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # Auto-migrate: old format was dict keyed by user_id
    if isinstance(raw, dict):
        merged = []
        for uid, products in raw.items():
            for p in products:
                if "created_by" not in p:
                    p["created_by"] = int(uid)
                p.setdefault("updated_by", None)
                p.setdefault("updated_at", None)
                merged.append(p)
        _save(merged)
        return merged
    return raw


def _save(data: list) -> None:
    with open(PRODUCTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_product(product_data: dict, created_by: int) -> dict:
    """Append a new product to the shared global list."""
    products = _load()
    product  = {
        "id":         str(uuid.uuid4()),
        "created_by": created_by,
        "created_at": time.time(),
        "updated_by": None,
        "updated_at": None,
        **product_data,
    }
    products.append(product)
    _save(products)
    return product


def get_products() -> list[dict]:
    """Return all products — shared across all users."""
    return list(_load())


def get_product(product_id: str) -> dict | None:
    for p in _load():
        if p["id"] == product_id:
            return p
    return None


def delete_product(product_id: str, deleted_by: int) -> bool:
    products = _load()
    before   = len(products)
    products = [p for p in products if p["id"] != product_id]
    if len(products) < before:
        _save(products)
        return True
    return False


def update_product(product_id: str, updates: dict, updated_by: int) -> bool:
    products = _load()
    for i, p in enumerate(products):
        if p["id"] == product_id:
            products[i].update(updates)
            products[i]["updated_by"] = updated_by
            products[i]["updated_at"] = time.time()
            _save(products)
            return True
    return False


def add_attachment(product_id: str, attachment: dict) -> bool:
    products = _load()
    for p in products:
        if p["id"] == product_id:
            if "attachments" not in p:
                p["attachments"] = []
            p["attachments"].append(attachment)
            _save(products)
            return True
    return False


def delete_attachment(product_id: str, idx: int) -> bool:
    products = _load()
    for p in products:
        if p["id"] == product_id:
            attachments = p.get("attachments", [])
            if 0 <= idx < len(attachments):
                attachments.pop(idx)
                p["attachments"] = attachments
                _save(products)
                return True
    return False
