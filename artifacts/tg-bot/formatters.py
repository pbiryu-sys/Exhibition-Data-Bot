from datetime import datetime
from pricing import calculate_cost_per_unit


def format_product_card(data: dict, settings: dict) -> str:
    # ── Single shared input state ─────────────────────────────────────────────
    # All pricing outputs (card + debug trace) derive from ONE call.
    # qty_40hq is bound here from data["qty_40hq"] and nowhere else.
    _input_fob = data["price"]
    _input_qty = data["qty_40hq"]

    calc = calculate_cost_per_unit(
        fob_price_usd=_input_fob,
        qty_40hq=_input_qty,
        settings=settings,
    )

    # ── Input state validation ─────────────────────────────────────────────────
    # calc["E5"] is the qty the pricing engine actually used (set from qty_40hq).
    # If it ever diverges from the input, all downstream cells are invalid.
    if calc["E5"] != _input_qty:
        raise ValueError(
            f"INPUT_STATE_MISMATCH: bot_qty={_input_qty} != engine_qty={calc['E5']}"
        )

    year       = datetime.now().year
    factory    = data["factory"]
    exhibition = data.get("exhibition", "")
    category   = data["category"]
    article    = data["article"]
    note       = data.get("note", "—")
    tag        = f"{factory} | {exhibition}" if exhibition else factory

    lines = [
        f"📦 <b>{category} | {article}</b>",
        "",
        f"🏭 Фабрика: <b>{factory}</b>",
        f"🎪 Выставка: <b>{exhibition}</b>",
        f"📅 Год: {year}",
        "",
        f"💵 FOB цена: <b>${data['price']:.2f}</b>",
        f"   → K5 (FOB KZT): {calc['fob_kzt']:,.0f} ₸",
        f"📦 40HQ кол-во: <b>{data['qty_40hq']:,}</b> шт",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "💰 <b>Расчёт стоимости (KZT)</b>",
        f"   Таможня N5:     {calc['N5']:,.0f} ₸",
        f"   НДС O5:         {calc['O5_vat']:,.0f} ₸",
        f"   Прочие сборы:   {calc['logistics_per_unit']:,.0f} ₸/шт",
        f"   T5 (итого контейнер): <b>{calc['total_container']:,.0f} ₸</b>",
        f"   Себестоимость U5: <b>{calc['unit_cost']:,.0f} ₸</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏷 <b>Розничная цена: {calc['sell_price']:,.0f} ₸</b>",
        f"   (маржа {int(settings['margin']*100)}%, НДС ×{settings['vat']})",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📝 Заметка: {note}",
        "",
        f"🔗 Тег: {tag}",
    ]

    if settings.get("debug_mode"):
        fob_usd  = data["price"]
        qty      = data["qty_40hq"]
        O1       = calc["O1"]
        O2       = calc["O2"]
        margin   = settings["margin"]
        vat      = settings["vat"]
        vat_base = round((calc["L5"] * 0.85) + calc["N5"] + calc["M5"] + calc["K5"], 2)
        lines += [
            "",
            "────────────────────",
            "🔍 <b>DEBUG TRACE</b>",
            "────────────────────",
            f"K5  FOB KZT:        {calc['K5']:>14,.2f}",
            f"L5  Контейнер KZT:  {calc['L5']:>14,.2f}",
            f"M5  Страховка:      {calc['M5']:>14,.2f}",
            f"N5  Таможня:        {calc['N5']:>14,.2f}",
            f"O5  НДС:            {calc['O5']:>14,.2f}",
            f"P5  Терминал:       {calc['P5']:>14,.2f}",
            f"Q5  Хранение:       {calc['Q5']:>14,.2f}",
            f"R5  Брокер:         {calc['R5']:>14,.2f}",
            f"S5  Экспедитор:     {calc['S5']:>14,.2f}",
            f"T5  Итого контейнер:{calc['T5']:>14,.2f}",
            f"U5  Себестоимость:  {calc['U5']:>14,.2f}",
            f"V5  Без НДС:        {calc['V5']:>14,.2f}",
            "────────────────────",
            f"FOB:  ${fob_usd} × {qty} шт × {O1} = {calc['K5']:,.2f} ₸",
            f"Фрахт: {O1} × ${O2} = {calc['L5']:,.2f} ₸",
            f"База НДС (85%L5+N5+M5+K5): {vat_base:,.2f}",
            f"Маржа: {calc['U5']:,.2f} / (1 − {margin}) × {vat} = {calc['G5']:,.2f} ₸",
            f"<b>G5 (ROOT RETAIL): {calc['G5']:,.2f} ₸</b>",
        ]

    return "\n".join(lines)


def format_settings(settings: dict) -> str:
    lines = [
        "⚙️ <b>Текущие настройки</b>",
        "",
        f"1. Маржа:        <b>{settings['margin']}</b> ({int(settings['margin']*100)}%)",
        f"2. НДС:          <b>{settings['vat']}</b> (×{settings['vat']})",
        f"3. Курс USD/KZT: <b>{settings['exchange_rate']}</b> (O1)",
        f"4. Контейнер:    <b>${settings['container_cost']:,.0f}</b> (O2, USD)",
        f"5. Таможня N5:   <b>{settings['customs_fee_kzt']:,.0f} ₸</b>",
        f"6. Терминал P5:  <b>{settings['terminal_fee_kzt']:,.0f} ₸</b>",
        f"7. Хранение Q5:  <b>{settings['storage_fee_kzt']:,.0f} ₸</b>",
        f"8. Брокер R5:    <b>{settings['broker_fee_kzt']:,.0f} ₸</b>",
        f"9. Экспедитор S5:<b>{settings['forwarder_fee_kzt']:,.0f} ₸</b>",
        f"10. Debug trace:  <b>{'ВКЛ 🟢' if settings.get('debug_mode') else 'ВЫКЛ 🔴'}</b>",
        "",
        "Нажмите кнопку для изменения параметра.",
    ]
    return "\n".join(lines)
