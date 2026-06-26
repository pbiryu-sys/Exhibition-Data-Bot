"""
Google Sheets + Drive export for product cards.
Auth: Google Service Account JSON (stored in GOOGLE_SERVICE_ACCOUNT_JSON secret).

Required secrets:
  GOOGLE_SERVICE_ACCOUNT_JSON  — full JSON content of the service account key file

Settings (in settings.json):
  google_share_email  — your personal Gmail; the sheet + folders are shared here
  google_sheet_id     — auto-set on first export; do not edit manually
"""
import os
import time
import json
import logging
from io import BytesIO
from datetime import datetime
from pricing import load_settings, save_settings, calculate_cost_per_unit

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_TAB_NAME   = "Products"
ROOT_FOLDER_NAME = "Exhibition Bot"
SHEET_HEADERS    = [
    "product_id", "sku", "factory", "category",
    "fob_usd", "qty_40hq", "cost_price_kzt", "sell_price_kzt",
    "exhibition", "created_at", "photos", "videos", "documents",
]


# ── Auth ───────────────────────────────────────────────────────────────────────

def _sa_json() -> dict | None:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        logger.exception("GOOGLE_SERVICE_ACCOUNT_JSON JSON parse failed")
        return None


def _sa_email() -> str:
    info = _sa_json()
    return info.get("client_email", "unknown") if info else "unknown"


def is_configured() -> bool:
    return _sa_json() is not None


def _creds():
    from google.oauth2.service_account import Credentials
    info = _sa_json()
    if not info:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set or invalid.")
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _sheets_client():
    import gspread
    logger.info(">>> INIT SHEETS CLIENT")
    info = _sa_json()
    if not info:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set or invalid.")
    client = gspread.service_account_from_dict(info, scopes=SCOPES)
    logger.info(">>> SHEETS CLIENT OK (sa=%s)", info.get("client_email"))
    return client


def _drive_service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_creds(), cache_discovery=False)


# ── Google Drive helpers ───────────────────────────────────────────────────────

def _share_with_email(drive, file_id: str, email: str, role: str = "writer") -> None:
    if not email or "@" not in email:
        return
    try:
        drive.permissions().create(
            fileId=file_id,
            body={"role": role, "type": "user", "emailAddress": email},
            sendNotificationEmail=False,
        ).execute()
        logger.info("Shared %s with %s (role=%s)", file_id, email, role)
    except Exception:
        logger.exception("Failed to share %s with %s", file_id, email)


def _share_anyone_reader(drive, file_id: str) -> None:
    try:
        drive.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()
    except Exception:
        logger.exception("share_anyone_reader failed for %s", file_id)


