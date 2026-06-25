from datetime import datetime
from pricing import calculate_cost_per_unit


def format_product_card(data: dict, settings: dict) -> str:
    calc = calculate_cost_per_unit(
        fob_price_usd=data["price"],
        qty_40hq=data["qty_40hq"],
        settings=settings,
    )

    year = datetime.now().year
    factory = data["factory"]
    category = data["category"]
    article = data["article"]
    note = data.get("note", "—")

    lines = [
        f"📦 <b>{category} | {article}</b>",
        "",
        f"🏭 Фабрика: <b>{factory}</b>",
        f"📅 Год: {year}",
        "",
        f"💵 FOB цена: <b>${data['price']:.2f}</b>",
        f"   → в рублях: {calc['fob_rub']:,.0f} ₽",
        f"📦 40HQ кол-во: <b>{data['qty_40hq']:,}</b> шт",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "💰 <b>Расчёт стоимости</b>",
        f"   Логистика/шт: {calc['container_per_unit']:,.0f} ₽",
        f"   Таможня/шт:   {calc['customs_per_unit']:,.0f} ₽",
        f"   Себестоимость: <b>{calc['cost']:,.0f} ₽</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏷 <b>Розничная цена: {calc['sell_price']:,.0f} ₽</b>",
        f"   (маржа {int(settings['margin']*100)}%, НДС {int((settings['vat']-1)*100)}%)",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📝 Заметка: {note}",
        "",
        f"🔗 Тег: #{factory.replace(' ', '_')}_{year}",
    ]

    return "\n".join(lines)


def format_settings(settings: dict) -> str:
    lines = [
        "⚙️ <b>Текущие настройки</b>",
        "",
        f"1. Маржа:              <b>{settings['margin']}</b> ({int(settings['margin']*100)}%)",
        f"2. НДС:                <b>{settings['vat']}</b> ({int((settings['vat']-1)*100)}%)",
        f"3. Курс USD/RUB:       <b>{settings['exchange_rate']}</b>",
        f"4. Стоимость контейнера: <b>{settings['container_cost']:,.0f} ₽</b>",
        f"5. Таможня (%):        <b>{settings['customs_fee']}</b> ({int(settings['customs_fee']*100)}%)",
        f"6. Брокер:             <b>{settings['broker_fee']:,.0f} ₽</b>",
        f"7. Терминал:           <b>{settings['terminal_fee']:,.0f} ₽</b>",
        f"8. Хранение:           <b>{settings['storage_fee']:,.0f} ₽</b>",
        f"9. Экспедитор:         <b>{settings['forwarder_fee']:,.0f} ₽</b>",
        "",
        "Нажмите кнопку для изменения параметра.",
    ]
    return "\n".join(lines)
