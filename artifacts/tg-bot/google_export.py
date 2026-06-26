"""
Google Sheets + Drive export for product cards.
Auth: Google Service Account JSON (stored in GOOGLE_SERVICE_ACCOUNT_JSON secret).

IMPORTANT — service accounts have NO Drive storage quota of their own.
Files must be written into resources that the USER already owns and has shared
with the service account (Editor role).

Setup required (one-time):
  1. Create a Google Spreadsheet in your Drive.
     Share it with the SA email as Editor.
     Paste the sheet ID into Settings → "Google Sheet ID".

  2. (Optional) Create a Drive folder in your Drive.
     Share it with the SA email as Editor.
     Paste the folder ID into Settings → "Google Drive Folder ID".
     If omitted, media files are skipped — only the sheet row is written.

  3. Make sure Sheets API and Drive API are enabled in your Google Cloud project.

Service account email: printed at export start so you know what to share with.
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

SHEET_TAB_NAME = "Products"
SHEET_HEADERS  = [
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
    """True when the service account secret is present."""
    return _sa_json() is not None


def _creds():
    from google.oauth2.service_account import Credentials
    info = _sa_json()
    if not info:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set or invalid.")
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _sheets_client():
    """gspread 6.x: use service_account_from_dict (authorize() was removed)."""
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

def _upload_bytes(drive, data: bytes, name: str, mime: str, folder_id: str) -> str:
    """Upload raw bytes into a user-owned Drive folder; return webViewLink."""
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(BytesIO(data), mimetype=mime, resumable=False)
    f = drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    link = f.get("webViewLink", "")
    logger.info(">>> DRIVE UPLOAD DONE: %s → %s", name, link)
    return link


async def _upload_tg_file(bot, file_id: str, file_name: str,
                           mime: str, folder_id: str, drive) -> str:
    tg_file = await bot.get_file(file_id)
    buf = BytesIO()
    await tg_file.download_to_memory(buf)
    return _upload_bytes(drive, buf.getvalue(), file_name, mime, folder_id)


# ── Google Sheets helpers ──────────────────────────────────────────────────────

def _open_sheet(gc, sheet_id: str):
    """
    Open a spreadsheet the user already owns and has shared with the SA.
    Never creates a sheet — service accounts have no Drive quota.
    """
    logger.info(">>> OPEN OR CREATE SHEET (sheet_id=%s)", sheet_id)
    sh = gc.open_by_key(sheet_id)
    logger.info(">>> SHEET READY — title='%s' id=%s url=%s", sh.title, sh.id, sh.url)
    return sh


def _worksheet(sh):
    try:
        ws = sh.sheet1
    except Exception:
        ws = sh.get_worksheet(0)

    logger.info("Worksheet: title='%s' id=%s cols=%s",
                ws.title, ws.id, ws.col_count)

    if not ws.row_values(1):
        ws.append_row(SHEET_HEADERS, value_input_option="RAW")
        logger.info("Header row written (%d columns)", len(SHEET_HEADERS))
    else:
        existing = ws.row_values(1)
        logger.info("Existing header (%d cols): %s", len(existing), existing)

    return ws


def _append_row(product: dict, settings: dict,
                photo_links: list, video_links: list, doc_links: list,
                sheet_id: str, drive, drive_folder_id: str | None) -> str:
    gc = _sheets_client()
    sh = _open_sheet(gc, sheet_id)
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

    if len(row) != len(SHEET_HEADERS):
        raise RuntimeError(
            f"Row length {len(row)} != headers length {len(SHEET_HEADERS)} — aborting append"
        )

    logger.info(">>> APPEND ROW START — %d columns, product=%s factory=%s sku=%s",
                len(row), product.get("id"), product.get("factory"), row[1])
    ws.append_row(row, value_input_option="USER_ENTERED")
    logger.info(">>> APPEND ROW SUCCESS — sheet=%s", sh.id)

    return sh.url


# ── Main export entry point ────────────────────────────────────────────────────

async def export_product(product: dict, settings: dict, bot) -> dict:
    """
    Full export pipeline:
      1. Upload media to user's Drive folder (if google_drive_folder_id is set).
      2. Append one row to the user's Google Sheet (google_sheet_id required).

    Returns {"status": "ok", ...} or {"status": "<error_type>", "error": "..."}
    """
    logger.info(">>> EXPORT STARTED — product_id=%s", product.get("id", "?"))

    if not is_configured():
        logger.info("Google export skipped — GOOGLE_SERVICE_ACCOUNT_JSON not set")
        return {"status": "not_configured"}

    pid             = product.get("id", "?")
    sku             = product.get("sku") or product.get("article") or product["id"][:8]
    factory         = (product.get("factory") or "unknown").replace("/", "-")[:20]
    sheet_id        = (settings.get("google_sheet_id") or "").strip()
    drive_folder_id = (settings.get("google_drive_folder_id") or "").strip() or None
    sa_email        = _sa_email()

    logger.info(
        ">>> PRODUCT SAVED — id=%s factory=%s sku=%s | sa=%s | sheet_id=%s | drive_folder=%s",
        pid, factory, sku, sa_email, sheet_id or "(NOT SET)", drive_folder_id or "(none)",
    )

    # Sheet ID is mandatory
    if not sheet_id:
        msg = (
            f"google_sheet_id not set. "
            f"Create a Google Sheet, share it with {sa_email} (Editor), "
            f"then paste the sheet ID into ⚙️ Settings → 'Google Sheet ID'."
        )
        logger.error(msg)
        return {"status": "no_sheet_id", "error": msg}

    # ── Drive media uploads (optional) ─────────────────────────────────────────
    photo_links: list[str] = []
    video_links: list[str] = []
    doc_links:   list[str] = []

    if drive_folder_id:
        logger.info(">>> DRIVE UPLOAD START — folder_id=%s", drive_folder_id)
        try:
            drive = _drive_service()
            logger.info("Drive service authenticated as %s", sa_email)
        except Exception:
            logger.exception("Drive auth FAILED")
            drive = None

        if drive:
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
                    link = await _upload_tg_file(bot, fid, fname, mime, drive_folder_id, drive)
                    (photo_links if ftype == "photo" else video_links).append(link)
                except Exception:
                    logger.exception("Hero media upload FAILED [%s]", fname)

            for d in product.get("documents") or []:
                fid   = d.get("telegram_file_id")
                if not fid:
                    continue
                fname = d.get("file_name", "document")
                ftype = d.get("file_type", "application/octet-stream")
                mime = (
                    "image/jpeg" if ftype == "photo" else
                    "video/mp4"  if ftype == "video" else
                    (ftype if "/" in ftype else "application/octet-stream")
                )
                try:
                    link = await _upload_tg_file(bot, fid, fname, mime, drive_folder_id, drive)
                    doc_links.append(link)
                except Exception:
                    logger.exception("Doc upload FAILED [%s]", fname)

        folder_url = f"https://drive.google.com/drive/folders/{drive_folder_id}"
        logger.info("Drive uploads done — photos=%d videos=%d docs=%d folder=%s",
                    len(photo_links), len(video_links), len(doc_links), folder_url)
    else:
        logger.info("Drive upload skipped — google_drive_folder_id not set")
        folder_url = ""

    # ── Append Sheets row ──────────────────────────────────────────────────────
    try:
        drive_svc = _drive_service() if drive_folder_id else None
        sheet_url = _append_row(
            product, settings, photo_links, video_links, doc_links,
            sheet_id, drive_svc, drive_folder_id,
        )
    except Exception:
        logger.exception("Sheets append FAILED for product=%s", pid)
        return {
            "status":     "sheets_error",
            "error":      "Sheets append failed — see bot logs for full traceback",
            "folder_url": folder_url,
        }

    logger.info(
        ">>> EXPORT COMPLETE — product=%s sheet=%s folder=%s photos=%d videos=%d docs=%d",
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
