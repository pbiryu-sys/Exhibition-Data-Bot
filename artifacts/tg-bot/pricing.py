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
    Strict 1:1 replication of Excel columns F5–V5.

    Excel cell mapping:
      E5  = qty_40hq
      F5  = fob_price_usd
      O1  = exchange_rate   (manual internal rate, USD→KZT)
      O2  = qty_40hq        (same as E5, used in L5 formula)

      K5  = F5 * O1                              — FOB in KZT (per unit × rate)
      L5  = O1 * O2                              — container freight base (rate × qty)
      M5  = (K5 * 5%) + ((L5 * 95%) * 5%)       — insurance/handling
      N5  = settings.customs_fee_kzt             — customs duty (configurable flat KZT)
      O5  = ((L5 * 85%) + N5 + M5 + K5) * 16%  — VAT on container
      P5  = settings.terminal_fee_kzt
      Q5  = settings.storage_fee_kzt
      R5  = settings.broker_fee_kzt
      S5  = settings.forwarder_fee_kzt

      T5  = SUM(K5:S5)   — total container cost
      U5  = T5 / E5      — unit cost
      V5  = T5 - O5      — net container cost (ex-VAT)

    SELL_PRICE = U5 / (1 - margin) * vat
    """
    O1 = settings["exchange_rate"]       # manual internal procurement rate
    O2 = qty_40hq                        # E5 / O2 are both qty_40hq

    margin = settings["margin"]
    vat    = settings["vat"]

    # ── Excel columns ─────────────────────────────────────────────────────────

    K5 = fob_price_usd * O1                          # FOB KZT per unit

    L5 = O1 * O2                                     # container freight base

    M5 = (K5 * 0.05) + ((L5 * 0.95) * 0.05)        # insurance/handling

    N5 = settings["customs_fee_kzt"]                 # customs duty flat KZT

    O5 = ((L5 * 0.85) + N5 + M5 + K5) * 0.16       # VAT on container

    P5 = settings["terminal_fee_kzt"]
    Q5 = settings["storage_fee_kzt"]
    R5 = settings["broker_fee_kzt"]
    S5 = settings["forwarder_fee_kzt"]

    T5 = K5 + L5 + M5 + N5 + O5 + P5 + Q5 + R5 + S5   # SUM(K5:S5)

    U5 = T5 / qty_40hq if qty_40hq > 0 else 0           # unit cost

    V5 = T5 - O5                                          # net ex-VAT

    SELL_PRICE = U5 / (1 - margin) * vat

    # ── Internal debug log (not shown in UI) ──────────────────────────────────
    logger.debug(
        "PRICING DEBUG | FOB_USD=%.4f qty=%d | "
        "K5(FOB_KZT)=%.2f | L5=%.2f | M5=%.2f | N5=%.2f | O5(VAT)=%.2f | "
        "P5=%.2f | Q5=%.2f | R5=%.2f | S5=%.2f | "
        "T5(TOTAL)=%.2f | U5(UNIT_COST)=%.2f | V5(NET)=%.2f | SELL=%.2f",
        fob_price_usd, qty_40hq,
        K5, L5, M5, N5, O5,
        P5, Q5, R5, S5,
        T5, U5, V5, SELL_PRICE,
    )

    return {
        "fob_kzt":            round(K5, 2),
        "L5":                 round(L5, 2),
        "M5":                 round(M5, 2),
        "N5":                 round(N5, 2),
        "O5_vat":             round(O5, 2),
        "logistics_per_unit": round((L5 + M5 + N5 + O5 + P5 + Q5 + R5 + S5) / qty_40hq if qty_40hq > 0 else 0, 2),
        "customs_per_unit":   round(N5 / qty_40hq if qty_40hq > 0 else 0, 2),
        "total_container":    round(T5, 2),
        "unit_cost":          round(U5, 2),
        "net_ex_vat":         round(V5, 2),
        "sell_price":         round(SELL_PRICE, 2),
    }
