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

from states import ProductState, SettingsState, ContextState, FactoryMgmtState, ExhibitionMgmtState
from pricing import load_settings, save_settings
from formatters import format_product_card, format_settings
from context_store import (
    load_user_context, save_user_context, clear_user_context,
    get_factories, add_factory, delete_factory,
    get_exhibitions, add_exhibition, delete_exhibition, exhibition_display,
    add_message_entry, get_all_message_entries,
    get_recent_message_entries, clear_all_message_entries, remove_message_entries,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Module-level list; populated in main() so clear_history can reset FSM state.
_conv_handlers: list = []


# ─── Tracked reply helpers ───────────────────────────────────────────────────
# Every bot message goes through these so message IDs are registered for
# bulk-deletion on "Clear History".

async def _reply(update: Update, *args, **kwargs) -> "Message":
    sent = await update.message.reply_text(*args, **kwargs)
    add_message_entry(update.effective_user.id, update.effective_chat.id, sent.message_id)
    return sent


async def _reply_photo(update: Update, *args, **kwargs) -> "Message":
    sent = await update.message.reply_photo(*args, **kwargs)
    add_message_entry(update.effective_user.id, update.effective_chat.id, sent.message_id)
    return sent

ALLOWED_CATEGORIES = [
    "Беговая дорожка",
    "Велотренажер",
    "Спин-байк",
    "Эллиптический тренажер",
    "Гребной тренажер",
    "Степпер",
    "Виброплатформа",
    "Пилатес",
    "Мини-тренажеры",
    "Силовые тренажеры",
    "Мультистанции",
    "Силовое оборудование",
    "Другое",
]

DATA_PROMPT = (
    "📋 <b>Введите данные товара одним сообщением:</b>\n\n"
    "<code>Артикул\n"
    "Категория\n"
    "FOB цена\n"
    "Кол-во 40HQ\n"
    "Заметка (опционально)</code>\n\n"
    "Пример:\n"
    "<code>TM-500\n"
    "Беговая дорожка\n"
    "85.50\n"
    "200\n"
    "Хорошее качество</code>"
)

SETTINGS_KEYS = [
    ("margin", "Маржа (напр. 0.59)"),
    ("vat", "НДС множитель (напр. 1.16)"),
    ("exchange_rate", "Курс USD/KZT (O1)"),
    ("container_cost", "Контейнер USD (O2)"),
    ("customs_fee_kzt", "Таможня N5, ₸"),
    ("terminal_fee_kzt", "Терминал P5, ₸"),
    ("storage_fee_kzt", "Хранение Q5, ₸"),
    ("broker_fee_kzt", "Брокер R5, ₸"),
    ("forwarder_fee_kzt", "Экспедитор S5, ₸"),
    ("debug_mode", "Debug trace (вкл/выкл)"),
]


# ─── Helpers ────────────────────────────────────────────────────────────────

def _main_keyboard(ctx: dict) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Новый товар")],
            [KeyboardButton("🏭 Фабрика"), KeyboardButton("🎪 Выставка")],
            [KeyboardButton("⚙️ Настройки"), KeyboardButton("🗑 Очистить историю")],
            [KeyboardButton("🧹 Очистить бот за 48 часов")],
        ],
        resize_keyboard=True,
    )


def _category_keyboard() -> ReplyKeyboardMarkup:
    buttons = []
    row = []
    for cat in ALLOWED_CATEGORIES[:-1]:
        row.append(KeyboardButton(cat))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton(ALLOWED_CATEGORIES[-1])])  # "Другое" alone
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def _main_menu_text(ctx: dict) -> str:
    factory    = ctx["active_factory"]    or "⚠️ не задана"
    exhibition = ctx["active_exhibition"] or "⚠️ не задана"
    hint = ""
    if not ctx["active_factory"] or not ctx["active_exhibition"]:
        hint = "\n\n⚠️ Укажите фабрику и выставку перед добавлением товара."
    return (
        "👋 <b>Бот учёта товаров с выставки</b>\n\n"
        f"🎪 Активная выставка: <b>{exhibition}</b>\n"
        f"🏭 Активная фабрика: <b>{factory}</b>"
        f"{hint}"
    )


