import logging
import os
import sys

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    TypeHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

from states import ProductState, EditPanelState, FactoryPanelState, ExhibitionPanelState, SettingsPanelState, AdminPanelState, CatalogState
from access import (
    is_allowed, is_owner, is_setup_mode,
    get_owner_id, set_owner,
    add_user, remove_user, list_users, list_users_with_roles, find_by_username,
)
from pricing import load_settings, save_settings, calculate_cost_per_unit
from formatters import format_product_card, format_card_from_db, format_settings
from context_store import (
    load_user_context, save_user_context, clear_user_context,
    get_factories, add_factory, delete_factory,
    get_exhibitions, add_exhibition, delete_exhibition, exhibition_display,
)
from products import (
    save_product, get_products, get_product, delete_product, update_product,
    get_active_products, count_active_products, soft_delete_product,
    add_attachment, delete_attachment, add_hero_media,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ─── Access gate (group=-1, runs before ALL other handlers) ──────────────────

# Commands exempt from access control — always reachable
_EXEMPT_COMMANDS = {"/whoami", "/setowner"}

async def access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Blocks any update from a non-allowed user.
    Raises ApplicationHandlerStop to prevent downstream handlers from running.
    Exempt: /whoami and /setowner (needed for bootstrap).
    """
    user = update.effective_user
    user_id = user.id if user else None

    # Let exempt commands through regardless of access
    msg = update.effective_message
    if msg and msg.text:
        cmd = msg.text.strip().split()[0].lower().split("@")[0]
        if cmd in _EXEMPT_COMMANDS:
            return

    if user_id is None or not is_allowed(user_id):
        if update.callback_query:
            try:
                await update.callback_query.answer("⛔ Доступ запрещён.", show_alert=True)
            except Exception:
                pass
        elif msg:
            await msg.reply_text("⛔ Доступ запрещён.")
        logger.info("ACCESS DENIED: user_id=%s", user_id)
        raise ApplicationHandlerStop


# ─── Access management commands ──────────────────────────────────────────────

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Always accessible — lets anyone discover their user_id for setup."""
    uid  = update.effective_user.id
    name = update.effective_user.full_name
    lines = [f"👤 <b>{name}</b>", f"🔑 ID: <code>{uid}</code>", ""]
    if is_setup_mode():
        lines += [
            "⚙️ Бот в режиме настройки (список пуст).",
            "Отправьте /setowner чтобы стать владельцем.",
        ]
    elif is_owner(uid):
        lines.append("👑 Вы владелец бота.")
    elif is_allowed(uid):
        lines.append("✅ Доступ разрешён.")
    else:
        lines.append("⛔ У вас нет доступа к этому боту.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_setowner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Claim ownership — only works when no owner is set yet."""
    uid = update.effective_user.id
    if get_owner_id() is not None:
        await update.message.reply_text("⛔ Владелец уже задан. Обратитесь к текущему владельцу.")
        return
    set_owner(uid)
    logger.info("Owner set: user_id=%d", uid)
    await update.message.reply_text(
        f"✅ Вы стали владельцем.\n"
        f"Ваш ID: <code>{uid}</code>\n\n"
        "Используйте кнопку «👥 Админ» для управления пользователями.",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(uid),
    )


async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: add a user to the allowlist."""
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ Только владелец может добавлять пользователей.")
        return
    if not context.args:
        await update.message.reply_text(
            "Использование: <code>/adduser 123456789</code>", parse_mode=ParseMode.HTML
        )
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Неверный ID — нужно целое число.")
        return
    if add_user(target):
        logger.info("User added: %d by owner %d", target, uid)
        await update.message.reply_text(
            f"✅ Пользователь <code>{target}</code> добавлен.", parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Пользователь <code>{target}</code> уже в списке.", parse_mode=ParseMode.HTML
        )


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: remove a user from the allowlist."""
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ Только владелец может удалять пользователей.")
        return
    if not context.args:
        await update.message.reply_text(
            "Использование: <code>/removeuser 123456789</code>", parse_mode=ParseMode.HTML
        )
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Неверный ID — нужно целое число.")
        return
    result = remove_user(target)
    if result == "ok":
        logger.info("User removed: %d by owner %d", target, uid)
        await update.message.reply_text(
            f"✅ Пользователь <code>{target}</code> удалён.", parse_mode=ParseMode.HTML
        )
    elif result == "is_owner":
        await update.message.reply_text("⛔ Нельзя удалить владельца.")
    else:
        await update.message.reply_text(
            f"⚠️ Пользователь <code>{target}</code> не найден.", parse_mode=ParseMode.HTML
        )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: list all allowed users."""
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ Только владелец может просматривать список.")
        return
    users = list_users()
    owner = get_owner_id()
    if not users:
        await update.message.reply_text("📋 Список пуст — бот в режиме настройки.")
        return
    lines = ["📋 <b>Разрешённые пользователи:</b>", ""]
    for u in users:
        crown = " 👑" if u == owner else ""
        lines.append(f"• <code>{u}</code>{crown}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ─── Reply helpers ───────────────────────────────────────────────────────────

async def _reply(update: Update, *args, **kwargs) -> "Message":
    return await update.message.reply_text(*args, **kwargs)


async def _reply_photo(update: Update, *args, **kwargs) -> "Message":
    return await update.message.reply_photo(*args, **kwargs)

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
    ("broker_fee_kzt", "Брокер P5, ₸"),
    ("terminal_fee_kzt", "Терминал Q5, ₸"),
    ("storage_fee_kzt", "Хранение R5, ₸"),
    ("forwarder_fee_kzt", "Экспедитор S5, ₸"),
    ("debug_mode", "Debug trace (вкл/выкл)"),
]


# ─── Helpers ────────────────────────────────────────────────────────────────

def _main_keyboard(user_id: int = None) -> ReplyKeyboardMarkup:
    count = count_active_products()
    rows = [
        [KeyboardButton("➕ Новый товар")],
        [KeyboardButton("🏭 Фабрика"), KeyboardButton("🎪 Выставка")],
        [KeyboardButton(f"📦 Каталог ({count})")],
        [KeyboardButton("⚙️ Настройки")],
    ]
    if user_id and is_owner(user_id):
        rows.append([KeyboardButton("👥 Админ")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


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

# ─── /start ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    await _reply(update,
        _main_menu_text(ctx),
        parse_mode=ParseMode.HTML,
        reply_markup=_main_keyboard(user_id),
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
            reply_markup=_main_keyboard(user_id),
        )
        return ConversationHandler.END

    if not ctx["active_factory"]:
        await _reply(update,
            "⚠️ Укажите <b>фабрику</b> перед добавлением товара.\n\n"
            "Нажмите «🏭 Фабрика» в меню.",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(user_id),
        )
        return ConversationHandler.END

    if not ctx["active_exhibition"]:
        await _reply(update,
            "⚠️ Укажите <b>выставку</b> перед добавлением товара.\n\n"
            "Нажмите «🎪 Выставка» в меню.",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(user_id),
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

    # Save to persistent catalog
    from datetime import datetime as _dt
    calc = calculate_cost_per_unit(data["price"], data["qty_40hq"], settings)
    edit_pid = data.pop("_edit_product_id", None)

    # Build hero_media from the optional creation-flow photo
    hero_media: list = []
    if data.get("photo_file_id"):
        hero_media = [{
            "type": "photo",
            "file_id": data["photo_file_id"],
            "timestamp": _dt.now().strftime("%Y-%m-%dT%H:%M"),
        }]

    product_record = {
        "sku":        data["article"],
        "category":   data["category"],
        "fob_usd":    data["price"],
        "qty_40hq":   data["qty_40hq"],
        "kz_price":   calc["G5"],
        "factory":    data["factory"],
        "exhibition": data["exhibition"],
        "note":       data.get("note", "—"),
        "hero_media": hero_media,
    }
    if edit_pid:
        update_product(edit_pid, product_record, updated_by=user_id)
        saved_id = edit_pid
    else:
        saved = save_product(product_record, created_by=user_id)
        saved_id = saved["id"]

    chat_id = update.effective_chat.id
    markup  = _card_markup(saved_id)

    # --- Send album header first (if any media) ---
    new_media_ids: list[int] = []
    if hero_media:
        group = []
        for m in hero_media[:10]:
            if m["type"] == "photo":
                group.append(InputMediaPhoto(media=m["file_id"]))
            else:
                group.append(InputMediaVideo(media=m["file_id"]))
        sent_group = await context.bot.send_media_group(chat_id=chat_id, media=group)
        new_media_ids = [msg.message_id for msg in sent_group]

    # --- Send text card body immediately after album (no reply-thread arrow) ---
    sent_text = await context.bot.send_message(
        chat_id=chat_id, text=card_text,
        parse_mode=ParseMode.HTML, reply_markup=markup,
    )

    update_product(saved_id, {
        "media_message_ids": new_media_ids,
        "text_message_id":   sent_text.message_id,
        "card_chat_id":      chat_id,
    }, updated_by=user_id)

    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Товар сохранён.",
        reply_markup=_main_keyboard(user_id),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    msg_id  = context.user_data.pop("_panel_msg_id",  None)
    chat_id = context.user_data.pop("_panel_chat_id", None)
    if msg_id and chat_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    else:
        await _reply(update, "❌ Ввод отменён.", reply_markup=_main_keyboard(user_id))
    return ConversationHandler.END


# ─── Panel helper ────────────────────────────────────────────────────────────

async def _panel_edit(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    msg_id  = context.user_data.get("_panel_msg_id")
    chat_id = context.user_data.get("_panel_chat_id")
    if msg_id and chat_id:
        try:
            await context.bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=msg_id,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception:
            pass


# ─── Factory panel ────────────────────────────────────────────────────────────

def _fp_text(user_id: int) -> str:
    ctx    = load_user_context(user_id)
    active = ctx["active_factory"] or "не задана"
    return f"🏭 <b>Фабрика</b>\n\nАктивная: <b>{active}</b>"


def _fp_keyboard(user_id: int) -> InlineKeyboardMarkup:
    ctx       = load_user_context(user_id)
    factories = get_factories()
    rows = []
    for i, f in enumerate(factories):
        mark = "✅ " if f == ctx["active_factory"] else ""
        rows.append([
            InlineKeyboardButton(f"{mark}{f}", callback_data=f"fp_sel:{i}"),
            InlineKeyboardButton("🗑", callback_data=f"fp_del:{i}"),
        ])
    rows.append([InlineKeyboardButton("➕ Добавить", callback_data="fp_add")])
    rows.append([InlineKeyboardButton("✖️ Закрыть",  callback_data="fp_done")])
    return InlineKeyboardMarkup(rows)


async def factory_panel_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sent = await update.message.reply_text(
        _fp_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_fp_keyboard(user_id)
    )
    context.user_data["_panel_msg_id"]  = sent.message_id
    context.user_data["_panel_chat_id"] = update.effective_chat.id
    return FactoryPanelState.IDLE


async def factory_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data    = query.data

    if data.startswith("fp_sel:"):
        idx = int(data.split(":")[1])
        factories = get_factories()
        if 0 <= idx < len(factories):
            ctx = load_user_context(user_id)
            save_user_context(user_id, factories[idx], ctx["active_exhibition"])
        await query.edit_message_text(
            _fp_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_fp_keyboard(user_id)
        )
        return FactoryPanelState.IDLE

    if data.startswith("fp_del:"):
        idx = int(data.split(":")[1])
        delete_factory(idx)
        await query.edit_message_text(
            _fp_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_fp_keyboard(user_id)
        )
        return FactoryPanelState.IDLE

    if data == "fp_add":
        await query.edit_message_text(
            "🏭 <b>Добавить фабрику</b>\n\nВведите название:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Отмена", callback_data="fp_cadd")]]
            ),
        )
        return FactoryPanelState.ADDING

    if data == "fp_cadd":
        await query.edit_message_text(
            _fp_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_fp_keyboard(user_id)
        )
        return FactoryPanelState.IDLE

    if data == "fp_done":
        ctx    = load_user_context(user_id)
        active = ctx["active_factory"] or "—"
        await query.edit_message_text(f"🏭 {active}")
        return ConversationHandler.END

    return FactoryPanelState.IDLE


async def factory_panel_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name    = update.message.text.strip()
    user_id = update.effective_user.id
    add_factory(name, added_by=user_id)
    ctx = load_user_context(user_id)
    save_user_context(user_id, name, ctx["active_exhibition"])
    await _panel_edit(context, _fp_text(user_id), _fp_keyboard(user_id))
    return FactoryPanelState.IDLE


# ─── Exhibition panel ─────────────────────────────────────────────────────────

def _ep_text(user_id: int) -> str:
    ctx    = load_user_context(user_id)
    active = ctx["active_exhibition"] or "не задана"
    return f"🎪 <b>Выставка</b>\n\nАктивная: <b>{active}</b>"


def _ep_keyboard(user_id: int) -> InlineKeyboardMarkup:
    ctx         = load_user_context(user_id)
    exhibitions = get_exhibitions()
    rows = []
    for i, ex in enumerate(exhibitions):
        disp = exhibition_display(ex)
        mark = "✅ " if disp == ctx["active_exhibition"] else ""
        rows.append([
            InlineKeyboardButton(f"{mark}{disp}", callback_data=f"ep_sel:{i}"),
            InlineKeyboardButton("🗑", callback_data=f"ep_del:{i}"),
        ])
    rows.append([InlineKeyboardButton("➕ Добавить", callback_data="ep_add")])
    rows.append([InlineKeyboardButton("✖️ Закрыть",  callback_data="ep_done")])
    return InlineKeyboardMarkup(rows)


async def exhibition_panel_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sent = await update.message.reply_text(
        _ep_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_ep_keyboard(user_id)
    )
    context.user_data["_panel_msg_id"]  = sent.message_id
    context.user_data["_panel_chat_id"] = update.effective_chat.id
    return ExhibitionPanelState.IDLE


async def exhibition_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data    = query.data

    if data.startswith("ep_sel:"):
        idx = int(data.split(":")[1])
        exhibitions = get_exhibitions()
        if 0 <= idx < len(exhibitions):
            disp = exhibition_display(exhibitions[idx])
            ctx  = load_user_context(user_id)
            save_user_context(user_id, ctx["active_factory"], disp)
        await query.edit_message_text(
            _ep_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_ep_keyboard(user_id)
        )
        return ExhibitionPanelState.IDLE

    if data.startswith("ep_del:"):
        idx = int(data.split(":")[1])
        delete_exhibition(idx)
        await query.edit_message_text(
            _ep_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_ep_keyboard(user_id)
        )
        return ExhibitionPanelState.IDLE

    if data == "ep_add":
        await query.edit_message_text(
            "🎪 <b>Добавить выставку</b>\n\nВведите название:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Отмена", callback_data="ep_cadd")]]
            ),
        )
        return ExhibitionPanelState.ADDING

    if data == "ep_cadd":
        await query.edit_message_text(
            _ep_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_ep_keyboard(user_id)
        )
        return ExhibitionPanelState.IDLE

    if data == "ep_skip_year":
        name = context.user_data.get("_new_ex_name", "")
        add_exhibition(name, None, added_by=user_id)
        disp = exhibition_display({"name": name, "year": None})
        ctx  = load_user_context(user_id)
        save_user_context(user_id, ctx["active_factory"], disp)
        await query.edit_message_text(
            _ep_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_ep_keyboard(user_id)
        )
        return ExhibitionPanelState.IDLE

    if data == "ep_done":
        ctx    = load_user_context(user_id)
        active = ctx["active_exhibition"] or "—"
        await query.edit_message_text(f"🎪 {active}")
        return ConversationHandler.END

    return ExhibitionPanelState.IDLE


async def exhibition_panel_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    context.user_data["_new_ex_name"] = name
    await _panel_edit(
        context,
        f"🎪 <b>Добавить выставку</b>\n\nНазвание: <b>{name}</b>\n\n"
        "Введите год (например: <code>2026</code>) или нажмите «Пропустить»:",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("Пропустить год", callback_data="ep_skip_year")],
            [InlineKeyboardButton("❌ Отмена",       callback_data="ep_cadd")],
        ]),
    )
    return ExhibitionPanelState.ADDING_YEAR


async def exhibition_panel_add_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    year    = update.message.text.strip() or None
    name    = context.user_data.get("_new_ex_name", "")
    user_id = update.effective_user.id
    add_exhibition(name, year, added_by=user_id)
    disp = exhibition_display({"name": name, "year": year})
    ctx  = load_user_context(user_id)
    save_user_context(user_id, ctx["active_factory"], disp)
    await _panel_edit(context, _ep_text(user_id), _ep_keyboard(user_id))
    return ExhibitionPanelState.IDLE


# ─── Settings panel ───────────────────────────────────────────────────────────

def _sp_keyboard(settings: dict) -> InlineKeyboardMarkup:
    rows = []
    for key, label in SETTINGS_KEYS:
        if key == "debug_mode":
            state = "🟢 ВКЛ" if settings.get(key) else "🔴 ВЫКЛ"
            rows.append([InlineKeyboardButton(f"Debug: {state}", callback_data=f"sp_toggle:{key}")])
        else:
            val = settings.get(key, "")
            rows.append([InlineKeyboardButton(f"✏️ {label}: {val}", callback_data=f"sp_edit:{key}")])
    rows.append([InlineKeyboardButton("✖️ Закрыть", callback_data="sp_done")])
    return InlineKeyboardMarkup(rows)


async def settings_panel_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    settings = load_settings()
    sent = await update.message.reply_text(
        format_settings(settings), parse_mode=ParseMode.HTML, reply_markup=_sp_keyboard(settings)
    )
    context.user_data["_panel_msg_id"]  = sent.message_id
    context.user_data["_panel_chat_id"] = update.effective_chat.id
    return SettingsPanelState.IDLE


async def settings_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data.startswith("sp_edit:"):
        key      = data.split(":", 1)[1]
        settings = load_settings()
        label    = next((l for k, l in SETTINGS_KEYS if k == key), key)
        current  = settings.get(key, "")
        context.user_data["_editing_key"]   = key
        context.user_data["_editing_label"] = label
        await query.edit_message_text(
            f"⚙️ <b>{label}</b>\n\nТекущее: <code>{current}</code>\n\nВведите новое значение:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Отмена", callback_data="sp_cancel")]]
            ),
        )
        return SettingsPanelState.EDITING

    if data.startswith("sp_toggle:"):
        key      = data.split(":", 1)[1]
        settings = load_settings()
        settings[key] = not bool(settings.get(key, False))
        save_settings(settings)
        await query.edit_message_text(
            format_settings(settings), parse_mode=ParseMode.HTML, reply_markup=_sp_keyboard(settings)
        )
        return SettingsPanelState.IDLE

    if data == "sp_cancel":
        settings = load_settings()
        await query.edit_message_text(
            format_settings(settings), parse_mode=ParseMode.HTML, reply_markup=_sp_keyboard(settings)
        )
        return SettingsPanelState.IDLE

    if data == "sp_done":
        await query.edit_message_text("⚙️ Настройки сохранены.")
        return ConversationHandler.END

    return SettingsPanelState.IDLE


async def settings_panel_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key   = context.user_data.get("_editing_key")
    label = context.user_data.get("_editing_label", key)
    try:
        value = float(update.message.text.replace(",", ".").strip())
    except ValueError:
        await _panel_edit(
            context,
            f"⚙️ <b>{label}</b>\n\n⚠️ Введите число (напр. <code>0.59</code>)\n\nВведите новое значение:",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="sp_cancel")]]),
        )
        return SettingsPanelState.EDITING
    settings = load_settings()
    settings[key] = value
    save_settings(settings)
    await _panel_edit(context, format_settings(settings), _sp_keyboard(settings))
    return SettingsPanelState.IDLE


# ─── Edit panel ──────────────────────────────────────────────────────────────

def _xp_text(p: dict) -> str:
    from datetime import datetime
    dt  = datetime.fromtimestamp(p.get("created_at", 0)).strftime("%d.%m.%Y")
    att = p.get("attachments", [])
    lines = [
        f"✏️ <b>{p.get('sku', '—')}</b> — {p.get('category', '—')}",
        f"🏭 {p.get('factory', '—')}  ·  🎪 {p.get('exhibition', '—')}",
        f"💵 FOB: <b>${p.get('fob_usd', 0):,.2f}</b>  ·  {p.get('qty_40hq', 0):,} шт / 40HQ",
        f"💴 KZ цена: <b>₸{int(p.get('kz_price', 0)):,}</b>".replace(",", " "),
    ]
    note = p.get("note") or "—"
    if note and note != "—":
        lines.append(f"📝 {note}")
    lines.append(f"📅 {dt}")
    if att:
        last  = att[-1]
        emoji = {"photo": "📸", "video": "📹", "note": "📝"}.get(last["type"], "📎")
        lines.append(f"📎 Вложений: {len(att)} · {emoji} последнее")
    if p.get("updated_at"):
        upd = datetime.fromtimestamp(p["updated_at"]).strftime("%d.%m.%Y %H:%M")
        lines.append(f"<i>Изменён: {upd}</i>")
    return "\n".join(lines)


def _xp_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📸 Фото",    callback_data=f"xp_photo:{pid}"),
            InlineKeyboardButton("📹 Видео",   callback_data=f"xp_video:{pid}"),
            InlineKeyboardButton("📝 Заметка", callback_data=f"xp_note:{pid}"),
        ],
        [
            InlineKeyboardButton("🏷 Артикул",   callback_data=f"xp_sku:{pid}"),
            InlineKeyboardButton("📦 Категория", callback_data=f"xp_cat:{pid}"),
            InlineKeyboardButton("💰 Цена FOB",  callback_data=f"xp_price:{pid}"),
        ],
        [
            InlineKeyboardButton("🏭 Завод", callback_data=f"xp_factory:{pid}"),
        ],
        [InlineKeyboardButton("✖️ Закрыть", callback_data=f"xp_done:{pid}")],
    ])


def _card_markup(pid: str) -> InlineKeyboardMarkup:
    """Single 'Edit' button for a finished product card."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{pid}")]]
    )


