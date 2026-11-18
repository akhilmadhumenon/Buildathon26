"""Live Agmarknet date-wise prices for the ten configured crops.

The public report at api.agmarknet.gov.in asks for a short check image on
each commodity-month. macOS reads it with Vision. A Linux server uses
Tesseract when that program is installed.
"""

from __future__ import annotations

import base64
import json
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
API = "https://api.agmarknet.gov.in/v1"
STATE_ID = 16
CTX = ssl._create_unverified_context()

# Commodity ids from the Agmarknet daily-price filters.
COMMODITIES = {
    "tomato": 65,
    "onion": 23,
    "potato": 24,
    "chilli": 113,
    "banana": 19,
    "ragi": 30,
    "maize": 4,
    "tur": 45,
    "paddy": 2,
    "groundnut": 10,
}

_vision_ready = False


def month_span(today: date | None = None) -> list[tuple[int, int]]:
    """January 2025 through the open month. That is enough for last year's week."""
    today = today or date.today()
    months = []
    year, month = 2025, 1
    while (year, month) <= (today.year, today.month):
        months.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return months


def pending_jobs(refresh_open: bool, today: date | None = None) -> list[tuple[str, int, int]]:
    today = today or date.today()
    open_month = (today.year, today.month)
    jobs = []
    for crop in COMMODITIES:
        for year, month in month_span(today):
            target = RAW / f"{crop}_{year}_{month:02d}.json"
            cached = target.exists() and target.stat().st_size > 200
            if cached and not (refresh_open and (year, month) == open_month):
                continue
            jobs.append((crop, year, month))
    return jobs


def reader_available() -> bool:
    if _vision_importable():
        return True
    return shutil.which("tesseract") is not None


def _vision_importable() -> bool:
    try:
        import objc  # noqa: F401
        from Foundation import NSURL  # noqa: F401
    except Exception:
        return False
    return True


def _request(url: str, data: bytes | None = None, timeout: int = 60) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as response:
        return response.read()


def _ocr_vision(path: Path) -> str | None:
    global _vision_ready
    if not _vision_importable():
        return None
    import objc
    from Foundation import NSURL

    if not _vision_ready:
        objc.loadBundle("Vision", globals(), bundle_path="/System/Library/Frameworks/Vision.framework")
        _vision_ready = True
    url = NSURL.fileURLWithPath_(str(path))
    request = VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)
    request.setUsesLanguageCorrection_(False)
    handler = VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    handler.performRequests_error_([request], None)
    results = request.results() or []
    if not results:
        return ""
    text = results[0].topCandidates_(1)[0].string()
    return "".join(text.split())


def _ocr_tesseract(image: bytes) -> str | None:
    if shutil.which("tesseract") is None:
        return None
    try:
        completed = subprocess.run(
            [
                "tesseract",
                "stdin",
                "stdout",
                "--psm",
                "7",
                "-c",
                "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
            ],
            input=image,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = completed.stdout.decode("utf-8", "replace")
    return "".join(text.split())


def solve_captcha() -> tuple[str, str]:
    payload = _request(f"{API}/captcha/generator", data=b"{}")
    body = json.loads(payload)
    RAW.mkdir(parents=True, exist_ok=True)
    image = base64.b64decode(body["captcha_image"])
    image_path = RAW / "_captcha.png"
    image_path.write_bytes(image)
    text = _ocr_vision(image_path)
    if not text:
        text = _ocr_tesseract(image) or ""
    return body["captcha_key"], text


def fetch_month(crop: str, year: int, month: int) -> dict:
    if crop not in COMMODITIES:
        raise RuntimeError(f"Unknown crop {crop}.")
    if not reader_available():
        raise RuntimeError("captcha_reader")
    commodity_id = COMMODITIES[crop]
    last_error = "no attempt"
    for _ in range(12):
        key, text = solve_captcha()
        if len(text) < 4 or len(text) > 8:
            last_error = f"ocr:{text!r}"
            continue
        query = urllib.parse.urlencode(
            {
                "year": year,
                "month": month,
                "includeExcel": "false",
                "stateId": STATE_ID,
                "commodityId": commodity_id,
                "captcha": text,
                "captcha_key": key,
            }
        )
        url = f"{API}/prices-and-arrivals/date-wise/specific-commodity?{query}"
        try:
            raw = _request(url, timeout=90)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:240]
            last_error = f"HTTP {exc.code}: {detail}"
            time.sleep(25 if "Too many requests" in detail else 1.5)
            continue
        except Exception as exc:  # noqa: BLE001 - network errors are retried
            last_error = str(exc)
            time.sleep(1)
            continue
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            last_error = raw[:180].decode("utf-8", "replace")
            continue
        if body.get("success") and body.get("markets") is not None:
            return body
        last_error = str(body.get("detail") or body.get("message") or str(body)[:180])
        time.sleep(0.4)
    raise RuntimeError(f"{crop} {year}-{month:02d}: {last_error}")


def fetch_pending(refresh_open: bool, progress=None, pause: float = 2.5) -> dict:
    """Save each missing month. The open month is fetched again when refresh_open is set."""
    if not reader_available():
        raise RuntimeError("captcha_reader")
    jobs = pending_jobs(refresh_open=refresh_open)
    fetched = []
    failed = []
    total = len(jobs)
    for index, (crop, year, month) in enumerate(jobs, start=1):
        if progress:
            progress({"done": index - 1, "total": total, "crop": crop, "year": year, "month": month})
        try:
            body = fetch_month(crop, year, month)
            target = RAW / f"{crop}_{year}_{month:02d}.json"
            target.write_text(json.dumps(body))
            fetched.append(
                {
                    "crop": crop,
                    "year": year,
                    "month": month,
                    "markets": len(body.get("markets") or []),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one crop should not stop the rest
            failed.append({"crop": crop, "year": year, "month": month, "detail": str(exc)[:180]})
        if index < total and pause:
            time.sleep(pause)
    if progress:
        progress({"done": total, "total": total, "crop": None, "year": None, "month": None})
    return {"fetched": fetched, "failed": failed}
