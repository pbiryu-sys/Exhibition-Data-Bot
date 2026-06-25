from datetime import datetime
from pricing import calculate_cost_per_unit


def format_product_card(data: dict, settings: dict) -> str:
    calc = calculate_cost_per_unit(
        fob_price_usd=data["price"],
        qty_40hq=data["qty_40hq"],
        settings=settings,
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
        "",
        "Нажмите кнопку для изменения параметра.",
    ]
    return "\n".join(lines)
