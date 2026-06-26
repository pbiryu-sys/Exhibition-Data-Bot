import json
import logging
import os

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")
logger = logging.getLogger(__name__)


def load_settings() -> dict:
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_settings(settings: dict) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def calculate_cost_per_unit(fob_price_usd: float, qty_40hq: int, settings: dict) -> dict:
    """
    Strict cell-by-cell Excel parity. Evaluation order is fixed and must not change.

    Inputs:
      fob_price_usd  — F5  (FOB price per unit, USD)
      qty_40hq       — E5  (units per 40HQ container)
      exchange_rate  — O1  (USD→KZT manual internal rate)
      container_cost — O2  (container freight cost, USD)

    Cell formulas (exact order):
      K5 = fob_price_usd * qty_40hq * exchange_rate
      L5 = exchange_rate * container_cost
      M5 = (K5 * 0.05) + (L5 * 0.95 * 0.05)
      N5 = customs_fee_kzt
      O5 = ((L5 * 0.85) + N5 + M5 + K5) * 0.16
      P5 = terminal_fee_kzt
      Q5 = storage_fee_kzt
      R5 = broker_fee_kzt
      S5 = forwarder_fee_kzt
      T5 = K5 + L5 + M5 + N5 + O5 + P5 + Q5 + R5 + S5
      U5 = T5 / qty_40hq
      V5 = T5 - O5
      G5 = V5 / qty_40hq / (1 - margin) * vat
    """
    # Named inputs — must never be swapped or aliased to each other
    exchange_rate  = settings["exchange_rate"]    # O1
    container_cost = settings["container_cost"]   # O2 (USD)
    margin         = settings["margin"]
    vat            = settings["vat"]

    # ── Cell-by-cell computation — exact Excel order, no reordering allowed ──

    K5 = fob_price_usd * qty_40hq * exchange_rate
    L5 = exchange_rate * container_cost
    M5 = (K5 * 0.05) + (L5 * 0.95 * 0.05)
    N5 = settings["customs_fee_kzt"]
    O5 = ((L5 * 0.85) + N5 + M5 + K5) * 0.16
    P5 = settings["terminal_fee_kzt"]
    Q5 = settings["storage_fee_kzt"]
    R5 = settings["broker_fee_kzt"]
    S5 = settings["forwarder_fee_kzt"]
    T5 = K5 + L5 + M5 + N5 + O5 + P5 + Q5 + R5 + S5
    U5 = T5 / qty_40hq if qty_40hq > 0 else 0
    V5 = T5 - O5
    G5 = V5 / qty_40hq / (1 - margin) * vat if qty_40hq > 0 else 0

    # ── Internal debug log ────────────────────────────────────────────────────
    logger.debug(
        "PRICING | FOB=%.4f qty=%d O1=%.2f O2=%.2f | "
        "K5=%.4f L5=%.4f M5=%.4f N5=%.2f O5=%.4f "
        "P5=%.2f Q5=%.2f R5=%.2f S5=%.2f "
        "T5=%.4f U5=%.4f V5=%.4f >>> G5=%.4f <<<",
        fob_price_usd, qty_40hq, exchange_rate, container_cost,
        K5, L5, M5, N5, O5, P5, Q5, R5, S5, T5, U5, V5, G5,
    )

    return {
        # Primary Excel cells — raw floats, rounded only for display
        "K5": round(K5, 2),
        "L5": round(L5, 2),
        "M5": round(M5, 2),
        "N5": round(N5, 2),
        "O5": round(O5, 2),
        "P5": round(P5, 2),
        "Q5": round(Q5, 2),
        "R5": round(R5, 2),
        "S5": round(S5, 2),
        "T5": round(T5, 2),
        "U5": round(U5, 2),
        "V5": round(V5, 2),
        "G5": round(G5, 2),

        # Input snapshot (debug trace display only)
        "O1": exchange_rate,
        "O2": container_cost,
        "E5": qty_40hq,

        # Legacy aliases (formatter compatibility)
        "fob_kzt":            round(K5, 2),
        "O5_vat":             round(O5, 2),
        "total_container":    round(T5, 2),
        "unit_cost":          round(U5, 2),
        "net_ex_vat":         round(V5, 2),
        "sell_price":         round(G5, 2),
        "logistics_per_unit": round(
            (L5 + M5 + N5 + O5 + P5 + Q5 + R5 + S5) / qty_40hq, 2
        ) if qty_40hq > 0 else 0,
    }
