"""
Google Sheets + Drive export for product cards.
Authenticates via Replit OAuth connector credentials proxy — no hardcoded API keys.

Required env vars (set after user connects via proposeIntegration):
  GOOGLE_SHEETS_CONN_ID  — Replit connection ID for Google Sheets
  GOOGLE_DRIVE_CONN_ID   — Replit connection ID for Google Drive

Optional env vars (auto-configured on first run):
  GOOGLE_SHEET_ID        — Spreadsheet ID; auto-created if blank
  GOOGLE_DRIVE_FOLDER_ID — Root Drive folder ID; auto-created if blank
"""
import os
import time
import logging
from io import BytesIO
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

_PROXY    = os.environ.get("REPLIT_CONNECTORS_HOSTNAME", "")
_IDENTITY = os.environ.get("REPL_IDENTITY", "")

SHEETS_CONN = os.environ.get("GOOGLE_SHEETS_CONN_ID", "")
DRIVE_CONN  = os.environ.get("GOOGLE_DRIVE_CONN_ID", "")

ROOT_FOLDER_NAME = "Exhibition Bot"
SHEET_TAB_NAME   = "Products"
SHEET_HEADERS    = [
    "product_id", "sku", "factory", "category",
    "fob_usd", "qty_40hq", "cost_price_kzt", "sell_price_kzt",
    "exhibition", "created_at", "photos", "videos", "documents",
]


# ── Auth ───────────────────────────────────────────────────────────────────────

def is_configured() -> bool:
    """True when both Google connections are wired."""
    return bool(_PROXY and SHEETS_CONN and DRIVE_CONN)


def _token(conn_id: str) -> str:
    """Fetch a fresh OAuth access-token from the Replit credentials proxy."""
    if not _PROXY or not _IDENTITY or not conn_id:
        raise RuntimeError(
            "Google integration not configured. "
            "Set GOOGLE_SHEETS_CONN_ID and GOOGLE_DRIVE_CONN_ID env vars."
        )
    r = httpx.get(
        f"https://{_PROXY}/api/v2/connection/{conn_id}/credentials",
        headers={"Authorization": f"Bearer {_IDENTITY}"},
        timeout=10,
    )
    r.raise_for_status()
    token = r.json().get("accessToken")
    if not token:
        raise RuntimeError(f"No accessToken in response for connection {conn_id}")
    return token


def _sheets_client():
    import gspread
    from google.oauth2.credentials import Credentials
    creds = Credentials(token=_token(SHEETS_CONN))
    return gspread.authorize(creds)


def _drive_service():
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    creds = Credentials(token=_token(DRIVE_CONN))
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ── Google Drive helpers ───────────────────────────────────────────────────────

def _ensure_folder(drive, name: str, parent_id: str | None = None) -> str:
    """Find or create a Drive folder; return its ID."""
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    if parent_id:
        q += f" and '{parent_id}' in parents"
    resp = drive.files().list(q=q, fields="files(id)").execute()
    hits = resp.get("files", [])
    if hits:
        return hits[0]["id"]
    meta: dict = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    f = drive.files().create(body=meta, fields="id").execute()
    _share_anyone(drive, f["id"])
    return f["id"]


def _share_anyone(drive, file_id: str) -> None:
    """Grant read access to anyone with the link."""
    try:
        drive.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()
    except Exception as e:
        logger.warning("share_anyone failed for %s: %s", file_id, e)


def _upload_bytes(drive, data: bytes, name: str, mime: str, folder_id: str) -> str:
    """Upload raw bytes to Drive folder; return shareable webViewLink."""
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(BytesIO(data), mimetype=mime, resumable=False)
    f = drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    _share_anyone(drive, f["id"])
    return f.get("webViewLink", "")


async def _upload_tg_file(bot, file_id: str, file_name: str,
                           mime: str, folder_id: str, drive) -> str:
    """Download Telegram file → upload to Drive → return link."""
    tg_file = await bot.get_file(file_id)
    buf = BytesIO()
    await tg_file.download_to_memory(buf)
    return _upload_bytes(drive, buf.getvalue(), file_name, mime, folder_id)


# ── Google Sheets helpers ──────────────────────────────────────────────────────

def _open_or_create_sheet(gc, sheet_id: str | None):
    """Return gspread Spreadsheet object, creating it if necessary."""
    if sheet_id:
        try:
            return gc.open_by_key(sheet_id)
        except Exception:
            logger.warning("Could not open sheet %s — creating new one", sheet_id)
    try:
        return gc.open(SHEET_TAB_NAME)
    except Exception:
        pass
    sh = gc.create(SHEET_TAB_NAME)
    sh.share("", perm_type="anyone", role="writer")
    return sh


