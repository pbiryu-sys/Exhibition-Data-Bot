"""
Google Sheets export for product cards.
Auth: Google Service Account JSON (stored in GOOGLE_SERVICE_ACCOUNT_JSON secret).

Media links: Telegram CDN URLs are resolved via bot.get_file() and written
directly into the sheet's photos/videos/documents columns.
No Google Drive upload is needed or performed.

Setup required (one-time):
  1. Create a Google Spreadsheet in your Drive.
     Share it with the SA email as Editor.
     Paste the sheet ID into Settings → "Google Sheet ID".

  2. Make sure Google Sheets API is enabled in your Google Cloud project.

Service account email: printed at export start so you know what to share with.
"""
import asyncio
import os
import json
import logging
from io import BytesIO
from datetime import datetime
from pricing import load_settings, save_settings, calculate_cost_per_unit

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# IMPORTANT: SHEET_HEADERS and row[] in _append_row() MUST always be identical order.
# Never add/remove a header without adding/removing the matching row value at the same index.
SHEET_HEADERS = [
    "product_id",     # 0
    "sku",            # 1
    "factory",        # 2
    "category",       # 3
    "fob_usd",        # 4
    "qty_40hq",       # 5
    "cost_price_kzt", # 6
    "sell_price_kzt", # 7
    "exhibition",     # 8
    "created_at",     # 9
    "photos_preview", # 10  =IMAGE(first_photo_url)
    "photo_url",      # 11  raw Telegram CDN link(s), "\n"-joined
    "videos",         # 12  raw video URLs
    "documents",      # 13  raw doc URLs
]

TG_TIMEOUT = 15  # seconds per get_file call


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


def _sheets_client():
    import gspread
    logger.info(">>> INIT SHEETS CLIENT")
    info = _sa_json()
    if not info:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set or invalid.")
    client = gspread.service_account_from_dict(info, scopes=SCOPES)
    logger.info(">>> SHEETS CLIENT OK (sa=%s)", info.get("client_email"))
    return client


# ── Telegram URL resolver ──────────────────────────────────────────────────────

async def _get_tg_url(bot, file_id: str) -> str:
    """
    Resolve a Telegram file_id to its CDN download URL.
    In PTB 20.x, tg_file.file_path IS the full https://... URL.
    Times out after TG_TIMEOUT seconds.
    """
    logger.info("Resolving TG URL — file_id=%s", file_id)
    try:
        tg_file = await asyncio.wait_for(bot.get_file(file_id), timeout=TG_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"TG_TIMEOUT: get_file did not respond within {TG_TIMEOUT}s "
            f"(file_id={file_id})"
        )
    url = tg_file.file_path  # full https://api.telegram.org/file/bot.../... URL
    logger.info("TG URL resolved — file_path=%s size=%s",
                url, getattr(tg_file, "file_size", "?"))
    return url


# ── Google Sheets helpers ──────────────────────────────────────────────────────

def _open_sheet(gc, sheet_id: str):
    logger.info("Opening sheet — id=%s", sheet_id)
    sh = gc.open_by_key(sheet_id)
    logger.info("Sheet open — title=%r url=%s", sh.title, sh.url)
    return sh


def _worksheet(sh):
    try:
        ws = sh.sheet1
    except Exception:
        logger.exception("sh.sheet1 failed — falling back to get_worksheet(0)")
        ws = sh.get_worksheet(0)

    logger.info("Worksheet — title=%r id=%s col_count=%s row_count=%s",
                ws.title, ws.id, ws.col_count, ws.row_count)

    first_row = ws.row_values(1)
    if not first_row:
        logger.info("No header row — writing %d headers", len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS, value_input_option="RAW",
                      insert_data_option="INSERT_ROWS")
        logger.info("Header row written OK")
    else:
        logger.info("Existing header (%d cols): %s", len(first_row), first_row)
        if len(first_row) != len(SHEET_HEADERS):
            logger.error(
                "HEADER MISMATCH — sheet has %d cols, code expects %d. "
                "Expected: %s",
                len(first_row), len(SHEET_HEADERS), SHEET_HEADERS,
            )
    return ws


def _to_sheet_image(url: str) -> str:
    """Return a Google Sheets =IMAGE() formula, or empty string if no URL."""
    if not url:
        return ""
    return f'=IMAGE("{url}")'