def _ensure_folder(drive, name: str, parent_id: str | None = None,
                   share_email: str | None = None) -> str:
    safe = name.replace("'", "\\'")
    q = (
        f"name='{safe}' and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    if parent_id:
        q += f" and '{parent_id}' in parents"
    resp = drive.files().list(q=q, fields="files(id)").execute()
    hits = resp.get("files", [])
    if hits:
        fid = hits[0]["id"]
        logger.info("Drive folder found: '%s' id=%s", name, fid)
        return fid
    meta: dict = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    f = drive.files().create(body=meta, fields="id").execute()
    folder_id = f["id"]
    logger.info("Drive folder CREATED: '%s' id=%s", name, folder_id)
    _share_anyone_reader(drive, folder_id)
    if share_email:
        _share_with_email(drive, folder_id, share_email, role="writer")
    return folder_id


def _upload_bytes(drive, data: bytes, name: str, mime: str, folder_id: str) -> str:
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(BytesIO(data), mimetype=mime, resumable=False)
    f = drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    _share_anyone_reader(drive, f["id"])
    link = f.get("webViewLink", "")
    logger.info("Drive upload OK: %s → %s", name, link)
    return link


async def _upload_tg_file(bot, file_id: str, file_name: str,
                           mime: str, folder_id: str, drive) -> str:
    tg_file = await bot.get_file(file_id)
    buf = BytesIO()
    await tg_file.download_to_memory(buf)
    return _upload_bytes(drive, buf.getvalue(), file_name, mime, folder_id)


# ── Google Sheets helpers ──────────────────────────────────────────────────────

def _open_or_create_sheet(gc, drive, sheet_id: str | None, share_email: str | None):
    logger.info(">>> OPEN OR CREATE SHEET (sheet_id=%s)", sheet_id)
    sh = None

    if sheet_id:
        try:
            sh = gc.open_by_key(sheet_id)
            logger.info(">>> SHEET READY (existing) id=%s url=%s", sh.id, sh.url)
            return sh
        except Exception:
            logger.exception("Cannot open sheet by key %s — will create new", sheet_id)

    sh = gc.create(SHEET_TAB_NAME)
    logger.info(">>> SHEET READY (newly created) id=%s url=%s", sh.id, sh.url)

    # Share with user's Gmail so it appears in their Drive
    if share_email:
        try:
            drive.permissions().create(
                fileId=sh.id,
                body={"role": "writer", "type": "user",
                      "emailAddress": share_email},
                sendNotificationEmail=False,
            ).execute()
            logger.info("Sheet shared with %s (writer)", share_email)
        except Exception:
            logger.exception("Failed to share sheet with %s", share_email)

    # Persist new sheet ID
    try:
        s = load_settings()
        s["google_sheet_id"] = sh.id
        save_settings(s)
        logger.info("Saved google_sheet_id=%s to settings.json", sh.id)
    except Exception:
        logger.exception("Failed to persist google_sheet_id to settings.json")

    return sh


def _worksheet(sh):
    ws = sh.sheet1
    ws.update_title(SHEET_TAB_NAME)
    if not ws.row_values(1):
        ws.append_row(SHEET_HEADERS, value_input_option="RAW")
        logger.info("Header row written to sheet")
    return ws


def _append_row(product: dict, settings: dict,
                photo_links: list, video_links: list, doc_links: list,
                sheet_id: str | None, drive, share_email: str | None) -> str:
    gc = _sheets_client()
    sh = _open_or_create_sheet(gc, drive, sheet_id, share_email)
    ws = _worksheet(sh)

    fob = product.get("fob_usd", 0)
    qty = product.get("qty_40hq", 0)
    try:
        calc     = calculate_cost_per_unit(fob, qty, settings)
        cost_kzt = calc.get("unit_cost", 0)
        sell_kzt = calc.get("sell_price", 0)
    except Exception:
        logger.exception("Pricing calc failed for product %s", product.get("id"))
        cost_kzt = sell_kzt = 0

    ts = product.get("created_at")
    dt = datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else ""

    row = [
        product.get("id", ""),
        product.get("sku") or product.get("article") or "",
        product.get("factory", ""),
        product.get("category", ""),
        fob,
        qty,
        round(cost_kzt, 2),
        round(sell_kzt, 2),
        product.get("exhibition", ""),
        dt,
        "\n".join(photo_links),
        "\n".join(video_links),
        "\n".join(doc_links),
    ]

    logger.info(">>> APPEND ROW START — product=%s factory=%s sku=%s",
                product.get("id"), product.get("factory"), row[1])
    ws.append_row(row, value_input_option="USER_ENTERED")
    logger.info(">>> APPEND ROW SUCCESS — sheet=%s", sh.id)

    return sh.url


# ── Main export entry point ────────────────────────────────────────────────────

async def export_product(product: dict, settings: dict, bot) -> dict:
    """
    Full export pipeline. All exceptions are logged with full tracebacks
    and then re-raised so callers can see exactly where it broke.
    """
    logger.info(">>> EXPORT STARTED — product_id=%s", product.get("id", "?"))

    if not is_configured():
        logger.info("Google export skipped — GOOGLE_SERVICE_ACCOUNT_JSON not set")
        return {"status": "not_configured"}

    pid         = product.get("id", "?")
    sku         = product.get("sku") or product.get("article") or product["id"][:8]
    factory     = (product.get("factory") or "unknown").replace("/", "-")[:20]
    date        = datetime.now().strftime("%Y%m%d")
    share_email = (settings.get("google_share_email") or "").strip() or None

    logger.info(">>> PRODUCT SAVED — product_id=%s factory=%s sku=%s sa=%s share_with=%s",
                pid, factory, sku, _sa_email(), share_email or "(none — set in ⚙️ Settings)")

    # ── Drive setup ────────────────────────────────────────────────────────────
    logger.info(">>> DRIVE UPLOAD START — connecting to Drive API")
    try:
        drive = _drive_service()
        logger.info("Drive service authenticated as %s", _sa_email())
    except Exception:
        logger.exception("Drive auth FAILED")
        return {"status": "drive_error", "error": "Drive auth failed — see logs"}

    try:
        root_id   = _ensure_folder(drive, ROOT_FOLDER_NAME, share_email=share_email)
        folder_id = _ensure_folder(drive, f"{factory}_{sku}_{date}", root_id,
                                   share_email=share_email)
    except Exception:
        logger.exception("Drive folder setup FAILED")
        return {"status": "drive_error", "error": "Drive folder setup failed — see logs"}

    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    logger.info(">>> DRIVE UPLOAD DONE — folder_url=%s", folder_url)

    photo_links: list[str] = []
    video_links: list[str] = []
    doc_links:   list[str] = []

    # ── Upload hero media ──────────────────────────────────────────────────────
    for m in product.get("hero_media") or []:
        fid   = m.get("telegram_file_id") or m.get("file_id")
        if not fid:
            continue
        ftype = m.get("type", "photo")
        fname = (
            m.get("file_name")
            or (f"photo_{int(time.time())}.jpg" if ftype == "photo"
                else f"video_{int(time.time())}.mp4")
        )
        mime = "image/jpeg" if ftype == "photo" else "video/mp4"
        try:
            link = await _upload_tg_file(bot, fid, fname, mime, folder_id, drive)
            (photo_links if ftype == "photo" else video_links).append(link)
        except Exception:
            logger.exception("Hero media upload FAILED [%s]", fname)

    # ── Upload attached documents ──────────────────────────────────────────────
    for d in product.get("documents") or []:
        fid   = d.get("telegram_file_id")
        if not fid:
            continue
        fname = d.get("file_name", "document")
        ftype = d.get("file_type", "application/octet-stream")
        if ftype == "photo":
            mime = "image/jpeg"
        elif ftype == "video":
            mime = "video/mp4"
        else:
            mime = ftype if "/" in ftype else "application/octet-stream"
        try:
            link = await _upload_tg_file(bot, fid, fname, mime, folder_id, drive)
            doc_links.append(link)
        except Exception:
            logger.exception("Doc upload FAILED [%s]", fname)

    # ── Append Sheets row ──────────────────────────────────────────────────────
    sheet_id = settings.get("google_sheet_id") or None
    try:
        sheet_url = _append_row(
            product, settings, photo_links, video_links, doc_links,
            sheet_id, drive, share_email,
        )
    except Exception:
        logger.exception("Sheets append FAILED for product=%s", pid)
        return {"status": "sheets_error",
                "error": "Sheets append failed — see logs", "folder_url": folder_url}

    logger.info(
        ">>> EXPORT COMPLETE — product=%s sheet=%s folder=%s "
        "photos=%d videos=%d docs=%d",
        pid, sheet_url, folder_url, len(photo_links), len(video_links), len(doc_links),
    )
    return {
        "status":     "ok",
        "sheet_url":  sheet_url,
        "folder_url": folder_url,
        "photos":     len(photo_links),
        "videos":     len(video_links),
        "docs":       len(doc_links),
    }