async def _xp_edit(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit the text-layer card message in-place via editMessageText."""
    msg_id  = context.user_data.get("_xp_msg_id")
    chat_id = context.user_data.get("_xp_chat_id")
    if not msg_id or not chat_id:
        return
    try:
        await context.bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.warning("_xp_edit failed: %s", e)


async def _rebuild_card(
    context: ContextTypes.DEFAULT_TYPE,
    pid: str,
    chat_id: int,
    user_id: int,
) -> None:
    """
    Full Shopify-card rebuild:
      1. Delete existing album messages (media_message_ids)
      2. Delete existing text card (text_message_id)
      3. Send fresh sendMediaGroup (album header)
      4. Send fresh text card (body)
      5. Store new IDs in DB and update session pointers.
    """
    p = get_product(pid)
    if not p:
        return
    settings   = load_settings()
    card_txt   = format_card_from_db(p, settings)
    markup     = _card_markup(pid)
    hero_media = p.get("hero_media", [])

    # --- 1. Delete old album messages ---
    for mid in p.get("media_message_ids", []):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass

    # --- 2. Delete old text card ---
    old_text_mid = p.get("text_message_id") or context.user_data.get("_xp_msg_id")
    if old_text_mid:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_text_mid)
        except Exception:
            pass

    # --- 3. Send fresh album header (if any media) ---
    new_media_ids: list[int] = []
    if hero_media:
        group = []
        for m in hero_media[:10]:
            if m["type"] == "photo":
                group.append(InputMediaPhoto(media=m["file_id"]))
            else:
                group.append(InputMediaVideo(media=m["file_id"]))
        sent_group = await context.bot.send_media_group(chat_id=chat_id, media=group)
        new_media_ids = [msg.message_id for msg in sent_group]

    # --- 4. Send fresh text card (body) ---
    sent_text = await context.bot.send_message(
        chat_id=chat_id, text=card_txt,
        parse_mode=ParseMode.HTML, reply_markup=markup,
    )

    # --- 5. Persist new IDs ---
    update_product(pid, {
        "media_message_ids": new_media_ids,
        "text_message_id":   sent_text.message_id,
        "card_chat_id":      chat_id,
    }, updated_by=user_id)
    context.user_data["_xp_msg_id"]  = sent_text.message_id
    context.user_data["_xp_chat_id"] = chat_id


async def _restore_card(context: ContextTypes.DEFAULT_TYPE, pid: str) -> None:
    """Update the text-layer body in-place (editMessageText). Does NOT touch the album."""
    p = get_product(pid)
    if not p:
        return
    settings = load_settings()
    await _xp_edit(context, format_card_from_db(p, settings), _card_markup(pid))


async def edit_by_id_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: inline button 'edit_<product_id>' on the text-layer card."""
    query = update.callback_query
    await query.answer()
    pid = query.data[len("edit_"):]
    p   = get_product(pid)
    if not p:
        await query.answer("⚠️ Товар не найден.", show_alert=True)
        return ConversationHandler.END
    context.user_data["_xp_pid"]     = pid
    context.user_data["_xp_msg_id"]  = query.message.message_id
    context.user_data["_xp_chat_id"] = query.message.chat_id
    try:
        await query.edit_message_text(
            _xp_text(p), parse_mode=ParseMode.HTML, reply_markup=_xp_keyboard(pid)
        )
    except Exception as e:
        logger.warning("edit_by_id_cb open failed: %s", e)
        return ConversationHandler.END
    return EditPanelState.IDLE



async def edit_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data    = query.data

    pid = data.split(":", 1)[1] if ":" in data else context.user_data.get("_xp_pid", "")
    context.user_data["_xp_pid"] = pid

    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отмена", callback_data=f"xp_cancel:{pid}")]]
    )

    if data.startswith("xp_photo:"):
        await query.edit_message_text(
            "📸 <b>Добавить фото</b>\n\nОтправьте фото товара:",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return EditPanelState.ADDING_PHOTO

    if data.startswith("xp_video:"):
        await query.edit_message_text(
            "📹 <b>Добавить видео</b>\n\nОтправьте видео:",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return EditPanelState.ADDING_VIDEO

    if data.startswith("xp_note:"):
        await query.edit_message_text(
            "📝 <b>Добавить заметку</b>\n\nВведите текст:",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return EditPanelState.ADDING_NOTE

    if data.startswith("xp_sku:"):
        p   = get_product(pid)
        sku = p.get("sku", "—") if p else "—"
        await query.edit_message_text(
            f"🏷 <b>Артикул (SKU)</b>\n\nТекущий: <b>{sku}</b>\n\nВведите новый:",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return EditPanelState.EDITING_SKU

    if data.startswith("xp_cat:"):
        p   = get_product(pid)
        cat = p.get("category", "—") if p else "—"
        await query.edit_message_text(
            f"📦 <b>Категория</b>\n\nТекущая: <b>{cat}</b>\n\nВведите новую:",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return EditPanelState.EDITING_CAT

    if data.startswith("xp_price:"):
        p = get_product(pid)
        if p:
            await query.edit_message_text(
                f"💰 <b>Цена FOB</b>\n\n"
                f"Текущая: <b>${p.get('fob_usd', 0):,.2f}</b>  ·  {p.get('qty_40hq', 0):,} шт\n\n"
                "Введите новую цену FOB в USD:",
                parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
            )
        return EditPanelState.EDITING_PRICE

    if data.startswith("xp_factory:"):
        p        = get_product(pid)
        current  = p.get("factory", "—") if p else "—"
        factories = get_factories()
        rows = []
        for i, f in enumerate(factories):
            mark = "✅ " if f == current else ""
            rows.append([InlineKeyboardButton(f"{mark}{f}", callback_data=f"xp_fsel:{pid}:{i}")])
        rows.append([InlineKeyboardButton("✏️ Ввести вручную", callback_data=f"xp_finput:{pid}")])
        rows.append([InlineKeyboardButton("❌ Отмена",         callback_data=f"xp_cancel:{pid}")])
        await query.edit_message_text(
            f"🏭 <b>Завод</b>\n\nТекущий: <b>{current}</b>\n\nВыберите или введите:",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows),
        )
        return EditPanelState.EDITING_FACT

    if data.startswith("xp_fsel:"):
        parts   = data.split(":")
        idx     = int(parts[2])
        factories = get_factories()
        if 0 <= idx < len(factories):
            update_product(pid, {"factory": factories[idx]}, updated_by=user_id)
        await _restore_card(context, pid)
        return ConversationHandler.END

    if data.startswith("xp_finput:"):
        await query.edit_message_text(
            "🏭 <b>Завод</b>\n\nВведите название завода:",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return EditPanelState.EDITING_FACT

    if data.startswith("xp_exh:"):
        p           = get_product(pid)
        current     = p.get("exhibition", "—") if p else "—"
        exhibitions = get_exhibitions()
        rows = []
        for i, ex in enumerate(exhibitions):
            disp = exhibition_display(ex)
            mark = "✅ " if disp == current else ""
            rows.append([InlineKeyboardButton(f"{mark}{disp}", callback_data=f"xp_esel:{pid}:{i}")])
        rows.append([InlineKeyboardButton("✏️ Ввести вручную", callback_data=f"xp_einput:{pid}")])
        rows.append([InlineKeyboardButton("❌ Отмена",          callback_data=f"xp_cancel:{pid}")])
        await query.edit_message_text(
            f"🎪 <b>Выставка</b>\n\nТекущая: <b>{current}</b>\n\nВыберите или введите:",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows),
        )
        return EditPanelState.EDITING_EXH

    if data.startswith("xp_esel:"):
        parts       = data.split(":")
        idx         = int(parts[2])
        exhibitions = get_exhibitions()
        if 0 <= idx < len(exhibitions):
            update_product(pid, {"exhibition": exhibition_display(exhibitions[idx])}, updated_by=user_id)
        await _restore_card(context, pid)
        return ConversationHandler.END

    if data.startswith("xp_einput:"):
        await query.edit_message_text(
            "🎪 <b>Выставка</b>\n\nВведите название выставки:",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return EditPanelState.EDITING_EXH

    if data.startswith("xp_cancel:"):
        p = get_product(pid)
        if p:
            await query.edit_message_text(_xp_text(p), parse_mode=ParseMode.HTML, reply_markup=_xp_keyboard(pid))
        return EditPanelState.IDLE

    if data.startswith("xp_done:"):
        await _restore_card(context, pid)
        return ConversationHandler.END

    return EditPanelState.IDLE


async def _recv_media_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    media_type: str,
    file_id: str,
    stay_state: int,
) -> int:
    """
    Add one photo or video to the product's hero_media gallery, then
    do a full Shopify-card rebuild: delete old album + text → resend album → resend text.
    No separate floating messages are ever sent.
    """
    from datetime import datetime
    pid     = context.user_data.get("_xp_pid", "")
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    ts      = datetime.now().strftime("%Y-%m-%dT%H:%M")

    add_hero_media(pid, {"type": media_type, "file_id": file_id, "timestamp": ts})
    await _rebuild_card(context, pid, chat_id, user_id)
    return ConversationHandler.END


async def edit_panel_recv_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("⚠️ Отправьте фото.")
        return EditPanelState.ADDING_PHOTO
    return await _recv_media_item(
        update, context,
        media_type="photo",
        file_id=update.message.photo[-1].file_id,
        stay_state=EditPanelState.ADDING_PHOTO,
    )


async def edit_panel_recv_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.video:
        await update.message.reply_text("⚠️ Отправьте видео.")
        return EditPanelState.ADDING_VIDEO
    return await _recv_media_item(
        update, context,
        media_type="video",
        file_id=update.message.video.file_id,
        stay_state=EditPanelState.ADDING_VIDEO,
    )


async def edit_panel_recv_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.text:
        await update.message.reply_text("⚠️ Введите текст.")
        return EditPanelState.ADDING_NOTE
    pid     = context.user_data.get("_xp_pid", "")
    user_id = update.effective_user.id
    update_product(pid, {"note": update.message.text.strip()}, updated_by=user_id)
    await _restore_card(context, pid)
    return ConversationHandler.END


async def edit_panel_recv_sku(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.text:
        await update.message.reply_text("⚠️ Введите артикул.")
        return EditPanelState.EDITING_SKU
    pid     = context.user_data.get("_xp_pid", "")
    user_id = update.effective_user.id
    update_product(pid, {"sku": update.message.text.strip()}, updated_by=user_id)
    await _restore_card(context, pid)
    return ConversationHandler.END


async def edit_panel_recv_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid     = context.user_data.get("_xp_pid", "")
    user_id = update.effective_user.id
    cat     = update.message.text.strip()
    update_product(pid, {"category": cat}, updated_by=user_id)
    await _restore_card(context, pid)
    return ConversationHandler.END


async def edit_panel_recv_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid     = context.user_data.get("_xp_pid", "")
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    try:
        fob_usd = float(text.replace(",", ".").replace(" ", ""))
        if fob_usd <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введите число больше 0, например: <code>85.50</code>",
            parse_mode=ParseMode.HTML,
        )
        return EditPanelState.EDITING_PRICE
    p = get_product(pid)
    if p:
        settings = load_settings()
        calc     = calculate_cost_per_unit(fob_usd, p.get("qty_40hq", 1), settings)
        update_product(pid, {"fob_usd": fob_usd, "kz_price": calc["G5"]}, updated_by=user_id)
    await _restore_card(context, pid)
    return ConversationHandler.END


async def edit_panel_recv_factory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid     = context.user_data.get("_xp_pid", "")
    user_id = update.effective_user.id
    factory = update.message.text.strip()
    update_product(pid, {"factory": factory}, updated_by=user_id)
    await _restore_card(context, pid)
    return ConversationHandler.END


async def edit_panel_recv_exh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid     = context.user_data.get("_xp_pid", "")
    user_id = update.effective_user.id
    exh     = update.message.text.strip()
    update_product(pid, {"exhibition": exh}, updated_by=user_id)
    await _restore_card(context, pid)
    return ConversationHandler.END


# ─── Catalog ──────────────────────────────────────────────────────────────────

CATALOG_PAGE_SIZE = 10


def _catalog_list_text(page: int, products: list) -> str:
    total   = len(products)
    pages   = max(1, (total + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
    start   = page * CATALOG_PAGE_SIZE
    end     = min(start + CATALOG_PAGE_SIZE, total)
    return (
        f"📦 <b>Каталог</b> — {total} позиций\n"
        f"Страница {page + 1}/{pages}\n\n"
        "Выберите товар для просмотра:"
    )


def _catalog_list_keyboard(page: int, products: list) -> InlineKeyboardMarkup:
    total  = len(products)
    pages  = max(1, (total + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
    start  = page * CATALOG_PAGE_SIZE
    end    = min(start + CATALOG_PAGE_SIZE, total)
    rows   = []

    for p in products[start:end]:
        factory = p.get("factory", "—")[:12]
        sku     = p.get("sku") or p.get("article") or "—"
        fob     = p.get("fob_usd", 0)
        label   = f"{factory} | #{sku} | ${fob:.2f}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cat_p_{p['id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"cat_pg_{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="cat_noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"cat_pg_{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 Закрыть", callback_data="cat_close")])
    return InlineKeyboardMarkup(rows)


def _catalog_detail_text(p: dict, settings: dict) -> str:
    from datetime import datetime
    factory  = p.get("factory", "—")
    exh      = p.get("exhibition", "—")
    category = p.get("category", "—")
    sku      = p.get("sku") or p.get("article") or "—"
    fob      = p.get("fob_usd", 0)
    qty      = p.get("qty_40hq", 0)
    note     = p.get("note", "")
    rate     = settings.get("exchange_rate", 1)
    created  = p.get("created_at")
    date_str = datetime.fromtimestamp(created).strftime("%d.%m.%Y") if created else "—"

    lines = [
        f"🏭 <b>{factory}</b>  🎪 {exh}",
        f"📋 {category}  🆔 #{sku}",
        f"💵 FOB: <b>${fob:.2f}</b>  ({fob * rate:,.0f}₸)",
        f"📦 40HQ: <b>{qty:,} шт</b>",
    ]
    if note:
        lines.append(f"📝 {note}")
    lines.append(f"\n🗓 Добавлено: {date_str}")
    return "\n".join(lines)


def _catalog_detail_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Просмотр карточки", callback_data=f"cat_view_{pid}")],
        [InlineKeyboardButton("✏️ Редактировать",     callback_data=f"edit_{pid}"),
         InlineKeyboardButton("🗑 Удалить",           callback_data=f"cat_del_{pid}")],
        [InlineKeyboardButton("🔙 Назад к списку",    callback_data="cat_back")],
    ])


def _catalog_confirm_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, удалить",  callback_data=f"cat_delok_{pid}"),
         InlineKeyboardButton("❌ Отмена",       callback_data=f"cat_p_{pid}")],
    ])


async def catalog_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    products = get_active_products()
    context.user_data["cat_page"] = 0
    page = 0
    msg = await _reply(
        update,
        _catalog_list_text(page, products),
        parse_mode=ParseMode.HTML,
        reply_markup=_catalog_list_keyboard(page, products),
    )
    context.user_data["cat_msg_id"]  = msg.message_id
    context.user_data["cat_chat_id"] = update.effective_chat.id
    return CatalogState.LIST


async def catalog_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query    = update.callback_query
    await query.answer()
    uid      = update.effective_user.id
    data     = query.data
    settings = load_settings()

    # ── Pagination ───────────────────────────────────────────────────────
    if data.startswith("cat_pg_"):
        page     = int(data.split("_")[-1])
        products = get_active_products()
        context.user_data["cat_page"] = page
        await query.edit_message_text(
            _catalog_list_text(page, products),
            parse_mode=ParseMode.HTML,
            reply_markup=_catalog_list_keyboard(page, products),
        )
        return CatalogState.LIST

    if data == "cat_noop":
        return CatalogState.LIST

    # ── Open product detail ───────────────────────────────────────────────
    if data.startswith("cat_p_"):
        pid = data[len("cat_p_"):]
        p   = get_product(pid)
        if not p or p.get("status") == "deleted":
            products = get_active_products()
            page     = context.user_data.get("cat_page", 0)
            await query.edit_message_text(
                "⚠️ Товар не найден.\n\n" + _catalog_list_text(page, products),
                parse_mode=ParseMode.HTML,
                reply_markup=_catalog_list_keyboard(page, products),
            )
            return CatalogState.LIST
        context.user_data["cat_detail_pid"] = pid
        await query.edit_message_text(
            _catalog_detail_text(p, settings),
            parse_mode=ParseMode.HTML,
            reply_markup=_catalog_detail_keyboard(pid),
        )
        return CatalogState.DETAIL

    # ── Back to list ─────────────────────────────────────────────────────
    if data == "cat_back":
        products = get_active_products()
        page     = context.user_data.get("cat_page", 0)
        await query.edit_message_text(
            _catalog_list_text(page, products),
            parse_mode=ParseMode.HTML,
            reply_markup=_catalog_list_keyboard(page, products),
        )
        return CatalogState.LIST

    # ── View card inline ─────────────────────────────────────────────────
    if data.startswith("cat_view_"):
        pid = data[len("cat_view_"):]
        p   = get_product(pid)
        if not p:
            return CatalogState.DETAIL
        card_text = format_card_from_db(p, settings)
        back_kb   = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад",           callback_data=f"cat_p_{pid}")],
            [InlineKeyboardButton("✏️ Редактировать",   callback_data=f"edit_{pid}")],
        ])
        await query.edit_message_text(
            card_text, parse_mode=ParseMode.HTML, reply_markup=back_kb,
        )
        return CatalogState.DETAIL

    # ── Delete prompt ────────────────────────────────────────────────────
    if data.startswith("cat_del_"):
        pid = data[len("cat_del_"):]
        p   = get_product(pid)
        if not p:
            return CatalogState.DETAIL
        sku = p.get("sku") or p.get("article") or pid[:8]
        await query.edit_message_text(
            f"🗑 <b>Удалить товар?</b>\n\n"
            f"<b>#{sku}</b> — {p.get('factory', '—')}\n\n"
            "Товар будет скрыт из каталога. Данные сохранятся.",
            parse_mode=ParseMode.HTML,
            reply_markup=_catalog_confirm_keyboard(pid),
        )
        return CatalogState.DELETE_CONFIRM

    # ── Confirm delete ───────────────────────────────────────────────────
    if data.startswith("cat_delok_"):
        pid = data[len("cat_delok_"):]
        soft_delete_product(pid, uid)
        logger.info("Catalog: product %s soft-deleted by %d", pid, uid)
        products = get_active_products()
        page     = context.user_data.get("cat_page", 0)
        # Clamp page if last product on page was deleted
        max_page = max(0, (len(products) - 1) // CATALOG_PAGE_SIZE)
        page     = min(page, max_page)
        context.user_data["cat_page"] = page
        await query.edit_message_text(
            "✅ Товар удалён из каталога.\n\n" + _catalog_list_text(page, products),
            parse_mode=ParseMode.HTML,
            reply_markup=_catalog_list_keyboard(page, products),
        )
        return CatalogState.LIST

    # ── Close ────────────────────────────────────────────────────────────
    if data == "cat_close":
        try:
            await query.delete_message()
        except Exception:
            await query.edit_message_reply_markup(reply_markup=None)
        return ConversationHandler.END

    return CatalogState.LIST


# ─── Admin panel ─────────────────────────────────────────────────────────────

def _ap_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Список пользователей", callback_data="ap_list")],
        [InlineKeyboardButton("➕ Добавить",             callback_data="ap_add"),
         InlineKeyboardButton("❌ Удалить",              callback_data="ap_remove")],
        [InlineKeyboardButton("📊 Статус системы",       callback_data="ap_status")],
        [InlineKeyboardButton("🔙 Закрыть",              callback_data="ap_close")],
    ])


def _ap_text() -> str:
    users = list_users_with_roles()
    count = len(users)
    owner = get_owner_id()
    return (
        f"👥 <b>Панель администратора</b>\n\n"
        f"Пользователей: <b>{count}</b>\n"
        f"Владелец: <code>{owner}</code>\n\n"
        "Выберите действие:"
    )


async def admin_panel_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if not is_owner(uid):
        await _reply(update, "⛔ Только владелец имеет доступ к панели.")
        return ConversationHandler.END
    msg = await _reply(update, _ap_text(), parse_mode=ParseMode.HTML, reply_markup=_ap_keyboard())
    context.user_data["_ap_msg_id"]  = msg.message_id
    context.user_data["_ap_chat_id"] = update.effective_chat.id
    return AdminPanelState.IDLE


async def admin_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    uid     = update.effective_user.id
    data    = query.data
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отмена", callback_data="ap_back")]]
    )

    if data == "ap_list":
        users = list_users_with_roles()
        owner = get_owner_id()
        if not users:
            text = "📋 Список пуст — бот в режиме настройки."
        else:
            lines = ["📋 <b>Разрешённые пользователи:</b>", ""]
            for u in users:
                crown = " 👑" if u["id"] == owner else ""
                role  = u.get("role", "manager")
                uname = f" @{u['username'].lstrip('@')}" if u.get("username") else ""
                lines.append(f"• <code>{u['id']}</code>{uname} — {role}{crown}")
            text = "\n".join(lines)
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Назад", callback_data="ap_back")]]
            ),
        )
        return AdminPanelState.IDLE

    if data == "ap_add":
        await query.edit_message_text(
            "➕ <b>Добавить пользователя</b>\n\n"
            "Укажите пользователя одним из способов:\n\n"
            "• <b>Переслать</b> любое сообщение от него сюда\n"
            "• Написать <b>@username</b> (если он уже есть в базе)\n"
            "• Написать числовой <b>Telegram ID</b>",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return AdminPanelState.ADDING_USER

    if data == "ap_remove":
        users = list_users_with_roles()
        owner = get_owner_id()
        removable = [u for u in users if u["id"] != owner]
        if not removable:
            await query.edit_message_text(
                "📋 Нет пользователей для удаления.", reply_markup=_ap_keyboard()
            )
            return AdminPanelState.IDLE
        lines = ["❌ <b>Удалить пользователя</b>\n\nОтправьте ID для удаления:\n"]
        for u in removable:
            uname = f" @{u['username'].lstrip('@')}" if u.get("username") else ""
            lines.append(f"• <code>{u['id']}</code>{uname} — {u.get('role','manager')}")
        await query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return AdminPanelState.REMOVING_USER

    if data == "ap_status":
        settings = load_settings()
        products = get_products()
        n_products = len(products) if products else 0
        owner = get_owner_id()
        n_users = len(list_users_with_roles())
        text = (
            "📊 <b>Статус системы</b>\n\n"
            f"👥 Пользователей: <b>{n_users}</b>\n"
            f"👑 Владелец: <code>{owner}</code>\n"
            f"📦 Товаров в каталоге: <b>{n_products}</b>\n\n"
            f"⚙️ <b>Параметры цен:</b>\n"
            f"• Курс USD/KZT: <b>{settings.get('exchange_rate', '—')}</b>\n"
            f"• Маржа: <b>{int(settings.get('margin', 0)*100)}%</b>\n"
            f"• НДС: <b>×{settings.get('vat', '—')}</b>"
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Назад", callback_data="ap_back")]]
            ),
        )
        return AdminPanelState.IDLE

    if data in ("ap_back", "ap_close"):
        if data == "ap_close":
            try:
                await query.delete_message()
            except Exception:
                await query.edit_message_text(_ap_text(), parse_mode=ParseMode.HTML, reply_markup=_ap_keyboard())
            return ConversationHandler.END
        await query.edit_message_text(_ap_text(), parse_mode=ParseMode.HTML, reply_markup=_ap_keyboard())
        return AdminPanelState.IDLE

    return AdminPanelState.IDLE


