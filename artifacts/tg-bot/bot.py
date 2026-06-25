import logging
import os
import sys

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
    CallbackContext,
)
from telegram.constants import ParseMode

from states import ProductState, SettingsState
from pricing import load_settings, save_settings
from formatters import format_product_card, format_settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

SETTINGS_KEYS = [
    ("margin", "Маржа (напр. 0.59)"),
    ("vat", "НДС множитель (напр. 1.16)"),
    ("exchange_rate", "Курс USD/RUB (напр. 90.0)"),
    ("container_cost", "Стоимость контейнера, ₽"),
    ("customs_fee", "Таможня, доля (напр. 0.15)"),
    ("broker_fee", "Брокер, ₽"),
    ("terminal_fee", "Терминал, ₽"),
    ("storage_fee", "Хранение, ₽"),
    ("forwarder_fee", "Экспедитор, ₽"),
]

SETTINGS_LABELS = {k: label for k, label in SETTINGS_KEYS}


# ─── /start ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [KeyboardButton("➕ Новый товар")],
        [KeyboardButton("⚙️ Настройки")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "👋 <b>Бот учёта товаров с выставки</b>\n\n"
        "Нажмите <b>«➕ Новый товар»</b> чтобы добавить позицию,\n"
        "или <b>«⚙️ Настройки»</b> чтобы изменить параметры расчёта.",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )


# ─── Product conversation ───────────────────────────────────────────────────

async def product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "📸 <b>Шаг 1/7 — Фото товара</b>\n\nПришлите фото или нажмите /skip чтобы пропустить.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ProductState.PHOTO


async def product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    else:
        context.user_data["photo_file_id"] = None
    return await ask_factory(update, context)


async def product_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["photo_file_id"] = None
    return await ask_factory(update, context)


async def ask_factory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🏭 <b>Шаг 2/7 — Фабрика</b>\n\nВведите название фабрики:",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.FACTORY


async def product_factory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["factory"] = update.message.text.strip()
    await update.message.reply_text(
        "📂 <b>Шаг 3/7 — Категория</b>\n\nВведите категорию товара:",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.CATEGORY


async def product_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["category"] = update.message.text.strip()
    await update.message.reply_text(
        "🔖 <b>Шаг 4/7 — Артикул</b>\n\nВведите артикул товара:",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.ARTICLE


async def product_article(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["article"] = update.message.text.strip()
    await update.message.reply_text(
        "💵 <b>Шаг 5/7 — Цена FOB (USD)</b>\n\nВведите цену в долларах (напр. <code>12.50</code>):",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.PRICE


async def product_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = float(update.message.text.replace(",", ".").strip())
        if price <= 0:
            raise ValueError
        context.user_data["price"] = price
    except ValueError:
        await update.message.reply_text("⚠️ Введите корректное число больше 0, например <code>12.50</code>", parse_mode=ParseMode.HTML)
        return ProductState.PRICE

    await update.message.reply_text(
        "📦 <b>Шаг 6/7 — Количество в 40HQ</b>\n\nСколько единиц товара помещается в контейнер 40HQ?",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.QTY_40HQ


async def product_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        qty = int(update.message.text.replace(" ", "").strip())
        if qty <= 0:
            raise ValueError
        context.user_data["qty_40hq"] = qty
    except ValueError:
        await update.message.reply_text("⚠️ Введите целое число больше 0, например <code>500</code>", parse_mode=ParseMode.HTML)
        return ProductState.QTY_40HQ

    await update.message.reply_text(
        "📝 <b>Шаг 7/7 — Заметка</b>\n\nДобавьте заметку или нажмите /skip:",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.NOTE


async def product_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["note"] = update.message.text.strip()
    return await finish_product(update, context)


async def product_skip_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["note"] = "—"
    return await finish_product(update, context)


async def finish_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    settings = load_settings()
    card_text = format_product_card(data, settings)

    keyboard = [[KeyboardButton("➕ Новый товар")], [KeyboardButton("⚙️ Настройки")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    if data.get("photo_file_id"):
        await update.message.reply_photo(
            photo=data["photo_file_id"],
            caption=card_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    else:
        await update.message.reply_text(
            card_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[KeyboardButton("➕ Новый товар")], [KeyboardButton("⚙️ Настройки")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "❌ Ввод отменён.",
        reply_markup=reply_markup,
    )
    return ConversationHandler.END


# ─── Settings conversation ──────────────────────────────────────────────────

def _settings_keyboard():
    buttons = []
    for i, (key, label) in enumerate(SETTINGS_KEYS, 1):
        buttons.append([KeyboardButton(f"{i}. {label}")])
    buttons.append([KeyboardButton("✅ Готово")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    settings = load_settings()
    await update.message.reply_text(
        format_settings(settings),
        parse_mode=ParseMode.HTML,
        reply_markup=_settings_keyboard(),
    )
    return SettingsState.CHOOSING


async def settings_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text == "✅ Готово":
        keyboard = [[KeyboardButton("➕ Новый товар")], [KeyboardButton("⚙️ Настройки")]]
        await update.message.reply_text(
            "✅ Настройки сохранены.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        )
        return ConversationHandler.END

    for i, (key, label) in enumerate(SETTINGS_KEYS, 1):
        if text.startswith(f"{i}."):
            context.user_data["editing_key"] = key
            context.user_data["editing_label"] = label
            settings = load_settings()
            current = settings[key]
            await update.message.reply_text(
                f"✏️ <b>{label}</b>\n\nТекущее значение: <code>{current}</code>\n\nВведите новое значение:",
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardRemove(),
            )
            return SettingsState.EDITING

    await update.message.reply_text("Выберите параметр из списка.", reply_markup=_settings_keyboard())
    return SettingsState.CHOOSING


async def settings_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key = context.user_data.get("editing_key")
    label = context.user_data.get("editing_label", key)

    try:
        value = float(update.message.text.replace(",", ".").strip())
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введите число (напр. <code>0.59</code> или <code>90.0</code>).",
            parse_mode=ParseMode.HTML,
        )
        return SettingsState.EDITING

    settings = load_settings()
    settings[key] = value
    save_settings(settings)

    await update.message.reply_text(
        f"✅ <b>{label}</b> обновлено: <code>{value}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=_settings_keyboard(),
    )

    settings = load_settings()
    await update.message.reply_text(
        format_settings(settings),
        parse_mode=ParseMode.HTML,
        reply_markup=_settings_keyboard(),
    )
    return SettingsState.CHOOSING


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    app = Application.builder().token(token).build()

    product_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Новый товар$"), product_start)],
        states={
            ProductState.PHOTO: [
                MessageHandler(filters.PHOTO, product_photo),
                CommandHandler("skip", product_skip_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_photo),
            ],
            ProductState.FACTORY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_factory)
            ],
            ProductState.CATEGORY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_category)
            ],
            ProductState.ARTICLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_article)
            ],
            ProductState.PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_price)
            ],
            ProductState.QTY_40HQ: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_qty)
            ],
            ProductState.NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_note),
                CommandHandler("skip", product_skip_note),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    settings_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⚙️ Настройки$"), settings_start)],
        states={
            SettingsState.CHOOSING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_choose)
            ],
            SettingsState.EDITING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_edit)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(product_conv)
    app.add_handler(settings_conv)

    logger.info("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