def _append_row(product: dict, settings: dict,
                photo_links: list, video_links: list, doc_links: list,
                sheet_id: str) -> str:
    gc = _sheets_client()
    sh = _open_sheet(gc, sheet_id)
    ws = _worksheet(sh)

    # Probe write access
    logger.info("Probe: testing write access...")
    try:
        ws.append_row(["__PROBE__"], value_input_option="RAW",
                      insert_data_option="INSERT_ROWS")
        all_vals = ws.col_values(1)
        probe_idx = len(all_vals)
        ws.delete_rows(probe_idx)
        logger.info("Probe OK — deleted probe row at index %d", probe_idx)
    except Exception:
        logger.exception("PROBE FAILED — write access blocked")
        raise

    # Build row
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

    # IMPORTANT: index comments must match SHEET_HEADERS exactly (same order, same count).
    row = [
        product.get("id", ""),                                    # 0  product_id
        product.get("sku") or product.get("article") or "",      # 1  sku
        product.get("factory", ""),                               # 2  factory
        product.get("category", ""),                              # 3  category
        fob,                                                      # 4  fob_usd
        qty,                                                      # 5  qty_40hq
        round(cost_kzt, 2),                                       # 6  cost_price_kzt
        round(sell_kzt, 2),                                       # 7  sell_price_kzt
        product.get("exhibition", ""),                            # 8  exhibition
        dt,                                                       # 9  created_at
        _to_sheet_image(photo_links[0]) if photo_links else "",  # 10 photos_preview
        "\n".join(photo_links),                                   # 11 photo_url
        "\n".join(video_links),                                   # 12 videos
        "\n".join(doc_links),                                     # 13 documents
    ]

    logger.info("Schema — headers=%d row=%d", len(SHEET_HEADERS), len(row))
    logger.info("Row values: %s", row)

    if len(row) != len(SHEET_HEADERS):
        raise RuntimeError(
            f"Schema mismatch: row={len(row)} headers={len(SHEET_HEADERS)}"
        )

    logger.info(">>> APPEND ROW — product=%s sku=%s", product.get("id"), row[1])
    ws.append_row(row, value_input_option="USER_ENTERED", insert_data_option="INSERT_ROWS")
    logger.info(">>> APPEND ROW SUCCESS — sheet=%s", sh.url)
    return sh.url


# ── Main export entry point ────────────────────────────────────────────────────

async def export_product(product: dict, settings: dict, bot) -> dict:
    """
    Export pipeline:
      1. Resolve Telegram CDN URLs for all media (photos, videos, documents).
      2. Write one row to the user's Google Sheet with those URLs inline.

    Returns {"status": "ok", ...} or {"status": "<error_type>", "error": "..."}
    """
    logger.info(">>> EXPORT STARTED — product_id=%s", product.get("id", "?"))

    if not is_configured():
        logger.info("Google export skipped — GOOGLE_SERVICE_ACCOUNT_JSON not set")
        return {"status": "not_configured"}

    pid      = product.get("id", "?")
    sheet_id = (settings.get("google_sheet_id") or "").strip()
    sa_email = _sa_email()

    logger.info("id=%s sa=%s sheet_id=%s", pid, sa_email, sheet_id or "(NOT SET)")

    if not sheet_id:
        msg = (
            f"google_sheet_id not set. Create a Google Sheet, share it with "
            f"{sa_email} (Editor), then paste the ID into ⚙️ Settings → 'Google Sheet ID'."
        )
        logger.error(msg)
        return {"status": "no_sheet_id", "error": msg}

    # ── STEP 1: Resolve media URLs from Telegram ────────────────────────────────
    photo_links: list[str] = []
    video_links: list[str] = []
    doc_links:   list[str] = []

    raw_hero = product.get("hero_media") or []
    raw_docs = product.get("documents")  or []

    # Fallback: reconstruct hero_media from flat photo_file_id if list is empty
    if not raw_hero:
        _fid = (product.get("photo_file_id")
                or product.get("file_id")
                or product.get("telegram_file_id"))
        if _fid:
            raw_hero = [{"type": "photo", "file_id": _fid}]
            logger.info("hero_media rebuilt from photo_file_id: %s", _fid)

    logger.info("Media inventory — hero_media=%d docs=%d", len(raw_hero), len(raw_docs))

    # Hero media
    for i, m in enumerate(raw_hero):
        fid = m.get("telegram_file_id") or m.get("file_id")
        if not fid:
            logger.warning("hero_media[%d] has no file_id — skipping", i)
            continue
        ftype = m.get("type", "photo")
        logger.info("Resolving hero_media[%d] — fid=%s type=%s", i, fid, ftype)
        try:
            url = await _get_tg_url(bot, fid)
            (photo_links if ftype == "photo" else video_links).append(url)
            logger.info("hero_media[%d] URL resolved OK", i)
        except Exception:
            logger.exception("hero_media[%d] URL resolution FAILED — skipping", i)

    # Documents
    for i, d in enumerate(raw_docs):
        fid = d.get("telegram_file_id")
        if not fid:
            logger.warning("documents[%d] has no telegram_file_id — skipping", i)
            continue
        logger.info("Resolving documents[%d] — fid=%s name=%s", i, fid, d.get("file_name"))
        try:
            url = await _get_tg_url(bot, fid)
            doc_links.append(url)
            logger.info("documents[%d] URL resolved OK", i)
        except Exception:
            logger.exception("documents[%d] URL resolution FAILED — skipping", i)

    logger.info("URLs resolved — photos=%d videos=%d docs=%d",
                len(photo_links), len(video_links), len(doc_links))

    # ── STEP 2: Write Sheets row ────────────────────────────────────────────────
    logger.info(">>> STEP 2: Sheets append")
    try:
        sheet_url = _append_row(
            product, settings, photo_links, video_links, doc_links, sheet_id,
        )
    except Exception:
        logger.exception("Sheets append FAILED — product=%s", pid)
        return {
            "status": "sheets_error",
            "error":  "Sheets append failed — see bot logs for full traceback",
        }

    logger.info(
        ">>> EXPORT COMPLETE — product=%s sheet=%s photos=%d videos=%d docs=%d",
        pid, sheet_url, len(photo_links), len(video_links), len(doc_links),
    )
    return {
        "status":    "ok",
        "sheet_url": sheet_url,
        "folder_url": "",
        "photos":    len(photo_links),
        "videos":    len(video_links),
        "docs":      len(doc_links),
    }
