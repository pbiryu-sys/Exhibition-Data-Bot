from datetime import datetime
from pricing import calculate_cost_per_unit, calculate_pl


def _pl_line(fob_usd, qty, settings) -> str:
    pct = round(settings.get("pl_target_margin", 0.08) * 100, 1)
    pct = int(pct) if pct == int(pct) else pct
    pl  = calculate_pl(fob_usd, qty, settings)
    if not pl:
        return "🇵🇱 Allegro: — (маржа недостижима при текущих настройках)"
    return f"🇵🇱 Allegro (чистая маржа {pct}%): <b>{pl['price']:,.0f} zł</b>"


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
        f"💴 Себестоимость (без НДС): <b>{calc['V5'] / _input_qty:,.0f} ₸</b>",
        f"🇰🇿 Розничная цена: <b>{calc['G5']:,.0f} ₸</b>",
        f"🇷🇺 Цена РФ (с НДС): <b>{calc['AC5']:,.0f} ₽</b>",
        f"🤝 СоИнвест (−{int(round(settings.get('soinvest_discount', 0.3) * 100))}%): <b>{calc['AD5']:,.0f} ₽</b>",
        _pl_line(_input_fob, _input_qty, settings),
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
            f"P5  Брокер:         {calc['P5']:>14,.2f}",
            f"Q5  Терминал:       {calc['Q5']:>14,.2f}",
            f"R5  Хранение:       {calc['R5']:>14,.2f}",
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
        f"💴 Себестоимость (без НДС): <b>{calc['V5'] / qty_40hq if qty_40hq else 0:,.0f} ₸</b>",
        f"🇰🇿 Розничная цена: <b>{calc['G5']:,.0f} ₸</b>",
        f"🇷🇺 Цена РФ (с НДС): <b>{calc['AC5']:,.0f} ₽</b>",
        f"🤝 СоИнвест (−{int(round(settings.get('soinvest_discount', 0.3) * 100))}%): <b>{calc['AD5']:,.0f} ₽</b>",
        _pl_line(fob_usd, qty_40hq, settings),
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

    docs = p.get("documents") or []
    if docs:
        lines.append(f"📎 Документов: {len(docs)}")

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
            f"P5  Брокер:         {calc['P5']:>14,.2f}",
            f"Q5  Терминал:       {calc['Q5']:>14,.2f}",
            f"R5  Хранение:       {calc['R5']:>14,.2f}",
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
    sheet_id  = settings.get("google_sheet_id")  or "❌ не задан"
    folder_id = settings.get("google_drive_folder_id") or "— (опционально)"
    lines = [
        "⚙️ <b>Текущие настройки</b>",
        "",
        f"1. Маржа:        <b>{settings['margin']}</b> ({int(settings['margin']*100)}%)",
        f"2. НДС:          <b>{settings['vat']}</b> (×{settings['vat']})",
        f"3. Курс USD/KZT: <b>{settings['exchange_rate']}</b> (O1)",
        f"4. Контейнер O2: <b>${settings['container_cost']:,.0f}</b> (USD)",
        f"5. Таможня N5:   <b>{settings['customs_fee_kzt']:,.0f} ₸</b>",
        f"6. Брокер P5:    <b>{settings['broker_fee_kzt']:,.0f} ₸</b>",
        f"7. Терминал Q5:  <b>{settings['terminal_fee_kzt']:,.0f} ₸</b>",
        f"8. Хранение R5:  <b>{settings['storage_fee_kzt']:,.0f} ₸</b>",
        f"9. Экспедитор S5:<b>{settings['forwarder_fee_kzt']:,.0f} ₸</b>",
        f"10. Google Sheet ID: <b>{sheet_id}</b>",
        f"11. Google Drive Folder: <b>{folder_id}</b>",
        f"12. Debug trace: <b>{'ВКЛ 🟢' if settings.get('debug_mode') else 'ВЫКЛ 🔴'}</b>",
        f"13. Курс RUB→KZT (T1): <b>{settings.get('rub_to_kzt')}</b>",
        f"14. Объём контейнера, м³: <b>{settings.get('ru_container_m3')}</b>",
        f"15. Доставка в РФ (Z2): <b>{settings.get('ru_delivery_rub', 0):,.0f} ₽</b>",
        f"16. Объём для доставки, м³ (Z3): <b>{settings.get('ru_delivery_m3')}</b>",
        f"17. Делитель цены РФ (AB): <b>{settings.get('ru_price_divisor')}</b>",
        f"18. НДС РФ (AC): <b>{settings.get('ru_vat')}</b>",
        f"19. Скидка СоИнвест (AD): <b>{settings.get('soinvest_discount')}</b>",
        f"20. Польша: курс USD/PLN: <b>{settings.get('pl_fx')}</b>",
        f"21. Польша: фрахт 40HQ, $: <b>{settings.get('pl_freight_usd')}</b>",
        f"22. Польша: пошлина: <b>{settings.get('pl_duty')}</b>",
        f"23. Allegro: комиссия: <b>{settings.get('pl_commission')}</b>",
        f"24. Allegro: лимит комиссии, zł: <b>{settings.get('pl_commission_cap')}</b>",
        f"25. Allegro Ads: <b>{settings.get('pl_ads')}</b>",
        f"26. Доставка + 3PL, zł: <b>{settings.get('pl_delivery')}</b>",
        f"27. Гарантия и возвраты: <b>{settings.get('pl_warranty')}</b>",
        f"28. Оверхед страны: <b>{settings.get('pl_overhead')}</b>",
        f"29. CIT: <b>{settings.get('pl_cit')}</b>",
        f"30. НДС Польши: <b>{settings.get('pl_vat')}</b>",
        f"31. Целевая чистая маржа PL: <b>{settings.get('pl_target_margin')}</b>",
        "",
        "Нажмите кнопку для изменения параметра.",
    ]
    return "\n".join(lines)