def _worksheet(sh):
    """Return the Products worksheet, adding headers on first use."""
    ws = sh.sheet1
    ws.update_title(SHEET_TAB_NAME)
    if not ws.row_values(1):
        ws.append_row(SHEET_HEADERS, value_input_option="RAW")
    return ws


def _append_row(product: dict, settings: dict,
                photo_links: list, video_links: list, doc_links: list,
                sheet_id: str | None) -> str:
    """Append one product row; return spreadsheet URL."""
    from pricing import calculate_cost_per_unit
    gc = _sheets_client()
    sh = _open_or_create_sheet(gc, sheet_id)
    ws = _worksheet(sh)

    fob = product.get("fob_usd", 0)
    qty = product.get("qty_40hq", 0)
    try:
        calc      = calculate_cost_per_unit(fob, qty, settings)
        cost_kzt  = calc.get("unit_cost", 0)
        sell_kzt  = calc.get("sell_price", 0)
    except Exception:
        cost_kzt = sell_kzt = 0

    ts  = product.get("created_at")
    dt  = datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else ""

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
    ws.append_row(row, value_input_option="USER_ENTERED")
    return sh.url


# ── Main entry point ───────────────────────────────────────────────────────────

async def export_product(product: dict, settings: dict, bot) -> dict:
    """
    Full export pipeline:
      1. Upload all product media + documents to a Google Drive folder.
      2. Append one row to the Google Sheets export table.

    Returns a result dict:
      {"status": "ok", "sheet_url": ..., "folder_url": ...,
       "photos": N, "videos": N, "docs": N}
    or
      {"status": "not_configured" | "drive_error" | "sheets_error", "error": ...}
    """
    if not is_configured():
        logger.info("Google export skipped — not configured")
        return {"status": "not_configured"}

    sku     = product.get("sku") or product.get("article") or product["id"][:8]
    factory = (product.get("factory") or "unknown").replace("/", "-")[:20]
    date    = datetime.now().strftime("%Y%m%d")

    # ── Drive setup ────────────────────────────────────────────────────────────
    try:
        drive = _drive_service()
    except Exception as e:
        logger.error("Drive auth failed: %s", e)
        return {"status": "drive_error", "error": str(e)}

    try:
        root_id   = _ensure_folder(drive, ROOT_FOLDER_NAME)
        folder_id = _ensure_folder(drive, f"{factory}_{sku}_{date}", root_id)
    except Exception as e:
        logger.error("Drive folder setup failed: %s", e)
        return {"status": "drive_error", "error": str(e)}

    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    photo_links: list[str] = []
    video_links: list[str] = []
    doc_links:   list[str] = []

    # ── Upload hero media ──────────────────────────────────────────────────────
    for m in product.get("hero_media") or []:
        fid   = m.get("telegram_file_id")
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
        except Exception as e:
            logger.warning("Hero media upload failed [%s]: %s", fname, e)

    # ── Upload attached documents ──────────────────────────────────────────────
    for d in product.get("documents") or []:
        fid   = d.get("telegram_file_id")
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
        except Exception as e:
            logger.warning("Doc upload failed [%s]: %s", fname, e)

    # ── Append Sheets row ──────────────────────────────────────────────────────
    sheet_id = settings.get("google_sheet_id") or None
    try:
        sheet_url = _append_row(
            product, settings, photo_links, video_links, doc_links, sheet_id
        )
    except Exception as e:
        logger.error("Sheets append failed: %s", e)
        return {"status": "sheets_error", "error": str(e), "folder_url": folder_url}

    # ── Persist auto-created sheet ID ─────────────────────────────────────────
    if not sheet_id:
        try:
            from pricing import load_settings, save_settings
            gc = _sheets_client()
            sh = _open_or_create_sheet(gc, None)
            s  = load_settings()
            s["google_sheet_id"] = sh.id
            save_settings(s)
        except Exception:
            pass

    logger.info(
        "Google export OK — product=%s photos=%d videos=%d docs=%d",
        product.get("id"), len(photo_links), len(video_links), len(doc_links),
    )
    return {
        "status":     "ok",
        "sheet_url":  sheet_url,
        "folder_url": folder_url,
        "photos":     len(photo_links),
        "videos":     len(video_links),
        "docs":       len(doc_links),
    }