# ─── Clear history ──────────────────────────────────────────────────────────

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # 1. Delete all tracked bot messages from the chat
    for entry in get_all_message_entries(user_id):
        try:
            await context.bot.delete_message(chat_id=entry["chat_id"], message_id=entry["id"])
        except Exception:
            pass  # Already deleted, too old (>48h), or no permission — skip silently

    # 2. Wipe persistent message log (keeps factories/exhibitions/active context)
    clear_all_message_entries(user_id)

    # 3. Reset every ConversationHandler's FSM state for this user.
    #    Default key scheme: (chat_id, user_id)  [per_chat=True, per_user=True]
    fsm_key = (chat_id, user_id)
    for handler in _conv_handlers:
        try:
            handler._conversations.pop(fsm_key, None)
        except Exception:
            pass

    # 4. Clear all in-memory session buffers
    context.user_data.clear()
    context.chat_data.clear()

    await _reply(update,
        "История полностью очищена. Нажмите /start",
    )


# ─── Clean last 48 hours ────────────────────────────────────────────────────

async def clean_48h(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import time as _time
    user_id = update.effective_user.id
    cutoff = _time.time() - 48 * 3600

    entries = get_recent_message_entries(user_id, cutoff)
    deleted_ids: set[int] = set()
    for entry in entries:
        try:
            await context.bot.delete_message(
                chat_id=entry["chat_id"], message_id=entry["id"]
            )
            deleted_ids.add(entry["id"])
        except Exception:
            deleted_ids.add(entry["id"])  # count as done even if already gone

    remove_message_entries(user_id, deleted_ids)

    ctx = load_user_context(user_id)
    await update.message.reply_text(
        f"🧹 Удалено сообщений за 48 ч: {len(deleted_ids)}.",
        reply_markup=_main_keyboard(ctx),
    )


# ─── /start ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    await _reply(update,
        _main_menu_text(ctx),
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )


# ─── Product conversation ───────────────────────────────────────────────────

