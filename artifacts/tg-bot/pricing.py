import json
import os

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")


def load_settings() -> dict:
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_settings(settings: dict) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def calculate_cost_per_unit(fob_price_usd: float, qty_40hq: int, settings: dict) -> dict:
    s = settings
    exchange_rate = s["exchange_rate"]
    container_cost = s["container_cost"]
    customs_fee_rate = s["customs_fee"]
    broker_fee = s["broker_fee"]
    terminal_fee = s["terminal_fee"]
    storage_fee = s["storage_fee"]
    forwarder_fee = s["forwarder_fee"]
    margin = s["margin"]
    vat = s["vat"]

    fob_rub = fob_price_usd * exchange_rate

    total_container_expenses = (
        container_cost + broker_fee + terminal_fee + storage_fee + forwarder_fee
    )

    customs_total = fob_rub * qty_40hq * customs_fee_rate

    per_unit_logistics = (total_container_expenses + customs_total) / qty_40hq if qty_40hq > 0 else 0

    cost = fob_rub + per_unit_logistics

    sell_price = cost / (1 - margin) * vat

    return {
        "fob_rub": round(fob_rub, 2),
        "per_unit_logistics": round(per_unit_logistics, 2),
        "cost": round(cost, 2),
        "sell_price": round(sell_price, 2),
        "customs_per_unit": round(customs_total / qty_40hq if qty_40hq > 0 else 0, 2),
        "container_per_unit": round(
            (container_cost + broker_fee + terminal_fee + storage_fee + forwarder_fee) / qty_40hq
            if qty_40hq > 0 else 0,
            2,
        ),
    }
