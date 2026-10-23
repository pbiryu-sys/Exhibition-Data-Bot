import json
import logging
import math
import os

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")
logger = logging.getLogger(__name__)

RU_DEFAULTS = {
    "rub_to_kzt": 5.1,
    "ru_container_m3": 68,
    "ru_delivery_rub": 250000,
    "ru_delivery_m3": 75,
    "ru_price_divisor": 0.22,
    "ru_vat": 1.22,
    "soinvest_discount": 0.30,
}

PL_DEFAULTS = {
    "pl_vat": 0.23,
    "pl_fx": 4.0,
    "pl_freight_usd": 5500,
    "pl_duty": 0.027,
    "pl_commission": 0.105,
    "pl_commission_cap": 120,
    "pl_ads": 0.07,
    "pl_delivery": 50,
    "pl_warranty": 0.025,
    "pl_overhead": 0.05,
    "pl_cit": 0.19,
    "pl_target_margin": 0.08,
}


def load_settings() -> dict:
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return {**RU_DEFAULTS, **PL_DEFAULTS, **json.load(f)}


def save_settings(settings: dict) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def calculate_cost_per_unit(fob_price_usd: float, qty_40hq: int, settings: dict) -> dict:
    """
    Strict cell-by-cell Excel parity. Evaluation order is fixed and must not change.

    Excel input mapping:
      C5 = fob_price_usd   (FOB price per unit, USD)
      E5 = qty_40hq        (units per 40HQ container)
      O1 = exchange_rate   (USD→KZT rate, from settings)
      O2 = container_cost  (container freight cost USD, from settings)

    Cell formulas (exact Excel column order K→S→T→U→V→G):
      K5 = (C5 * E5) * O1
      L5 = O1 * O2
      M5 = (K5 * 0.05) + ((L5 * 0.95) * 0.05)
      N5 = customs_fee_kzt                             (settings, fixed 23592)
      O5 = ((L5 * 0.85) + N5 + M5 + K5) * 0.16
      P5 = broker_fee_kzt                              (settings, 105000)
      Q5 = terminal_fee_kzt                            (settings,  50000)
      R5 = storage_fee_kzt                             (settings,  30000)
      S5 = forwarder_fee_kzt                           (settings,  90000)
      T5 = SUM(K5:S5)
      U5 = T5 / E5
      V5 = T5 - O5
      G5 = V5 / E5 / (1 - margin) * vat
    """
    # ── Named inputs (must never be swapped or aliased) ──────────────────────
    exchange_rate   = settings["exchange_rate"]    # O1
    container_cost  = settings["container_cost"]   # O2 — settings-driven, USD
    margin          = settings["margin"]
    vat             = settings["vat"]

    # ── Cell-by-cell computation — exact Excel order, no reordering allowed ──
    K5 = (fob_price_usd * qty_40hq) * exchange_rate
    L5 = exchange_rate * container_cost
    M5 = (K5 * 0.05) + ((L5 * 0.95) * 0.05)
    N5 = settings["customs_fee_kzt"]
    O5 = ((L5 * 0.85) + N5 + M5 + K5) * 0.16
    P5 = settings["broker_fee_kzt"]       # P5 = broker   (105 000 ₸)
    Q5 = settings["terminal_fee_kzt"]     # Q5 = terminal  (50 000 ₸)
    R5 = settings["storage_fee_kzt"]      # R5 = storage   (30 000 ₸)
    S5 = settings["forwarder_fee_kzt"]    # S5 = forwarder (90 000 ₸)
    T5 = K5 + L5 + M5 + N5 + O5 + P5 + Q5 + R5 + S5
    U5 = T5 / qty_40hq if qty_40hq > 0 else 0
    V5 = T5 - O5
    G5 = V5 / qty_40hq / (1 - margin) * vat if qty_40hq > 0 else 0

    # ── Debug log ─────────────────────────────────────────────────────────────
    # RU price layer (Excel columns X..AD), built on top of the KZ chain
    rub_to_kzt   = settings.get("rub_to_kzt", RU_DEFAULTS["rub_to_kzt"])
    ru_cont_m3   = settings.get("ru_container_m3", RU_DEFAULTS["ru_container_m3"])
    ru_deliv_rub = settings.get("ru_delivery_rub", RU_DEFAULTS["ru_delivery_rub"])
    ru_deliv_m3  = settings.get("ru_delivery_m3", RU_DEFAULTS["ru_delivery_m3"])
    ru_divisor   = settings.get("ru_price_divisor", RU_DEFAULTS["ru_price_divisor"])
    ru_vat       = settings.get("ru_vat", RU_DEFAULTS["ru_vat"])
    soi_discount = settings.get("soinvest_discount", RU_DEFAULTS["soinvest_discount"])
    X5  = V5 / qty_40hq if qty_40hq > 0 else 0
    Y5  = ru_cont_m3 / qty_40hq if qty_40hq > 0 else 0
    Z5  = Y5 * (ru_deliv_rub / ru_deliv_m3)
    AA5 = X5 / rub_to_kzt + Z5
    AB5 = AA5 / ru_divisor
    AC5 = AB5 * ru_vat
    AD5 = AC5 - AC5 * soi_discount

    logger.debug(
        "PRICING | C5=%.4f E5=%d O1=%.2f O2=%.2f | "
        "K5=%.2f L5=%.2f M5=%.2f N5=%.2f O5=%.2f "
        "P5=%.2f Q5=%.2f R5=%.2f S5=%.2f "
        "T5=%.2f U5=%.2f V5=%.2f >>> G5=%.2f <<<",
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

        "X5": round(X5, 2),
        "Y5": round(Y5, 6),
        "Z5": round(Z5, 2),
        "AA5": round(AA5, 2),
        "AB5": round(AB5, 2),
        "AC5": round(AC5, 2),
        "AD5": round(AD5, 2),

        # Input snapshot for debug trace
        "O1": exchange_rate,
        "O2": container_cost,
        "E5": qty_40hq,

        # Legacy aliases (formatter compatibility)
        "fob_kzt":         round(K5, 2),
        "O5_vat":          round(O5, 2),
        "total_container": round(T5, 2),
        "unit_cost":       round(U5, 2),
        "net_ex_vat":      round(V5, 2),
        "sell_price":      round(G5, 2),
    }


