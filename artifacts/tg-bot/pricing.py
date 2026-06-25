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
    exchange_rate    = s["exchange_rate"]   # manual internal procurement rate — applied ONLY to FOB
    container_cost   = s["container_cost"]  # KZT
    customs_fee_rate = s["customs_fee"]     # fraction
    broker_fee       = s["broker_fee"]      # KZT
    terminal_fee     = s["terminal_fee"]    # KZT
    storage_fee      = s["storage_fee"]     # KZT
    forwarder_fee    = s["forwarder_fee"]   # KZT
    margin           = s["margin"]
    vat              = s["vat"]

    # ── Stage 1: container-level aggregation ─────────────────────────────────

    # FOB for entire container in KZT (exchange_rate applied only here)
    fob_total_kzt = fob_price_usd * exchange_rate * qty_40hq

    # Customs on total container FOB value
    customs_total_kzt = fob_total_kzt * customs_fee_rate

    # All flat KZT fees — used as-is, no conversion
    all_fees_kzt = (
        container_cost
        + broker_fee
        + terminal_fee
        + storage_fee
        + forwarder_fee
    )

    # TOTAL_CONTAINER_COST — full aggregation before any per-unit division
    total_container_cost = fob_total_kzt + customs_total_kzt + all_fees_kzt

    # ── Stage 2: per-unit division ────────────────────────────────────────────
    unit_cost  = total_container_cost / qty_40hq if qty_40hq > 0 else 0
    sell_price = unit_cost / (1 - margin) * vat

    # ── Display breakdown (computed after total, not before) ──────────────────
    fob_unit_kzt       = fob_total_kzt / qty_40hq if qty_40hq > 0 else 0
    customs_per_unit   = customs_total_kzt / qty_40hq if qty_40hq > 0 else 0
    logistics_per_unit = all_fees_kzt / qty_40hq if qty_40hq > 0 else 0

    return {
        "fob_kzt":            round(fob_unit_kzt, 2),
        "logistics_per_unit": round(logistics_per_unit, 2),
        "customs_per_unit":   round(customs_per_unit, 2),
        "unit_cost":          round(unit_cost, 2),
        "sell_price":         round(sell_price, 2),
    }