async def product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)

    # Block entry if factory or exhibition not explicitly set
    if not ctx["active_factory"] and not ctx["active_exhibition"]:
        await _reply(update,
            "⚠️ Сначала укажите <b>фабрику</b> и <b>выставку</b>.\n\n"
            "Нажмите «🏭 Фабрика» и «🎪 Выставка» в меню.",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(ctx),
        )
        return ConversationHandler.END

    if not ctx["active_factory"]:
        await _reply(update,
            "⚠️ Укажите <b>фабрику</b> перед добавлением товара.\n\n"
            "Нажмите «🏭 Фабрика» в меню.",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(ctx),
        )
        return ConversationHandler.END

    if not ctx["active_exhibition"]:
        await _reply(update,
            "⚠️ Укажите <b>выставку</b> перед добавлением товара.\n\n"
            "Нажмите «🎪 Выставка» в меню.",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(ctx),
        )
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["factory"] = ctx["active_factory"]
    context.user_data["exhibition"] = ctx["active_exhibition"]

    await _reply(update,
        f"📸 <b>Шаг 1/2 — Фото товара</b>\n\n"
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
    await _reply(update,
        f"📋 <b>Шаг 2/2 — Данные товара</b>\n\n{DATA_PROMPT}",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ProductState.DATA


async def product_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["photo_file_id"] = None
    await _reply(update,
        f"📋 <b>Шаг 2/2 — Данные товара</b>\n\n{DATA_PROMPT}",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ProductState.DATA


async def product_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse multiline input: Article / Category / FOB price / Qty / Note(optional)."""
    lines = [l.strip() for l in update.message.text.strip().splitlines() if l.strip()]

    # Check required field count
    if len(lines) < 4:
        names = ["Артикул", "Категория", "FOB цена", "Кол-во 40HQ"]
        missing = names[len(lines):]
        await _reply(update,
            "⚠️ Не хватает полей:\n" + "\n".join(f"• {m}" for m in missing) +
            "\n\nОтправьте данные снова, все поля в одном сообщении.",
            parse_mode=ParseMode.HTML,
        )
        return ProductState.DATA

    article  = lines[0]
    category = lines[1]
    fob_raw  = lines[2]
    qty_raw  = lines[3]
    note     = " ".join(lines[4:]) if len(lines) > 4 else "—"

    # Validate FOB price
    try:
        price = float(fob_raw.replace(",", ".").replace(" ", ""))
        if price <= 0:
            raise ValueError
    except ValueError:
        await _reply(update,
            f"⚠️ Неверная FOB цена: <code>{fob_raw}</code>\n\n"
            "Введите число больше 0, например <code>85.50</code>\n\nОтправьте данные снова.",
            parse_mode=ParseMode.HTML,
        )
        return ProductState.DATA

    # Validate qty
    try:
        qty = int(qty_raw.replace(" ", "").replace(",", ""))
        if qty <= 0:
            raise ValueError
    except ValueError:
        await _reply(update,
            f"⚠️ Неверное количество: <code>{qty_raw}</code>\n\n"
            "Введите целое число больше 0, например <code>500</code>\n\nОтправьте данные снова.",
            parse_mode=ParseMode.HTML,
        )
        return ProductState.DATA

    # Store validated numeric fields
    context.user_data.update({
        "article":  article,
        "price":    price,
        "qty_40hq": qty,
        "note":     note,
    })

    # Validate category
    if category not in ALLOWED_CATEGORIES:
        await _reply(update,
            f"⚠️ Категория <b>{category}</b> не распознана.\n\nВыберите из списка:",
            parse_mode=ParseMode.HTML,
            reply_markup=_category_keyboard(),
        )
        return ProductState.CATEGORY_PICK

    context.user_data["category"] = category
    return await finish_product(update, context)


async def product_category_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle category button selection when initial category was invalid."""
    text = update.message.text.strip()
    if text not in ALLOWED_CATEGORIES:
        await _reply(update,
            "Пожалуйста, выберите категорию из кнопок ниже:",
            reply_markup=_category_keyboard(),
        )
        return ProductState.CATEGORY_PICK
    context.user_data["category"] = text
    return await finish_product(update, context)


# ─── Shared finish ──────────────────────────────────────────────────────────

async def finish_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    settings = load_settings()
    card_text = format_product_card(data, settings)

    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    reply_markup = _main_keyboard(ctx)

    if data.get("photo_file_id"):
        sent = await _reply_photo(update,
            photo=data["photo_file_id"],
            caption=card_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    else:
        sent = await _reply(update,
            card_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    await _reply(update,
        "❌ Ввод отменён.",
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


# ─── Factory context conversation (select active) ────────────────────────────

def _factory_select_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    factories = get_factories(user_id)
    buttons = [[KeyboardButton(f)] for f in factories]
    buttons.append([KeyboardButton("➕ Добавить фабрику")])
    buttons.append([KeyboardButton("❌ Отмена")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


async def factory_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    factories = get_factories(user_id)
    current = ctx["active_factory"] or "не задана"
    if not factories:
        await _reply(update,
            f"🏭 <b>Выбор фабрики</b>\n\nСписок фабрик пуст.\n\n"
            "Используйте /factories чтобы добавить фабрику.",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(ctx),
        )
        return ConversationHandler.END
    await _reply(update,
        f"🏭 <b>Выбор фабрики</b>\n\nТекущая: <b>{current}</b>\n\nВыберите из списка:",
        parse_mode=ParseMode.HTML,
        reply_markup=_factory_select_keyboard(user_id),
    )
    return ContextState.FACTORY_CHOOSE


async def factory_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == "❌ Отмена":
        ctx = load_user_context(user_id)
        await _reply(update,"Отменено.", reply_markup=_main_keyboard(ctx))
        return ConversationHandler.END

    if text == "➕ Добавить фабрику":
        await _reply(update,
            "✏️ Введите название фабрики:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ContextState.FACTORY_CUSTOM

    factories = get_factories(user_id)
    if text in factories:
        ctx = load_user_context(user_id)
        save_user_context(user_id, text, ctx["active_exhibition"])
        ctx = load_user_context(user_id)
        await _reply(update,
            f"✅ Фабрика установлена: <b>{text}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(ctx),
        )
        return ConversationHandler.END

    await _reply(update,
        "Выберите фабрику из списка или нажмите «➕ Добавить фабрику».",
        reply_markup=_factory_select_keyboard(user_id),
    )
    return ContextState.FACTORY_CHOOSE


async def factory_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id
    add_factory(user_id, text)
    ctx = load_user_context(user_id)
    save_user_context(user_id, text, ctx["active_exhibition"])
    ctx = load_user_context(user_id)
    await _reply(update,
        f"✅ Фабрика добавлена и установлена: <b>{text}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


# ─── Factory management conversation (/factories) ───────────────────────────

def _factory_mgmt_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Добавить фабрику")],
            [KeyboardButton("📋 Список"), KeyboardButton("🗑 Удалить")],
            [KeyboardButton("❌ Выход")],
        ],
        resize_keyboard=True,
    )


async def factories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _reply(update,
        "🏭 <b>Управление фабриками</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=_factory_mgmt_keyboard(),
    )
    return FactoryMgmtState.MENU


async def factories_menu_handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == "❌ Выход":
        ctx = load_user_context(user_id)
        await _reply(update,"Готово.", reply_markup=_main_keyboard(ctx))
        return ConversationHandler.END

    if text == "📋 Список":
        factories = get_factories(user_id)
        if not factories:
            msg = "Список фабрик пуст."
        else:
            lines = [f"{i+1}. {f}" for i, f in enumerate(factories)]
            msg = "🏭 <b>Ваши фабрики:</b>\n\n" + "\n".join(lines)
        await _reply(update,msg, parse_mode=ParseMode.HTML,
                                        reply_markup=_factory_mgmt_keyboard())
        return FactoryMgmtState.MENU

    if text == "➕ Добавить фабрику":
        await _reply(update,
            "✏️ Введите название фабрики:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return FactoryMgmtState.ADD_NAME

    if text == "🗑 Удалить":
        factories = get_factories(user_id)
        if not factories:
            await _reply(update,"Список пуст.", reply_markup=_factory_mgmt_keyboard())
            return FactoryMgmtState.MENU
        lines = [f"{i+1}. {f}" for i, f in enumerate(factories)]
        await _reply(update,
            "🗑 <b>Удалить фабрику</b>\n\n" + "\n".join(lines) +
            "\n\nВведите номер для удаления:",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return FactoryMgmtState.DELETE_CHOOSE

    await _reply(update,"Выберите действие.", reply_markup=_factory_mgmt_keyboard())
    return FactoryMgmtState.MENU


async def factories_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    user_id = update.effective_user.id
    add_factory(user_id, name)
    await _reply(update,
        f"✅ Фабрика <b>{name}</b> добавлена.",
        parse_mode=ParseMode.HTML,
        reply_markup=_factory_mgmt_keyboard(),
    )
    return FactoryMgmtState.MENU


async def factories_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    try:
        idx = int(update.message.text.strip()) - 1
    except ValueError:
        await _reply(update,
            "Введите номер из списка.", reply_markup=_factory_mgmt_keyboard()
        )
        return FactoryMgmtState.MENU
    removed = delete_factory(user_id, idx)
    if removed:
        await _reply(update,
            f"🗑 Фабрика <b>{removed}</b> удалена.",
            parse_mode=ParseMode.HTML,
            reply_markup=_factory_mgmt_keyboard(),
        )
    else:
        await _reply(update,
            "Неверный номер.", reply_markup=_factory_mgmt_keyboard()
        )
    return FactoryMgmtState.MENU


# ─── Exhibition context conversation (select active) ─────────────────────────

def _exhibition_select_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    exhibitions = get_exhibitions(user_id)
    buttons = [[KeyboardButton(exhibition_display(ex))] for ex in exhibitions]
    buttons.append([KeyboardButton("➕ Добавить выставку")])
    buttons.append([KeyboardButton("❌ Отмена")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


async def exhibition_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    exhibitions = get_exhibitions(user_id)
    current = ctx["active_exhibition"] or "не задана"
    if not exhibitions:
        await _reply(update,
            f"🎪 <b>Выбор выставки</b>\n\nСписок выставок пуст.\n\n"
            "Используйте /exhibitions чтобы добавить выставку.",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(ctx),
        )
        return ConversationHandler.END
    await _reply(update,
        f"🎪 <b>Выбор выставки</b>\n\nТекущая: <b>{current}</b>\n\nВыберите из списка:",
        parse_mode=ParseMode.HTML,
        reply_markup=_exhibition_select_keyboard(user_id),
    )
    return ContextState.EXHIBITION_CHOOSE


async def exhibition_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == "❌ Отмена":
        ctx = load_user_context(user_id)
        await _reply(update,"Отменено.", reply_markup=_main_keyboard(ctx))
        return ConversationHandler.END

    if text == "➕ Добавить выставку":
        await _reply(update,
            "✏️ Введите название выставки (например: Canton Fair):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ContextState.EXHIBITION_CUSTOM

    # Match by display string
    exhibitions = get_exhibitions(user_id)
    for ex in exhibitions:
        if exhibition_display(ex) == text:
            ctx = load_user_context(user_id)
            save_user_context(user_id, ctx["active_factory"], text)
            ctx = load_user_context(user_id)
            await _reply(update,
                f"✅ Выставка установлена: <b>{text}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=_main_keyboard(ctx),
            )
            return ConversationHandler.END

    await _reply(update,
        "Выберите выставку из списка или нажмите «➕ Добавить выставку».",
        reply_markup=_exhibition_select_keyboard(user_id),
    )
    return ContextState.EXHIBITION_CHOOSE


async def exhibition_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save exhibition name and ask for year."""
    name = update.message.text.strip()
    context.user_data["new_exhibition_name"] = name
    await _reply(update,
        f"📅 Введите год выставки (например: <code>2026</code>) или /skip чтобы пропустить:",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ContextState.EXHIBITION_CUSTOM


async def exhibition_custom_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive year (or /skip), save exhibition and set active."""
    year = update.message.text.strip() if update.message.text else None
    name = context.user_data.get("new_exhibition_name", "")
    user_id = update.effective_user.id
    add_exhibition(user_id, name, year)
    display = exhibition_display({"name": name, "year": year})
    ctx = load_user_context(user_id)
    save_user_context(user_id, ctx["active_factory"], display)
    ctx = load_user_context(user_id)
    await _reply(update,
        f"✅ Выставка добавлена и установлена: <b>{display}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


async def exhibition_custom_skip_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /skip for year step — save with no year."""
    name = context.user_data.get("new_exhibition_name", "")
    user_id = update.effective_user.id
    add_exhibition(user_id, name, None)
    display = exhibition_display({"name": name, "year": None})
    ctx = load_user_context(user_id)
    save_user_context(user_id, ctx["active_factory"], display)
    ctx = load_user_context(user_id)
    await _reply(update,
        f"✅ Выставка добавлена и установлена: <b>{display}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(ctx),
    )
    return ConversationHandler.END


# ─── Exhibition management conversation (/exhibitions) ───────────────────────

def _exhibition_mgmt_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Добавить выставку")],
            [KeyboardButton("📋 Список"), KeyboardButton("🗑 Удалить")],
            [KeyboardButton("❌ Выход")],
        ],
        resize_keyboard=True,
    )


async def exhibitions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _reply(update,
        "🎪 <b>Управление выставками</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=_exhibition_mgmt_keyboard(),
    )
    return ExhibitionMgmtState.MENU


async def exhibitions_menu_handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == "❌ Выход":
        ctx = load_user_context(user_id)
        await _reply(update,"Готово.", reply_markup=_main_keyboard(ctx))
        return ConversationHandler.END

    if text == "📋 Список":
        exhibitions = get_exhibitions(user_id)
        if not exhibitions:
            msg = "Список выставок пуст."
        else:
            lines = [f"{i+1}. {exhibition_display(ex)}" for i, ex in enumerate(exhibitions)]
            msg = "🎪 <b>Ваши выставки:</b>\n\n" + "\n".join(lines)
        await _reply(update,msg, parse_mode=ParseMode.HTML,
                                        reply_markup=_exhibition_mgmt_keyboard())
        return ExhibitionMgmtState.MENU

    if text == "➕ Добавить выставку":
        await _reply(update,
            "✏️ Введите название выставки (например: Canton Fair):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ExhibitionMgmtState.ADD_NAME

    if text == "🗑 Удалить":
        exhibitions = get_exhibitions(user_id)
        if not exhibitions:
            await _reply(update,"Список пуст.", reply_markup=_exhibition_mgmt_keyboard())
            return ExhibitionMgmtState.MENU
        lines = [f"{i+1}. {exhibition_display(ex)}" for i, ex in enumerate(exhibitions)]
        await _reply(update,
            "🗑 <b>Удалить выставку</b>\n\n" + "\n".join(lines) +
            "\n\nВведите номер для удаления:",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return ExhibitionMgmtState.DELETE_CHOOSE

    await _reply(update,"Выберите действие.", reply_markup=_exhibition_mgmt_keyboard())
    return ExhibitionMgmtState.MENU


async def exhibitions_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    context.user_data["new_exhibition_name"] = name
    await _reply(update,
        f"📅 Введите год выставки (например: <code>2026</code>) или /skip чтобы пропустить:",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ExhibitionMgmtState.ADD_YEAR


async def exhibitions_add_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    year = update.message.text.strip() or None
    name = context.user_data.get("new_exhibition_name", "")
    user_id = update.effective_user.id
    add_exhibition(user_id, name, year)
    await _reply(update,
        f"✅ Выставка <b>{exhibition_display({'name': name, 'year': year})}</b> добавлена.",
        parse_mode=ParseMode.HTML,
        reply_markup=_exhibition_mgmt_keyboard(),
    )
    return ExhibitionMgmtState.MENU


async def exhibitions_add_year_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = context.user_data.get("new_exhibition_name", "")
    user_id = update.effective_user.id
    add_exhibition(user_id, name, None)
    await _reply(update,
        f"✅ Выставка <b>{name}</b> добавлена.",
        parse_mode=ParseMode.HTML,
        reply_markup=_exhibition_mgmt_keyboard(),
    )
    return ExhibitionMgmtState.MENU


async def exhibitions_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    try:
        idx = int(update.message.text.strip()) - 1
    except ValueError:
        await _reply(update,
            "Введите номер из списка.", reply_markup=_exhibition_mgmt_keyboard()
        )
        return ExhibitionMgmtState.MENU
    removed = delete_exhibition(user_id, idx)
    if removed:
        await _reply(update,
            f"🗑 Выставка <b>{exhibition_display(removed)}</b> удалена.",
            parse_mode=ParseMode.HTML,
            reply_markup=_exhibition_mgmt_keyboard(),
        )
    else:
        await _reply(update,
            "Неверный номер.", reply_markup=_exhibition_mgmt_keyboard()
        )
    return ExhibitionMgmtState.MENU


# ─── Settings conversation ──────────────────────────────────────────────────

def _settings_keyboard() -> ReplyKeyboardMarkup:
    buttons = []
    for i, (key, label) in enumerate(SETTINGS_KEYS, 1):
        buttons.append([KeyboardButton(f"{i}. {label}")])
    buttons.append([KeyboardButton("✅ Готово")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    settings = load_settings()
    await _reply(update,
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
        await _reply(update,
            "✅ Настройки сохранены.",
            reply_markup=_main_keyboard(ctx),
        )
        return ConversationHandler.END

    for i, (key, label) in enumerate(SETTINGS_KEYS, 1):
        if text.startswith(f"{i}."):
            settings = load_settings()

            # debug_mode is a boolean toggle — flip immediately, no text input needed
            if key == "debug_mode":
                settings["debug_mode"] = not bool(settings.get("debug_mode", False))
                save_settings(settings)
                state = "ВКЛ 🟢" if settings["debug_mode"] else "ВЫКЛ 🔴"
                await _reply(update,
                    f"✅ <b>Debug trace</b>: {state}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=_settings_keyboard(),
                )
                await _reply(update,
                    format_settings(settings), parse_mode=ParseMode.HTML
                )
                return SettingsState.CHOOSING

            context.user_data["editing_key"] = key
            context.user_data["editing_label"] = label
            current = settings[key]
            await _reply(update,
                f"✏️ <b>{label}</b>\n\nТекущее значение: <code>{current}</code>\n\nВведите новое значение:",
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardRemove(),
            )
            return SettingsState.EDITING

    await _reply(update,"Выберите параметр из списка.", reply_markup=_settings_keyboard())
    return SettingsState.CHOOSING


async def settings_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key = context.user_data.get("editing_key")
    label = context.user_data.get("editing_label", key)

    try:
        value = float(update.message.text.replace(",", ".").strip())
    except ValueError:
        await _reply(update,
            "⚠️ Введите число (напр. <code>0.59</code> или <code>90.0</code>).",
            parse_mode=ParseMode.HTML,
        )
        return SettingsState.EDITING

    settings = load_settings()
    settings[key] = value
    save_settings(settings)

    await _reply(update,
        f"✅ <b>{label}</b> обновлено: <code>{value}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=_settings_keyboard(),
    )

    settings = load_settings()
    await _reply(update,
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
            ProductState.DATA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_data),
            ],
            ProductState.CATEGORY_PICK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_category_pick),
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

    factory_mgmt_conv = ConversationHandler(
        entry_points=[CommandHandler("factories", factories_menu)],
        states={
            FactoryMgmtState.MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, factories_menu_handle)
            ],
            FactoryMgmtState.ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, factories_add)
            ],
            FactoryMgmtState.DELETE_CHOOSE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, factories_delete)
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
                CommandHandler("skip", exhibition_custom_skip_year),
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibition_custom_year),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    exhibition_mgmt_conv = ConversationHandler(
        entry_points=[CommandHandler("exhibitions", exhibitions_menu)],
        states={
            ExhibitionMgmtState.MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibitions_menu_handle)
            ],
            ExhibitionMgmtState.ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibitions_add_name)
            ],
            ExhibitionMgmtState.ADD_YEAR: [
                CommandHandler("skip", exhibitions_add_year_skip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibitions_add_year),
            ],
            ExhibitionMgmtState.DELETE_CHOOSE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibitions_delete)
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

    # Populate the module-level registry so clear_history can reset FSM state.
    _conv_handlers.extend([
        product_conv,
        factory_conv,
        factory_mgmt_conv,
        exhibition_conv,
        exhibition_mgmt_conv,
        settings_conv,
    ])

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🗑 Очистить историю$"), clear_history))
    app.add_handler(MessageHandler(filters.Regex("^🧹 Очистить бот за 48 часов$"), clean_48h))
    app.add_handler(factory_mgmt_conv)
    app.add_handler(exhibition_mgmt_conv)
    app.add_handler(factory_conv)
    app.add_handler(exhibition_conv)
    app.add_handler(product_conv)
    app.add_handler(settings_conv)

    logger.info("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
