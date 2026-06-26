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
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

from states import ProductState, CatalogEditState, FactoryPanelState, ExhibitionPanelState, SettingsPanelState
from pricing import load_settings, save_settings, calculate_cost_per_unit
from formatters import format_product_card, format_settings
from context_store import (
    load_user_context, save_user_context, clear_user_context,
    get_factories, add_factory, delete_factory,
    get_exhibitions, add_exhibition, delete_exhibition, exhibition_display,
)
from products import (
    save_product, get_products, get_product, delete_product, update_product,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

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
            [KeyboardButton("📚 Каталог"), KeyboardButton("⚙️ Настройки")],
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


# ─── Catalog helpers ────────────────────────────────────────────────────────

def _catalog_text(p: dict, index: int, total: int) -> str:
    from datetime import datetime
    dt = datetime.fromtimestamp(p.get("created_at", 0)).strftime("%d.%m.%Y")
    photo_line = "📸 <b>Фото:</b> есть" if p.get("photo_file_id") else ""
    note = p.get("note") or "—"
    lines = [
        f"📦 <b>{p['sku']}</b>",
        f"🏷 {p['category']}",
        f"🏭 {p['factory']}",
        f"🎪 {p['exhibition']}",
        "",
        f"💵 FOB: <b>${p['fob_usd']:,.2f}</b> · {p['qty_40hq']:,} шт / 40HQ",
        f"💴 KZ цена: <b>₸{p['kz_price']:,.0f}</b>",
        f"📝 {note}",
    ]
    if photo_line:
        lines.append(photo_line)
    lines += ["", f"📅 {dt}   <i>{index + 1} из {total}</i>"]
    return "\n".join(lines)


def _catalog_keyboard(index: int, total: int, p: dict) -> InlineKeyboardMarkup:
    pid = p["id"]
    prev_i = (index - 1) % total
    next_i = (index + 1) % total

    if total > 1:
        nav_row = [
            InlineKeyboardButton("⬅️", callback_data=f"cprev:{prev_i}"),
            InlineKeyboardButton(f"{index + 1} / {total}", callback_data="cnoop"),
            InlineKeyboardButton("➡️", callback_data=f"cnext:{next_i}"),
        ]
    else:
        nav_row = [InlineKeyboardButton("1 / 1", callback_data="cnoop")]

    rows = [
        nav_row,
        [
            InlineKeyboardButton("✏️ Изменить", callback_data=f"cedit:{pid}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"cdel:{pid}:{index}"),
        ],
    ]
    if p.get("photo_file_id"):
        rows.append([InlineKeyboardButton("📸 Показать фото", callback_data=f"cphoto:{pid}")])
    return InlineKeyboardMarkup(rows)


# ─── Catalog handlers ────────────────────────────────────────────────────────

async def catalog_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    products = get_products(user_id)
    ctx = load_user_context(user_id)

    if not products:
        await _reply(update,
            "📚 <b>Каталог пуст.</b>\n\nДобавьте товар через «➕ Новый товар».",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(ctx),
        )
        return

    index = 0
    p = products[index]
    await update.message.reply_text(
        _catalog_text(p, index, len(products)),
        parse_mode=ParseMode.HTML,
        reply_markup=_catalog_keyboard(index, len(products), p),
    )


async def catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "cnoop":
        return

    products = get_products(user_id)

    if data.startswith("cprev:") or data.startswith("cnext:"):
        index = int(data.split(":")[1])
        if not products:
            await query.edit_message_text("📚 Каталог пуст.", parse_mode=ParseMode.HTML)
            return
        index = max(0, min(index, len(products) - 1))
        p = products[index]
        await query.edit_message_text(
            _catalog_text(p, index, len(products)),
            parse_mode=ParseMode.HTML,
            reply_markup=_catalog_keyboard(index, len(products), p),
        )

    elif data.startswith("cdel:"):
        _, pid, curr = data.split(":")
        delete_product(user_id, pid)
        products = get_products(user_id)
        if not products:
            await query.edit_message_text("📚 <b>Каталог пуст.</b>", parse_mode=ParseMode.HTML)
            return
        index = min(int(curr), len(products) - 1)
        p = products[index]
        await query.edit_message_text(
            _catalog_text(p, index, len(products)),
            parse_mode=ParseMode.HTML,
            reply_markup=_catalog_keyboard(index, len(products), p),
        )

    elif data.startswith("cphoto:"):
        pid = data.split(":")[1]
        p = get_product(user_id, pid)
        if p and p.get("photo_file_id"):
            await query.message.reply_photo(photo=p["photo_file_id"])
        else:
            await query.answer("Фото не найдено", show_alert=True)


# ─── Catalog edit conversation ───────────────────────────────────────────────

async def catalog_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    pid = query.data.split(":", 1)[1]

    p = get_product(user_id, pid)
    if not p:
        await query.answer("Товар не найден", show_alert=True)
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["_edit_product_id"] = pid
    context.user_data["factory"]    = p["factory"]
    context.user_data["exhibition"] = p["exhibition"]

    await query.message.reply_text(
        f"✏️ <b>Редактирование: {p['sku']}</b>\n\n"
        "Отправьте новое фото или /skip чтобы оставить прежнее.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return CatalogEditState.PHOTO


async def catalog_edit_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    await _reply(update,
        f"📋 <b>Данные товара</b>\n\n{DATA_PROMPT}",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return CatalogEditState.DATA


async def catalog_edit_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid = context.user_data.get("_edit_product_id")
    existing = get_product(update.effective_user.id, pid) if pid else None
    context.user_data["photo_file_id"] = existing.get("photo_file_id") if existing else None
    await _reply(update,
        f"📋 <b>Данные товара</b>\n\n{DATA_PROMPT}",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return CatalogEditState.DATA


async def catalog_edit_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lines = [l.strip() for l in update.message.text.strip().splitlines() if l.strip()]

    if len(lines) < 4:
        names = ["Артикул", "Категория", "FOB цена", "Кол-во 40HQ"]
        missing = names[len(lines):]
        await _reply(update,
            "⚠️ Не хватает полей:\n" + "\n".join(f"• {m}" for m in missing) +
            "\n\nОтправьте данные снова.",
            parse_mode=ParseMode.HTML,
        )
        return CatalogEditState.DATA

    article  = lines[0]
    category = lines[1]
    fob_raw  = lines[2]
    qty_raw  = lines[3]
    note     = " ".join(lines[4:]) if len(lines) > 4 else "—"

    try:
        price = float(fob_raw.replace(",", ".").replace(" ", ""))
        if price <= 0:
            raise ValueError
    except ValueError:
        await _reply(update,
            f"⚠️ Неверная FOB цена: <code>{fob_raw}</code>\n\nОтправьте данные снова.",
            parse_mode=ParseMode.HTML,
        )
        return CatalogEditState.DATA

    try:
        qty = int(qty_raw.replace(" ", "").replace(",", ""))
        if qty <= 0:
            raise ValueError
    except ValueError:
        await _reply(update,
            f"⚠️ Неверное количество: <code>{qty_raw}</code>\n\nОтправьте данные снова.",
            parse_mode=ParseMode.HTML,
        )
        return CatalogEditState.DATA

    context.user_data.update({"article": article, "price": price, "qty_40hq": qty, "note": note})

    if category not in ALLOWED_CATEGORIES:
        await _reply(update,
            f"⚠️ Категория <b>{category}</b> не распознана.\n\nВыберите из списка:",
            parse_mode=ParseMode.HTML,
            reply_markup=_category_keyboard(),
        )
        return CatalogEditState.CATEGORY_PICK

    context.user_data["category"] = category
    return await finish_product(update, context)


async def catalog_edit_category_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text not in ALLOWED_CATEGORIES:
        await _reply(update, "Выберите категорию из кнопок:", reply_markup=_category_keyboard())
        return CatalogEditState.CATEGORY_PICK
    context.user_data["category"] = text
    return await finish_product(update, context)


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

    # Save to persistent catalog
    calc = calculate_cost_per_unit(data["price"], data["qty_40hq"], settings)
    edit_pid = data.pop("_edit_product_id", None)
    product_record = {
        "sku":          data["article"],
        "category":     data["category"],
        "fob_usd":      data["price"],
        "qty_40hq":     data["qty_40hq"],
        "kz_price":     calc["G5"],
        "factory":      data["factory"],
        "exhibition":   data["exhibition"],
        "note":         data.get("note", "—"),
        "photo_file_id": data.get("photo_file_id"),
    }
    if edit_pid:
        update_product(user_id, edit_pid, product_record)
    else:
        save_product(user_id, product_record)

    ctx = load_user_context(user_id)
    reply_markup = _main_keyboard(ctx)

    if data.get("photo_file_id"):
        await _reply_photo(update,
            photo=data["photo_file_id"],
            caption=card_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    else:
        await _reply(update,
            card_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ctx = load_user_context(user_id)
    msg_id  = context.user_data.pop("_panel_msg_id",  None)
    chat_id = context.user_data.pop("_panel_chat_id", None)
    if msg_id and chat_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    else:
        await _reply(update, "❌ Ввод отменён.", reply_markup=_main_keyboard(ctx))
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
    factories = get_factories(user_id)
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
        factories = get_factories(user_id)
        if 0 <= idx < len(factories):
            ctx = load_user_context(user_id)
            save_user_context(user_id, factories[idx], ctx["active_exhibition"])
        await query.edit_message_text(
            _fp_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_fp_keyboard(user_id)
        )
        return FactoryPanelState.IDLE

    if data.startswith("fp_del:"):
        idx = int(data.split(":")[1])
        delete_factory(user_id, idx)
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
    add_factory(user_id, name)
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
    exhibitions = get_exhibitions(user_id)
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
        exhibitions = get_exhibitions(user_id)
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
        delete_exhibition(user_id, idx)
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
        add_exhibition(user_id, name, None)
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
    add_exhibition(user_id, name, year)
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

    catalog_edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(catalog_edit_start, pattern=r"^cedit:")],
        states={
            CatalogEditState.PHOTO: [
                MessageHandler(filters.PHOTO, catalog_edit_photo),
                CommandHandler("skip", catalog_edit_skip_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, catalog_edit_photo),
            ],
            CatalogEditState.DATA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, catalog_edit_data),
            ],
            CatalogEditState.CATEGORY_PICK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, catalog_edit_category_pick),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📚 Каталог$"), catalog_open))
    app.add_handler(catalog_edit_conv)
    app.add_handler(factory_panel_conv)
    app.add_handler(exhibition_panel_conv)
    app.add_handler(product_conv)
    app.add_handler(settings_panel_conv)
    app.add_handler(CallbackQueryHandler(catalog_callback))

    logger.info("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
