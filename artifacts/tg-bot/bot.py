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
)
from telegram.constants import ParseMode

from states import ProductState, SpeedState, SettingsState, ContextState
from pricing import load_settings, save_settings
from formatters import format_product_card, format_settings
from context_store import load_user_context, save_user_context, set_speed_mode

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

FACTORY_OPTIONS = ["DHZ", "WNQ", "Matrix OEM", "Johnson clone", "Другое (ввести)"]
EXHIBITION_OPTIONS = ["CF26", "FIBO26", "TaiSPO26", "IWF26", "Другое (ввести)"]

SPEED_BTN_OFF = "⚡ Speed Mode"
SPEED_BTN_ON  = "⚡ Speed: ВКЛ ✓"


# ─── Helpers ────────────────────────────────────────────────────────────────

def _main_keyboard(ctx: dict) -> ReplyKeyboardMarkup:
    speed_btn = SPEED_BTN_ON if ctx["speed_mode"] else SPEED_BTN_OFF
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Новый товар")],
            [KeyboardButton("🏭 Фабрика"), KeyboardButton("🎪 Выставка")],
            [KeyboardButton(speed_btn)],
            [KeyboardButton("⚙️ Настройки")],
        ],
        resize_keyboard=True,
    )


def _main_menu_text(ctx: dict) -> str:
    mode_line = "⚡ <b>Режим: СКОРОСТЬ</b>" if ctx["speed_mode"] else "🔄 Режим: обычный"
    return (
        "👋 <b>Бот учёта товаров с выставки</b>\n\n"
        f"🎪 Активная выставка: <b>{ctx['active_exhibition']}</b>\n"
        f"🏭 Активная фабрика: <b>{ctx['active_factory']}</b>\n"
        f"{mode_line}\n\n"
        "Нажмите <b>«➕ Новый товар»</b> чтобы добавить позицию."
    )


# ─── /start ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        _main_menu_text(ctx),
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )


# ─── Speed mode toggle ──────────────────────────────────────────────────────

async def speed_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    set_speed_mode(user_id, True)
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        "⚡ <b>Speed Mode включён!</b>\n\n"
        "После фото отправьте одно сообщение:\n"
        "<code>Артикул/SKU\n"
        "FOB цена (USD)\n"
        "40HQ кол-во\n"
        "Заметка (необязательно)</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )


async def speed_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    set_speed_mode(user_id, False)
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        "🔄 <b>Normal Mode включён.</b>\n\nВозвращаемся к пошаговому вводу.",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )


async def speed_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    if ctx["speed_mode"]:
        await speed_off(update, context)
    else:
        await speed_on(update, context)


# ─── Product conversation (normal mode) ────────────────────────────────────

