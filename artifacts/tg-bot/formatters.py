from datetime import datetime
from pricing import calculate_cost_per_unit


def format_product_card(data: dict, settings: dict) -> str:
    """Format card from creation-flow context.user_data (fields: price, article, qty_40hq…)."""
    _input_fob = data["price"]
    _input_qty = data["qty_40hq"]

    calc = calculate_cost_per_unit(
        fob_price_usd=_input_fob,
        qty_40hq=_input_qty,
        settings=settings,
    )

    if _input_qty != data["qty_40hq"]:
        raise ValueError(
            f"INPUT_STATE_MISMATCH: _input_qty={_input_qty} != data['qty_40hq']={data['qty_40hq']}"
        )
    if _input_fob != data["price"]:
        raise ValueError(
            f"INPUT_STATE_MISMATCH: _input_fob={_input_fob} != data['price']={data['price']}"
        )

    factory    = data["factory"]
    exhibition = data.get("exhibition", "")
    category   = data["category"]
    article    = data["article"]
    note       = data.get("note", "—")
    added      = datetime.now().strftime("%d.%m.%Y")

    lines = [
        f"📦 <b>{category} | {article}</b>",
        "",
        f"🏭 {factory}",
        f"🎪 {exhibition}",
        f"🗓 Добавлено: {added}",
        "",
        f"💵 FOB: <b>${_input_fob:,.2f}</b>  ·  {_input_qty:,} шт / 40HQ",
        f"🚢 Контейнер: <b>${_input_fob * _input_qty:,.0f}</b>",
        "",
        f"💴 Себестоимость: <b>{calc['U5']:,.0f} ₸</b>",
        f"🏷 Розничная цена: <b>{calc['G5']:,.0f} ₸</b>",
    ]

    if note and note != "—":
        lines += ["", f"📝 {note}"]

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


def format_card_from_db(p: dict, settings: dict) -> str:
    """
    Full card renderer from a DB product object.
    Single source of truth for all post-edit re-renders.
    DB field names: fob_usd, sku, qty_40hq, factory, exhibition, category, note, attachments.
    """
    fob_usd  = p.get("fob_usd", 0) or 0
    qty_40hq = p.get("qty_40hq", 0) or 0

    calc = calculate_cost_per_unit(
        fob_price_usd=fob_usd,
        qty_40hq=qty_40hq,
        settings=settings,
    )

    factory    = p.get("factory") or "—"
    exhibition = p.get("exhibition") or ""
    category   = p.get("category") or "—"
    article    = p.get("sku") or "—"
    note       = p.get("note") or "—"
    added = (
        datetime.fromtimestamp(p["created_at"]).strftime("%d.%m.%Y")
        if p.get("created_at") else datetime.now().strftime("%d.%m.%Y")
    )
    hero_media = p.get("hero_media") or []

    lines = [
        f"📦 <b>{category} | {article}</b>",
        "",
        f"🏭 {factory}",
        f"🎪 {exhibition}",
        f"🗓 Добавлено: {added}",
        "",
        f"💵 FOB: <b>${fob_usd:,.2f}</b>  ·  {qty_40hq:,} шт / 40HQ",
        f"🚢 Контейнер: <b>${fob_usd * qty_40hq:,.0f}</b>",
        "",
        f"💴 Себестоимость: <b>{calc['U5']:,.0f} ₸</b>",
        f"🏷 Розничная цена: <b>{calc['G5']:,.0f} ₸</b>",
    ]

    if note and note != "—":
        lines += ["", f"📝 {note}"]

    if hero_media:
        n_photo = sum(1 for m in hero_media if m.get("type") == "photo")
        n_video = sum(1 for m in hero_media if m.get("type") == "video")
        parts = []
        if n_photo:
            parts.append(f"📸 {n_photo}")
        if n_video:
            parts.append(f"📹 {n_video}")
        lines.append(f"🖼 Медиа: {' · '.join(parts)}")

    if settings.get("debug_mode"):
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
            f"FOB:  ${fob_usd} × {qty_40hq} шт × {O1} = {calc['K5']:,.2f} ₸",
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
        f"4. Таможня N5:   <b>{settings['customs_fee_kzt']:,.0f} ₸</b>",
        f"5. Терминал P5:  <b>{settings['terminal_fee_kzt']:,.0f} ₸</b>",
        f"6. Хранение Q5:  <b>{settings['storage_fee_kzt']:,.0f} ₸</b>",
        f"7. Брокер R5:    <b>{settings['broker_fee_kzt']:,.0f} ₸</b>",
        f"8. Экспедитор S5:<b>{settings['forwarder_fee_kzt']:,.0f} ₸</b>",
        f"9. Debug trace:  <b>{'ВКЛ 🟢' if settings.get('debug_mode') else 'ВЫКЛ 🔴'}</b>",
        "",
        "Нажмите кнопку для изменения параметра.",
    ]
    return "\n".join(lines)