# ── Poland / Allegro calculator (GENAU_Allegro_profit_calculator.xlsx) ──

def _pl(s: dict, key: str) -> float:
    return s.get(key, PL_DEFAULTS[key])


def _pl_landed(fob_usd: float, qty: int, s: dict) -> float:
    fx      = _pl(s, "pl_fx")
    factory = fob_usd * fx
    freight = _pl(s, "pl_freight_usd") / qty * fx if qty > 0 else 0
    duty    = (factory + freight) * _pl(s, "pl_duty")
    return factory + freight + duty


def pl_unit_economics(price: float, fob_usd: float, qty: int, s: dict) -> dict:
    """Per-unit economics at a given Allegro retail price (PLN, incl. VAT). Mirrors Excel C23:C41."""
    revenue      = price / (1 + _pl(s, "pl_vat"))
    landed       = _pl_landed(fob_usd, qty, s)
    commission   = min(price * _pl(s, "pl_commission"), _pl(s, "pl_commission_cap"))
    ads          = price * _pl(s, "pl_ads")
    delivery     = _pl(s, "pl_delivery")
    warranty     = revenue * _pl(s, "pl_warranty")
    contribution = revenue - (landed + commission + ads + delivery + warranty)
    overhead     = revenue * _pl(s, "pl_overhead")
    pbt          = contribution - overhead
    cit          = max(pbt, 0) * _pl(s, "pl_cit")
    net          = pbt - cit
    return {
        "revenue": revenue, "landed": landed, "commission": commission, "ads": ads,
        "delivery": delivery, "warranty": warranty, "contribution": contribution,
        "overhead": overhead, "pbt": pbt, "cit": cit, "net": net,
        "net_margin": net / revenue if revenue > 0 else 0,
    }


def pl_price_for_margin(fob_usd: float, qty: int, s: dict, margin: float | None = None):
    """Closed-form retail price (PLN incl. VAT) giving the target NET margin after CIT.
    Handles the Allegro commission cap. Returns None if the margin is unreachable."""
    if qty <= 0:
        return None
    m   = _pl(s, "pl_target_margin") if margin is None else margin
    mt  = m / (1 - _pl(s, "pl_cit"))
    vat = _pl(s, "pl_vat")
    c, a, cap = _pl(s, "pl_commission"), _pl(s, "pl_ads"), _pl(s, "pl_commission_cap")
    k   = (1 - mt - _pl(s, "pl_warranty") - _pl(s, "pl_overhead")) / (1 + vat)
    base = _pl_landed(fob_usd, qty, s) + _pl(s, "pl_delivery")
    den = k - c - a
    if den > 0:
        price = base / den
        if price * c <= cap:
            return price
    den = k - a
    if den <= 0:
        return None
    return (base + cap) / den


def calculate_pl(fob_usd: float, qty: int, settings: dict):
    """Allegro price rounded up to a whole zloty that reaches the target net margin."""
    exact = pl_price_for_margin(fob_usd, qty, settings)
    if exact is None:
        return None
    price = math.ceil(exact - 1e-9)
    eco   = pl_unit_economics(price, fob_usd, qty, settings)
    return {
        "price_exact": round(exact, 2),
        "price": price,
        "net_per_unit": round(eco["net"], 2),
        "net_margin": eco["net_margin"],
        "net_per_container": round(eco["net"] * qty, 2),
        "eco": eco,
    }