async def product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    context.user_data.clear()
    context.user_data["factory"] = ctx["active_factory"]
    context.user_data["exhibition"] = ctx["active_exhibition"]

    if ctx["speed_mode"]:
        await update.message.reply_text(
            f"⚡ <b>SPEED MODE — Шаг 1/2 — Фото</b>\n\n"
            f"Фабрика: <b>{ctx['active_factory']}</b> | Выставка: <b>{ctx['active_exhibition']}</b>\n\n"
            "Пришлите фото или /skip:",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return SpeedState.PHOTO

    await update.message.reply_text(
        f"📸 <b>Шаг 1/6 — Фото товара</b>\n\n"
        f"Фабрика: <b>{ctx['active_factory']}</b> | Выставка: <b>{ctx['active_exhibition']}</b>\n\n"
        "Пришлите фото или нажмите /skip чтобы пропустить.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ProductState.PHOTO


# Normal flow handlers ───────────────────────────────────────────────────────

async def product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    else:
        context.user_data["photo_file_id"] = None
    await update.message.reply_text(
        "📂 <b>Шаг 2/6 — Категория</b>\n\nВведите категорию товара:",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.CATEGORY


async def product_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["photo_file_id"] = None
    await update.message.reply_text(
        "📂 <b>Шаг 2/6 — Категория</b>\n\nВведите категорию товара:",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.CATEGORY


async def product_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["category"] = update.message.text.strip()
    await update.message.reply_text(
        "🔖 <b>Шаг 3/6 — Артикул</b>\n\nВведите артикул товара:",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.ARTICLE


async def product_article(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["article"] = update.message.text.strip()
    await update.message.reply_text(
        "💵 <b>Шаг 4/6 — Цена FOB (USD)</b>\n\nВведите цену в долларах (напр. <code>12.50</code>):",
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
        await update.message.reply_text(
            "⚠️ Введите корректное число больше 0, например <code>12.50</code>",
            parse_mode=ParseMode.HTML,
        )
        return ProductState.PRICE

    await update.message.reply_text(
        "📦 <b>Шаг 5/6 — Количество в 40HQ</b>\n\nСколько единиц товара помещается в контейнер 40HQ?",
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
        await update.message.reply_text(
            "⚠️ Введите целое число больше 0, например <code>500</code>",
            parse_mode=ParseMode.HTML,
        )
        return ProductState.QTY_40HQ

    await update.message.reply_text(
        "📝 <b>Шаг 6/6 — Заметка</b>\n\nДобавьте заметку или нажмите /skip:",
        parse_mode=ParseMode.HTML,
    )
    return ProductState.NOTE


async def product_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["note"] = update.message.text.strip()
    return await finish_product(update, context)


async def product_skip_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["note"] = "—"
    return await finish_product(update, context)


# Speed flow handlers ────────────────────────────────────────────────────────

async def speed_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    else:
        context.user_data["photo_file_id"] = None
    await update.message.reply_text(
        "⚡ <b>Шаг 2/2 — Данные товара</b>\n\n"
        "Отправьте одним сообщением:\n"
        "<code>Артикул/SKU\n"
        "FOB цена (USD)\n"
        "40HQ кол-во\n"
        "Заметка (необязательно)</code>",
        parse_mode=ParseMode.HTML,
    )
    return SpeedState.DATA


async def speed_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["photo_file_id"] = None
    await update.message.reply_text(
        "⚡ <b>Шаг 2/2 — Данные товара</b>\n\n"
        "Отправьте одним сообщением:\n"
        "<code>Артикул/SKU\n"
        "FOB цена (USD)\n"
        "40HQ кол-во\n"
        "Заметка (необязательно)</code>",
        parse_mode=ParseMode.HTML,
    )
    return SpeedState.DATA


async def speed_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lines = [l.strip() for l in update.message.text.strip().splitlines() if l.strip()]

    if len(lines) < 3:
        await update.message.reply_text(
            "⚠️ Нужно минимум 3 строки:\n"
            "<code>Артикул/SKU\n"
            "FOB цена\n"
            "40HQ кол-во\n"
            "Заметка (необязательно)</code>\n\n"
            "Попробуйте снова:",
            parse_mode=ParseMode.HTML,
        )
        return SpeedState.DATA

    article = lines[0]

    try:
        price = float(lines[1].replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            f"⚠️ Строка 2 — неверная цена: <code>{lines[1]}</code>\n\nПопробуйте снова:",
            parse_mode=ParseMode.HTML,
        )
        return SpeedState.DATA

    try:
        qty = int(lines[2].replace(" ", ""))
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            f"⚠️ Строка 3 — неверное количество: <code>{lines[2]}</code>\n\nПопробуйте снова:",
            parse_mode=ParseMode.HTML,
        )
        return SpeedState.DATA

    note = lines[3] if len(lines) >= 4 else "—"

    context.user_data["article"] = article
    context.user_data["category"] = "—"
    context.user_data["price"] = price
    context.user_data["qty_40hq"] = qty
    context.user_data["note"] = note

    return await finish_product(update, context)


# Shared finish ──────────────────────────────────────────────────────────────

async def finish_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    settings = load_settings()
    card_text = format_product_card(data, settings)

    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    reply_markup = _main_keyboard(ctx)

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
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        "❌ Ввод отменён.",
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


# ─── Factory context conversation ───────────────────────────────────────────

def _factory_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(opt)] for opt in FACTORY_OPTIONS],
        resize_keyboard=True,
    )


async def factory_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        f"🏭 <b>Выбор фабрики</b>\n\nТекущая: <b>{ctx['active_factory']}</b>\n\nВыберите из списка:",
        parse_mode=ParseMode.HTML,
        reply_markup=_factory_keyboard(),
    )
    return ContextState.FACTORY_CHOOSE


async def factory_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "Другое (ввести)":
        await update.message.reply_text(
            "✏️ Введите название фабрики:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ContextState.FACTORY_CUSTOM

    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    save_user_context(user_id, text, ctx["active_exhibition"])
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        f"✅ Фабрика установлена: <b>{text}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


async def factory_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    save_user_context(user_id, text, ctx["active_exhibition"])
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        f"✅ Фабрика установлена: <b>{text}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


# ─── Exhibition context conversation ────────────────────────────────────────

def _exhibition_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(opt)] for opt in EXHIBITION_OPTIONS],
        resize_keyboard=True,
    )


async def exhibition_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        f"🎪 <b>Выбор выставки</b>\n\nТекущая: <b>{ctx['active_exhibition']}</b>\n\nВыберите из списка:",
        parse_mode=ParseMode.HTML,
        reply_markup=_exhibition_keyboard(),
    )
    return ContextState.EXHIBITION_CHOOSE


