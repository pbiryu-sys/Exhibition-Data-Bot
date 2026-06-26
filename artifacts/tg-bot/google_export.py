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

DRIVE_TIMEOUT = 20  # seconds — hard ceiling for any single Drive/TG network call


def _upload_bytes_sync(drive, data: bytes, name: str, mime: str, folder_id: str) -> str:
    """Synchronous Drive upload — always called via asyncio.to_thread so it can be timed out."""
    from googleapiclient.http import MediaIoBaseUpload
    logger.info("DRIVE upload start — name=%s mime=%s folder=%s size=%d",
                name, mime, folder_id, len(data))
    media = MediaIoBaseUpload(BytesIO(data), mimetype=mime, resumable=False)
    f = drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    file_id  = f.get("id", "?")
    link     = f.get("webViewLink", "")
    logger.info("DRIVE upload done — file_id=%s link=%s", file_id, link)
    return link


async def _upload_tg_file(bot, file_id: str, file_name: str,
                           mime: str, folder_id: str, drive) -> str:
    import asyncio
    logger.info("TG download start — file_id=%s name=%s mime=%s", file_id, file_name, mime)

    # Step 1: get_file with timeout
    try:
        tg_file = await asyncio.wait_for(bot.get_file(file_id), timeout=DRIVE_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(f"DRIVE_UPLOAD_TIMEOUT: get_file timed out after {DRIVE_TIMEOUT}s "
                           f"(file_id={file_id})")
    logger.info("TG get_file OK — file_path=%s file_size=%s",
                getattr(tg_file, "file_path", "?"),
                getattr(tg_file, "file_size", "?"))

    # Step 2: download with timeout
    buf = BytesIO()
    try:
        await asyncio.wait_for(tg_file.download_to_memory(buf), timeout=DRIVE_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(f"DRIVE_UPLOAD_TIMEOUT: download_to_memory timed out after "
                           f"{DRIVE_TIMEOUT}s (file_id={file_id})")
    buf_size = len(buf.getvalue())
    logger.info("TG download complete — buffer_size=%d bytes", buf_size)

    if buf_size == 0:
        raise RuntimeError(
            f"Downloaded file is empty (0 bytes): file_id={file_id} name={file_name}"
        )

    # Step 3: Drive upload with timeout (sync → thread)
    try:
        link = await asyncio.wait_for(
            asyncio.to_thread(_upload_bytes_sync, drive, buf.getvalue(),
                              file_name, mime, folder_id),
            timeout=DRIVE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"DRIVE_UPLOAD_TIMEOUT: Drive API did not respond within "
                           f"{DRIVE_TIMEOUT}s (name={file_name})")
    return link


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
    # Prefer sheet1 property, fall back to get_worksheet(0)
    try:
        ws = sh.sheet1
        logger.info("Worksheet obtained via sh.sheet1")
    except Exception:
        logger.exception("sh.sheet1 failed — falling back to get_worksheet(0)")
        ws = sh.get_worksheet(0)

    # Full worksheet state dump
    logger.info(
        "Worksheet state — title='%s' id=%s col_count=%s row_count=%s",
        ws.title, ws.id, ws.col_count, ws.row_count,
    )

    # Check / write header row
    first_row = ws.row_values(1)
    if not first_row:
        logger.info("No header row found — writing %d headers", len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS, value_input_option="RAW",
                      insert_data_option="INSERT_ROWS")
        logger.info("Header row written OK")
    else:
        logger.info("Existing header row (%d cols): %s", len(first_row), first_row)
        if len(first_row) != len(SHEET_HEADERS):
            logger.error(
                "HEADER MISMATCH — sheet has %d cols, code expects %d cols (%s)",
                len(first_row), len(SHEET_HEADERS), SHEET_HEADERS,
            )

    return ws


def _append_row(product: dict, settings: dict,
                photo_links: list, video_links: list, doc_links: list,
                sheet_id: str, drive, drive_folder_id: str | None) -> str:
    gc = _sheets_client()
    sh = _open_sheet(gc, sheet_id)
    ws = _worksheet(sh)

    # ── Probe: verify write access with a trivial append ───────────────────────
    logger.info("Probe: testing write access with a single-cell append...")
    try:
        ws.append_row(["__PROBE__"], value_input_option="RAW",
                      insert_data_option="INSERT_ROWS")
        logger.info("Probe append OK — deleting probe row now")
        # Delete the probe row (it was appended at the end)
        all_vals = ws.col_values(1)
        probe_row_idx = len(all_vals)  # 1-based row index of the probe
        ws.delete_rows(probe_row_idx)
        logger.info("Probe row deleted at index %d", probe_row_idx)
    except Exception:
        logger.exception("PROBE APPEND FAILED — write access is blocked")
        raise

    # ── Build data row ─────────────────────────────────────────────────────────
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

    # ── Schema validation ──────────────────────────────────────────────────────
    logger.info(
        "Schema check — SHEET_HEADERS=%d cols, row=%d cols",
        len(SHEET_HEADERS), len(row),
    )
    logger.info("Row values: %s", row)
    if len(row) != len(SHEET_HEADERS):
        raise RuntimeError(
            f"Schema mismatch: row has {len(row)} cols but SHEET_HEADERS has "
            f"{len(SHEET_HEADERS)} cols. row={row} headers={SHEET_HEADERS}"
        )

    # ── Real append ───────────────────────────────────────────────────────────
    logger.info(">>> APPEND ROW START — product=%s factory=%s sku=%s",
                product.get("id"), product.get("factory"), row[1])
    ws.append_row(row, value_input_option="RAW", insert_data_option="INSERT_ROWS")
    logger.info(">>> APPEND ROW SUCCESS — sheet=%s url=%s", sh.id, sh.url)

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

    # ── Media inventory ─────────────────────────────────────────────────────────
    photo_links: list[str] = []
    video_links: list[str] = []
    doc_links:   list[str] = []

    logger.info("PRODUCT MEDIA STATE: %s", product)

    raw_hero = product.get("hero_media") or []
    raw_docs = product.get("documents")  or []

    # Fallback: if hero_media list is empty, reconstruct from flat photo_file_id field
    if not raw_hero:
        _fid = (product.get("photo_file_id")
                or product.get("file_id")
                or product.get("telegram_file_id"))
        logger.info("PHOTO FILE ID (fallback): %s", _fid)
        if _fid:
            raw_hero = [{"type": "photo", "file_id": _fid}]
            logger.info("FORCED hero_media from photo_file_id: %s", raw_hero)
        else:
            logger.info("NO MEDIA FOUND — skip-safe upload mode")
    else:
        logger.info("HERO MEDIA (from record): %s", raw_hero)

    logger.info("MEDIA INVENTORY — hero_media=%d docs=%d drive_folder=%s",
                len(raw_hero), len(raw_docs), drive_folder_id or "(NOT SET)")

    # ── STEP 1: Sheets row (always runs first — Drive never blocks this) ────────
    logger.info(">>> STEP 1: Sheets append — product=%s", pid)
    try:
        sheet_url = _append_row(
            product, settings, photo_links, video_links, doc_links,
            sheet_id, None, drive_folder_id,
        )
        logger.info(">>> STEP 1 COMPLETE — sheet_url=%s", sheet_url)
    except Exception:
        logger.exception("Sheets append FAILED for product=%s", pid)
        return {
            "status": "sheets_error",
            "error":  "Sheets append failed — see bot logs for full traceback",
            "folder_url": "",
        }

    # ── STEP 2: Drive media uploads (best-effort — never blocks return) ─────────
    folder_url = ""
    if drive_folder_id:
        folder_url = f"https://drive.google.com/drive/folders/{drive_folder_id}"
        logger.info(">>> STEP 2: Drive upload — folder=%s", drive_folder_id)
        try:
            drive = _drive_service()
            logger.info("Drive service OK — sa=%s", sa_email)
        except Exception:
            logger.exception("Drive auth FAILED — skipping media upload (drive_failed)")
            drive = None

        if drive:
            # Hero media (photos + videos)
            for i, m in enumerate(raw_hero):
                fid = m.get("telegram_file_id") or m.get("file_id")
                if not fid:
                    logger.warning("hero_media[%d] has no file_id — skipping", i)
                    continue
                ftype = m.get("type", "photo")
                fname = (
                    m.get("file_name")
                    or (f"photo_{int(time.time())}.jpg" if ftype == "photo"
                        else f"video_{int(time.time())}.mp4")
                )
                mime = "image/jpeg" if ftype == "photo" else "video/mp4"
                logger.info("Uploading hero_media[%d] — fid=%s ftype=%s fname=%s",
                            i, fid, ftype, fname)
                try:
                    link = await _upload_tg_file(bot, fid, fname, mime, drive_folder_id, drive)
                    (photo_links if ftype == "photo" else video_links).append(link)
                    logger.info("hero_media[%d] uploaded OK — link=%s", i, link)
                except Exception:
                    logger.exception("hero_media[%d] FAILED (drive_failed) — continuing", i)

            # Documents
            for i, d in enumerate(raw_docs):
                fid = d.get("telegram_file_id")
                if not fid:
                    logger.warning("documents[%d] has no telegram_file_id — skipping", i)
                    continue
                fname = d.get("file_name", "document")
                ftype = d.get("file_type", "application/octet-stream")
                mime = (
                    "image/jpeg" if ftype == "photo" else
                    "video/mp4"  if ftype == "video" else
                    (ftype if "/" in ftype else "application/octet-stream")
                )
                logger.info("Uploading documents[%d] — fid=%s fname=%s", i, fid, fname)
                try:
                    link = await _upload_tg_file(bot, fid, fname, mime, drive_folder_id, drive)
                    doc_links.append(link)
                    logger.info("documents[%d] uploaded OK — link=%s", i, link)
                except Exception:
                    logger.exception("documents[%d] FAILED (drive_failed) — continuing", i)

        logger.info(">>> STEP 2 COMPLETE — photos=%d videos=%d docs=%d",
                    len(photo_links), len(video_links), len(doc_links))
    else:
        logger.info(">>> STEP 2 SKIPPED — google_drive_folder_id not set")

    # ── Update Sheets row with Drive links (if any files were uploaded) ─────────
    if drive_folder_id and (photo_links or video_links or doc_links):
        logger.info("Updating Sheets row with Drive links — photos=%d videos=%d docs=%d",
                    len(photo_links), len(video_links), len(doc_links))
        try:
            _append_row(
                product, settings, photo_links, video_links, doc_links,
                sheet_id, None, drive_folder_id,
            )
            logger.info("Sheets row updated with Drive links OK")
        except Exception:
            logger.exception("Sheets link-update FAILED — Drive links lost but export was saved")

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
