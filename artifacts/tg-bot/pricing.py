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
    Strict 1:1 replication of Excel columns F5–V5 + G5.

    Excel cell mapping:
      E5  = qty_40hq
      F5  = fob_price_usd
      O1  = exchange_rate        (manual internal rate, USD→KZT)
      O2  = container_cost_usd  (freight cost per container in USD, from settings)

      K5  = F5 * O1                              — FOB in KZT (container total)
      L5  = O1 * O2                              — container freight in KZT (rate × container_cost_usd)
      M5  = (K5 * 5%) + ((L5 * 95%) * 5%)       — insurance/handling
      N5  = settings.customs_fee_kzt             — customs duty (configurable flat KZT)
      O5  = ((L5 * 85%) + N5 + M5 + K5) * 16%  — VAT on container
      P5  = settings.terminal_fee_kzt
      Q5  = settings.storage_fee_kzt
      R5  = settings.broker_fee_kzt
      S5  = settings.forwarder_fee_kzt

      T5  = SUM(K5:S5)   — total container cost (aggregation layer)
      U5  = T5 / E5      — unit cost (unit derivation layer)
      V5  = T5 - O5      — net container cost ex-VAT (diagnostic)

    ── ROOT PRICING NODE ──────────────────────────────────────────────────────
      G5  = U5 / (1 - margin) * vat   PRIMARY SOURCE OF TRUTH (retail price)
            All validation must anchor to G5. T5/U5/V5 are cost-breakdown
            diagnostics only. G5_bot == G5_excel is the exact-match target.
    """
    # ── Excel named inputs — strict semantic bindings ────────────────────────
    O1 = settings["exchange_rate"]          # USD→KZT rate (Excel O1)

    # O2 MUST be container_cost_usd from settings — NEVER qty or any derived value.
    container_cost_usd = settings["container_cost"]
    if not isinstance(container_cost_usd, (int, float)) or container_cost_usd != settings["container_cost"]:
        raise ValueError(f"INVALID_CONTAINER_COST_BINDING: O2={container_cost_usd!r}")
    O2 = container_cost_usd                # alias kept for Excel column traceability

    E5 = qty_40hq                          # quantity — separate variable, never aliased to O2

    margin = settings["margin"]
    vat    = settings["vat"]

    # ── Stage 1: build frozen base-cell snapshot ─────────────────────────────
    # Evaluation order mirrors Excel dependency graph exactly.
    # Each cell is written ONCE to snap[]. After its line, it is immutable.
    # Downstream cells read only from snap[] — never re-derive any value.

    snap = {}

    # STEP 1 — L5 (shared dependency: used by both M5 and O5; must freeze first)
    snap["L5"] = O1 * O2                   # exchange_rate × container_cost_usd — STATIC, frozen from here

    # STEP 2 — K5 (independent of L5)
    snap["K5"] = fob_price_usd * E5 * O1  # F5 * E5 * O1 — full precision

    # STEP 3 — M5 (depends on frozen L5 and frozen K5 only)
    snap["M5"] = (snap["K5"] * 0.05) + ((snap["L5"] * 0.95) * 0.05)

    # STEP 4 — N5 (constant, no dependency)
    snap["N5"] = settings["customs_fee_kzt"]

    # Snapshot closed — L5/K5/M5/N5 will not be written again.
    # All downstream formulas reference snap[] exclusively.

    # ── Stage 2: downstream cells reference snapshot exclusively ─────────────

    # O5 uses ONLY frozen snap["L5"] and frozen snap["M5"] from above
    snap["O5"] = round(
        ((snap["L5"] * 0.85) + snap["N5"] + snap["M5"] + snap["K5"]) * 0.16,
        2,
    )

    snap["P5"] = settings["terminal_fee_kzt"]
    snap["Q5"] = settings["storage_fee_kzt"]
    snap["R5"] = settings["broker_fee_kzt"]
    snap["S5"] = settings["forwarder_fee_kzt"]

    snap["T5"] = round(
        snap["K5"] + snap["L5"] + snap["M5"] + snap["N5"] + snap["O5"]
        + snap["P5"] + snap["Q5"] + snap["R5"] + snap["S5"],
        2,
    )

    snap["U5"] = round(snap["T5"] / qty_40hq, 2) if qty_40hq > 0 else 0

    snap["V5"] = round(snap["T5"] - snap["O5"], 2)

    # ── G5: ROOT PRICING NODE — primary source of truth ──────────────────────
    # G5_bot == G5_excel is the exact-match validation target.
    # T5 / U5 / V5 above are cost-breakdown diagnostics only.
    snap["G5"] = round(snap["U5"] / (1 - margin) * vat, 2)

    # Convenience aliases for return / debug
    K5 = snap["K5"]; L5 = snap["L5"]; M5 = snap["M5"]; N5 = snap["N5"]
    O5 = snap["O5"]; P5 = snap["P5"]; Q5 = snap["Q5"]; R5 = snap["R5"]
    S5 = snap["S5"]; T5 = snap["T5"]; U5 = snap["U5"]; V5 = snap["V5"]
    G5 = snap["G5"]

    # ── Internal debug log (not shown in UI) ──────────────────────────────────
    logger.debug(
        "PRICING DEBUG | FOB_USD=%.4f qty=%d | "
        "K5(FOB_KZT)=%.4f | L5=%.4f | M5=%.4f | N5=%.2f | O5(VAT)=%.2f | "
        "P5=%.2f | Q5=%.2f | R5=%.2f | S5=%.2f | "
        "T5(TOTAL)=%.2f | U5(UNIT)=%.2f | V5(NET)=%.2f | "
        ">>> G5(ROOT)=%.2f <<<",
        fob_price_usd, qty_40hq,
        K5, L5, M5, N5, O5,
        P5, Q5, R5, S5,
        T5, U5, V5, G5,
    )

    return {
        # ── ROOT (primary validation target) ─────────────────────────────────
        "G5":                 G5,           # retail price — anchor G5_bot == G5_excel

        # ── Cost breakdown (diagnostic layer) ─────────────────────────────────
        "fob_kzt":            round(K5, 2),
        "L5":                 round(L5, 2),
        "M5":                 round(M5, 2),
        "N5":                 round(N5, 2),
        "O5_vat":             O5,
        "logistics_per_unit": round((L5 + M5 + N5 + O5 + P5 + Q5 + R5 + S5) / qty_40hq if qty_40hq > 0 else 0, 2),
        "customs_per_unit":   round(N5 / qty_40hq if qty_40hq > 0 else 0, 2),
        "total_container":    T5,
        "unit_cost":          U5,
        "net_ex_vat":         V5,

        # ── Legacy alias (keeps formatter working without change) ─────────────
        "sell_price":         G5,
    }