async def admin_panel_recv_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid     = update.effective_user.id
    msg     = update.message
    target_id: int | None   = None
    target_username: str | None = None

    # ── 1. Forwarded message ──────────────────────────────────────────────
    if msg.forward_from:
        target_id       = msg.forward_from.id
        target_username = msg.forward_from.username
        source_label    = f"переслано от @{target_username}" if target_username else f"переслано (ID {target_id})"

    # ── 2. Text input ─────────────────────────────────────────────────────
    elif msg.text:
        raw = msg.text.strip()

        # @username lookup in internal DB
        if raw.startswith("@") or (not raw.lstrip("-").isdigit()):
            # treat as username
            uname = raw.lstrip("@")
            found = find_by_username(uname)
            if found:
                target_id       = found["id"]
                target_username = found.get("username")
                source_label    = f"найден @{uname} → ID {target_id}"
            else:
                await msg.reply_text(
                    f"⚠️ Пользователь <code>@{uname}</code> не найден в базе.\n\n"
                    "Попросите его переслать вам любое своё сообщение и перешлите его сюда.",
                    parse_mode=ParseMode.HTML,
                )
                return AdminPanelState.ADDING_USER

        else:
            # Numeric ID
            try:
                target_id    = int(raw)
                source_label = f"ID {target_id}"
            except ValueError:
                await msg.reply_text("⚠️ Не удалось распознать. Введите числовой ID или @username:")
                return AdminPanelState.ADDING_USER

    else:
        # Non-text, non-forward message (sticker, etc.)
        await msg.reply_text(
            "⚠️ Не удалось определить пользователя.\n\n"
            "Перешлите его сообщение, напишите @username или числовой ID."
        )
        return AdminPanelState.ADDING_USER

    # ── Commit ────────────────────────────────────────────────────────────
    added = add_user(target_id, username=target_username)
    if added:
        logger.info("Admin panel: user %d (%s) added by owner %d", target_id, target_username, uid)
        uname_str = f" (@{target_username.lstrip('@')})" if target_username else ""
        await msg.reply_text(
            f"✅ Пользователь <code>{target_id}</code>{uname_str} добавлен.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await msg.reply_text(
            f"ℹ️ Пользователь <code>{target_id}</code> уже есть в списке.",
            parse_mode=ParseMode.HTML,
        )

    # Refresh panel inline message
    ap_msg_id  = context.user_data.get("_ap_msg_id")
    ap_chat_id = context.user_data.get("_ap_chat_id")
    if ap_msg_id and ap_chat_id:
        try:
            await context.bot.edit_message_text(
                _ap_text(), chat_id=ap_chat_id, message_id=ap_msg_id,
                parse_mode=ParseMode.HTML, reply_markup=_ap_keyboard(),
            )
        except Exception:
            pass
    return ConversationHandler.END


