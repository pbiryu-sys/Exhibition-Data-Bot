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
    exchange_rate    = s["exchange_rate"]       # manual internal procurement rate (USD → KZT), used ONLY for FOB
    container_cost   = s["container_cost"]      # KZT — stored and used as-is
    customs_fee_rate = s["customs_fee"]         # fraction (e.g. 0.15)
    broker_fee       = s["broker_fee"]          # KZT
    terminal_fee     = s["terminal_fee"]        # KZT
    storage_fee      = s["storage_fee"]         # KZT
    forwarder_fee    = s["forwarder_fee"]       # KZT
    margin           = s["margin"]
    vat              = s["vat"]

    # Step 1 — FOB_KZT = FOB_USD * exchange_rate (only place exchange_rate is applied)
    fob_kzt = fob_price_usd * exchange_rate

    # Step 2 — Container cost is already in KZT, no conversion
    container_cost_kzt = container_cost

    # Step 3 — Customs on total container FOB value, result in KZT
    customs_total_kzt = fob_kzt * qty_40hq * customs_fee_rate

    # Step 4 — All KZT logistics per container
    fixed_fees_kzt = broker_fee + terminal_fee + storage_fee + forwarder_fee

    # Step 5 — Total KZT for the whole container
    total_kzt = (
        fob_kzt * qty_40hq
        + container_cost_kzt
        + fixed_fees_kzt
        + customs_total_kzt
    )

    # Step 6 — Unit cost and sell price (E5 = qty_40hq)
    unit_cost  = total_kzt / qty_40hq if qty_40hq > 0 else 0
    sell_price = unit_cost / (1 - margin) * vat

    # Per-unit breakdown for card display
    logistics_per_unit = (
        (container_cost_kzt + fixed_fees_kzt) / qty_40hq if qty_40hq > 0 else 0
    )
    customs_per_unit = customs_total_kzt / qty_40hq if qty_40hq > 0 else 0

    return {
        "fob_kzt":            round(fob_kzt, 2),
        "logistics_per_unit": round(logistics_per_unit, 2),
        "customs_per_unit":   round(customs_per_unit, 2),
        "unit_cost":          round(unit_cost, 2),
        "sell_price":         round(sell_price, 2),
    }