async def exhibition_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "Другое (ввести)":
        await update.message.reply_text(
            "✏️ Введите название выставки:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ContextState.EXHIBITION_CUSTOM

    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    save_user_context(user_id, ctx["active_factory"], text)
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        f"✅ Выставка установлена: <b>{text}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


async def exhibition_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    save_user_context(user_id, ctx["active_factory"], text)
    ctx = load_user_context(user_id)
    await update.message.reply_text(
        f"✅ Выставка установлена: <b>{text}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


# ─── Settings conversation ──────────────────────────────────────────────────

def _settings_keyboard() -> ReplyKeyboardMarkup:
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
        user_id = update.effective_user.id
        ctx = load_user_context(user_id)
        await update.message.reply_text(
            "✅ Настройки сохранены.",
            reply_markup=_main_keyboard(ctx),
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
            # Normal mode states
            ProductState.PHOTO: [
                MessageHandler(filters.PHOTO, product_photo),
                CommandHandler("skip", product_skip_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_photo),
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
            # Speed mode states
            SpeedState.PHOTO: [
                MessageHandler(filters.PHOTO, speed_photo),
                CommandHandler("skip", speed_skip_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, speed_photo),
            ],
            SpeedState.DATA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, speed_data)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    factory_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🏭 Фабрика$"), factory_start),
            CommandHandler("factory", factory_start),
        ],
        states={
            ContextState.FACTORY_CHOOSE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, factory_choose)
            ],
            ContextState.FACTORY_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, factory_custom)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    exhibition_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🎪 Выставка$"), exhibition_start),
            CommandHandler("exhibition", exhibition_start),
        ],
        states={
            ContextState.EXHIBITION_CHOOSE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibition_choose)
            ],
            ContextState.EXHIBITION_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibition_custom)
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

    speed_filter = filters.Regex(f"^({SPEED_BTN_OFF}|{SPEED_BTN_ON})$")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("speed", speed_on))
    app.add_handler(CommandHandler("normal", speed_off))
    app.add_handler(MessageHandler(speed_filter, speed_toggle))
    app.add_handler(factory_conv)
    app.add_handler(exhibition_conv)
    app.add_handler(product_conv)
    app.add_handler(settings_conv)

    logger.info("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