async def admin_panel_recv_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid  = update.effective_user.id
    text = update.message.text.strip()
    try:
        target = int(text)
    except ValueError:
        await update.message.reply_text("⚠️ Неверный ID — введите целое число:")
        return AdminPanelState.REMOVING_USER

    result = remove_user(target)
    if result == "ok":
        logger.info("Admin panel: user %d removed by owner %d", target, uid)
        await update.message.reply_text(
            f"✅ Пользователь <code>{target}</code> удалён.", parse_mode=ParseMode.HTML
        )
    elif result == "is_owner":
        await update.message.reply_text("⛔ Нельзя удалить владельца.")
    else:
        await update.message.reply_text(
            f"⚠️ Пользователь <code>{target}</code> не найден.", parse_mode=ParseMode.HTML
        )
    # Refresh panel
    msg_id  = context.user_data.get("_ap_msg_id")
    chat_id = context.user_data.get("_ap_chat_id")
    if msg_id and chat_id:
        try:
            await context.bot.edit_message_text(
                _ap_text(), chat_id=chat_id, message_id=msg_id,
                parse_mode=ParseMode.HTML, reply_markup=_ap_keyboard(),
            )
        except Exception:
            pass
    return ConversationHandler.END


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

    edit_panel_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_by_id_cb, pattern=r"^edit_"),
        ],
        states={
            EditPanelState.IDLE: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_"),
            ],
            EditPanelState.ADDING_PHOTO: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_cancel:"),
                MessageHandler(filters.PHOTO, edit_panel_recv_photo),
            ],
            EditPanelState.ADDING_VIDEO: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_cancel:"),
                MessageHandler(filters.VIDEO, edit_panel_recv_video),
            ],
            EditPanelState.ADDING_NOTE: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_cancel:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_panel_recv_note),
            ],
            EditPanelState.EDITING_CAT: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_cancel:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_panel_recv_cat),
            ],
            EditPanelState.EDITING_PRICE: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_cancel:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_panel_recv_price),
            ],
            EditPanelState.EDITING_FACT: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_panel_recv_factory),
            ],
            EditPanelState.EDITING_EXH: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_panel_recv_exh),
            ],
            EditPanelState.EDITING_SKU: [
                CallbackQueryHandler(edit_panel_cb, pattern=r"^xp_cancel:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_panel_recv_sku),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    factory_panel_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🏭 Фабрика$"), factory_panel_open),
            CommandHandler("factory",   factory_panel_open),
            CommandHandler("factories", factory_panel_open),
        ],
        states={
            FactoryPanelState.IDLE: [
                CallbackQueryHandler(factory_panel_cb, pattern=r"^fp_"),
            ],
            FactoryPanelState.ADDING: [
                CallbackQueryHandler(factory_panel_cb, pattern=r"^fp_cadd$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, factory_panel_add_name),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    exhibition_panel_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🎪 Выставка$"), exhibition_panel_open),
            CommandHandler("exhibition",  exhibition_panel_open),
            CommandHandler("exhibitions", exhibition_panel_open),
        ],
        states={
            ExhibitionPanelState.IDLE: [
                CallbackQueryHandler(exhibition_panel_cb, pattern=r"^ep_"),
            ],
            ExhibitionPanelState.ADDING: [
                CallbackQueryHandler(exhibition_panel_cb, pattern=r"^ep_cadd$|^ep_skip_year$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibition_panel_add_name),
            ],
            ExhibitionPanelState.ADDING_YEAR: [
                CallbackQueryHandler(exhibition_panel_cb, pattern=r"^ep_skip_year$|^ep_cadd$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, exhibition_panel_add_year),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    settings_panel_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^⚙️ Настройки$"), settings_panel_open),
        ],
        states={
            SettingsPanelState.IDLE: [
                CallbackQueryHandler(settings_panel_cb, pattern=r"^sp_"),
            ],
            SettingsPanelState.EDITING: [
                CallbackQueryHandler(settings_panel_cb, pattern=r"^sp_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_panel_edit_value),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    admin_panel_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👥 Админ$"), admin_panel_open)],
        states={
            AdminPanelState.IDLE: [
                CallbackQueryHandler(admin_panel_cb, pattern=r"^ap_"),
            ],
            AdminPanelState.ADDING_USER: [
                CallbackQueryHandler(admin_panel_cb, pattern=r"^ap_back$"),
                MessageHandler(filters.ALL & ~filters.COMMAND, admin_panel_recv_add),
            ],
            AdminPanelState.REMOVING_USER: [
                CallbackQueryHandler(admin_panel_cb, pattern=r"^ap_back$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_panel_recv_remove),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # Access gate — must run before every other handler
    app.add_handler(TypeHandler(Update, access_gate), group=-1)

    # Always-accessible access management
    app.add_handler(CommandHandler("whoami",     whoami))
    app.add_handler(CommandHandler("setowner",   cmd_setowner))
    app.add_handler(CommandHandler("adduser",    cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users",      cmd_users))

    catalog_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^📦 Каталог"), catalog_open)],
        states={
            CatalogState.LIST: [
                CallbackQueryHandler(catalog_cb, pattern=r"^cat_"),
            ],
            CatalogState.DETAIL: [
                CallbackQueryHandler(catalog_cb, pattern=r"^cat_"),
                # edit_{pid} callbacks fall through to edit_panel_conv
            ],
            CatalogState.DELETE_CONFIRM: [
                CallbackQueryHandler(catalog_cb, pattern=r"^cat_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(admin_panel_conv)
    app.add_handler(catalog_conv)
    app.add_handler(edit_panel_conv)
    app.add_handler(factory_panel_conv)
    app.add_handler(exhibition_panel_conv)
    app.add_handler(product_conv)
    app.add_handler(settings_panel_conv)

    logger.info("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
