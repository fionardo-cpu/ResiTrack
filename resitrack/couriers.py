"""Courier tracking providers.

Endpoints (researched from public GitHub projects, verified live Nov 2026):

* SPX  : GET  https://spx.co.id/shipment/order/open/order/get_order_info?spx_tn=<resi>&language_code=id
         (same endpoint used by github.com/mb26-dev/ha-kurir for SPX TH/MY/ID/...)
* Anteraja : POST https://api.anteraja.id/order/tracking  body [{"codes": "<resi>"}]
         headers mv: 1.2, source: aca_android   (public endpoint used by the AnterAja web/app)
* JNE / POS : both public websites (jne.co.id, posindonesia.co.id) are protected by
         Cloudflare Turnstile (anti-bot). We do NOT bypass that. Instead we use the
         BinderByte aggregator API (https://api.binderbyte.com/v1/track) which is what most
         Indonesian open-source trackers use (e.g. tegar-arsya/cekresi, shafarizkyf/cek-resi).
         BinderByte also works as fallback for SPX & Anteraja.
* Shopee Open Platform : v2.logistics.get_tracking_info works for ANY courier on a
         Shopee order (see shopee_api.py) - best option when you have API access.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Callable

import requests

from .models import (ST_DELIVERED, ST_FAILED, ST_OUT, ST_PENDING, ST_RETURNED, ST_TRANSIT, ST_UNKNOWN,
                     NotFound, TrackEvent, TrackingError, TrackResult, classify, is_delivered_event,
                     is_redelivery_event, is_rts_event)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 ResiTrack/1.0")
TIMEOUT = 20

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})


# --------------------------------------------------------------------------- helpers
def normalize_resi(value: str) -> str:
    cleaned = re.sub(r"[\s\-]", "", str(value or "")).upper()
    if cleaned.endswith(".0") and cleaned[:-2].isdigit():
        cleaned = cleaned[:-2]
    return cleaned


def detect_courier(resi: str, hint: str = "") -> str:
    """Detect courier from Shopee's 'Opsi Pengiriman' text or the resi shape."""
    h = (hint or "").lower()
    if "spx" in h or "shopee express" in h or "spx express" in h:
        return "SPX"
    if "jne" in h:
        return "JNE"
    if "anteraja" in h or "anter aja" in h:
        return "ANTERAJA"
    if "pos" in h or "pos indonesia" in h:
        return "POS"
    r = normalize_resi(resi)
    if r.startswith("SPX") or r.startswith("ID") and len(r) >= 14 and r[2:4].isdigit():
        return "SPX"
    if r.startswith("SHPE") or re.fullmatch(r"P\d{12,14}", r) or re.fullmatch(r"[A-Z]{2}\d{9}ID", r):
        return "POS"
    if r.startswith(("CM", "TLJ", "JT")) or re.fullmatch(r"(CGK|BDO|SUB|JOG|SRG|MES|DPS|UPG)[A-Z0-9]{8,}", r):
        return "JNE"
    if re.fullmatch(r"1\d{13}", r):          # Anteraja: 14 digits starting with 1
        return "ANTERAJA"
    if re.fullmatch(r"\d{12,16}", r):        # JNE numeric cnote
        return "JNE"
    return ""


def _fmt_ts(ts) -> str:
    try:
        val = int(ts)
        if val <= 0:
            return ""
        return datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _fmt_str(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%d-%m-%Y %H:%M",
                "%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%d %b %Y %H:%M"):
        try:
            return datetime.strptime(s[:26], fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return s


def _finish(result: TrackResult) -> TrackResult:
    if result.events:
        result.events.sort(key=lambda e: e.time, reverse=True)
        result.last_event = result.events[0].description
        result.last_time = result.events[0].time
        if result.status == ST_UNKNOWN:
            result.status = classify(result.last_event)
    elif result.status == ST_UNKNOWN:
        result.status = ST_PENDING
    return result


# --------------------------------------------------------------------------- SPX
def track_spx(resi: str) -> TrackResult:
    url = "https://spx.co.id/shipment/order/open/order/get_order_info"
    try:
        r = _session.get(url, params={"spx_tn": resi, "language_code": "id"}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise TrackingError(f"SPX koneksi gagal: {e}") from e
    if "json" not in r.headers.get("Content-Type", ""):
        raise TrackingError(f"SPX tidak membalas JSON (HTTP {r.status_code})")
    try:
        data = r.json()
    except ValueError as e:
        raise TrackingError(f"SPX mengembalikan JSON tidak valid (HTTP {r.status_code})") from e
    if data.get("retcode") != 0:
        raise NotFound(f"SPX: resi tidak ditemukan ({data.get('retcode')})")
    info = (data.get("data") or {}).get("sls_tracking_info") or {}
    records = sorted(
        (x for x in (info.get("records") or []) if x.get("display_flag", 1) == 1),
        key=lambda x: int(x.get("actual_time") or 0),
        reverse=True,
    )
    res = TrackResult(resi=resi, courier="SPX", source="SPX (spx.co.id)", raw=data)
    for rec in records:
        loc = ((rec.get("current_location") or {}).get("location_name") or "").strip()
        desc = (rec.get("buyer_description") or rec.get("description") or rec.get("tracking_name") or "").strip()
        res.events.append(TrackEvent(_fmt_ts(rec.get("actual_time")), desc, loc))
    if records:
        latest = records[0]
        milestone = (latest.get("milestone_name") or "").lower()
        try:
            milestone_code = int(latest.get("milestone_code") or 0)
        except (TypeError, ValueError):
            milestone_code = 0
        desc_low = (latest.get("buyer_description") or latest.get("description") or "").lower()
        text = f"{latest.get('tracking_name', '')} {desc_low}".lower()
        code = (latest.get("tracking_code") or "").upper()
        if (
            (
                milestone_code == 10
                or "unsuccessful" in milestone
                or is_rts_event(desc_low)
                or "return" in text
                or "retur" in text
            )
            and not is_redelivery_event(desc_low)
        ):
            res.status = ST_RETURNED
        elif (
            milestone == "delivered"
            or milestone_code == 7
            or is_delivered_event(desc_low)
        ):
            res.status = ST_DELIVERED
        elif is_redelivery_event(desc_low) or code == "F650":
            res.status = ST_OUT
        elif "ditolak pembeli" in desc_low or "fail" in text or "gagal" in text:
            res.status = ST_FAILED
        elif code == "F600" or milestone_code == 6 or "dalam proses pengantaran" in desc_low:
            res.status = ST_OUT
        elif code in ("F000",):
            res.status = ST_PENDING
        else:
            res.status = ST_TRANSIT
    return _finish(res)


# --------------------------------------------------------------------------- Anteraja
def track_anteraja(resi: str) -> TrackResult:
    url = "https://api.anteraja.id/order/tracking"
    headers = {"mv": "1.2", "source": "aca_android", "Content-Type": "application/json"}
    try:
        r = _session.post(url, json=[{"codes": resi}], headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise TrackingError(f"Anteraja koneksi gagal: {e}") from e
    try:
        data = r.json()
    except ValueError as e:
        raise TrackingError(f"Anteraja tidak membalas JSON (HTTP {r.status_code})") from e
    if data.get("status") != 200 or not data.get("content"):
        raise NotFound(f"Anteraja: {data.get('info') or 'resi tidak ditemukan'}")
    content = data["content"]
    item = content[0] if isinstance(content, list) else content
    res = TrackResult(resi=resi, courier="ANTERAJA", source="Anteraja (api.anteraja.id)", raw=data)
    history = sorted(
        item.get("history") or item.get("histories") or [],
        key=lambda h: _fmt_str(h.get("timestamp") or h.get("date") or ""),
        reverse=True,
    )
    for h in history:
        msg = h.get("message")
        if isinstance(msg, dict):
            msg = msg.get("id") or msg.get("en") or ""
        res.events.append(TrackEvent(_fmt_str(h.get("timestamp") or h.get("date") or ""), str(msg or ""),
                                     h.get("hub_name") or h.get("location") or ""))
    code = 0
    if history:
        try:
            code = int(history[0].get("tracking_code") or 0)
        except (TypeError, ValueError):
            code = 0
    if code == 250:
        res.status = ST_DELIVERED
    return _finish(res)


# --------------------------------------------------------------------------- ZPI (Open GitHub community aggregator)
ZPI_BASE_URL = "https://api.zpi.web.id/v1/logistics:cek-resi/track"
ZPI_API_KEY = "zpi_1z65ydb1u4uigwmdhptl1o1s8e"
ZPI_COURIERS = {
    "SPX": "shopee-express",
    "JNE": "jne",
    "POS": "pos-indonesia",
    "ANTERAJA": "anteraja",
}


def track_zpi(resi: str, courier: str) -> TrackResult:
    """Track via open logistics gateway (compatible with fk0u/cekresi)."""
    courier_code = ZPI_COURIERS.get(courier)
    if not courier_code:
        raise TrackingError(f"Kurir {courier} tidak didukung oleh gateway ZPI")
    try:
        r = _session.get(
            ZPI_BASE_URL,
            params={"awb": resi, "courier": courier_code},
            headers={"x-api-key": ZPI_API_KEY, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise TrackingError(f"Gateway ZPI koneksi gagal: {e}") from e

    if r.status_code == 404:
        raise NotFound(f"ZPI: Resi {resi} ({courier}) tidak ditemukan")
    if r.status_code >= 500:
        raise TrackingError(
            f"Gateway ZPI sedang gangguan sementara (HTTP {r.status_code}). "
            "Gunakan Shopee API atau isi API key BinderByte di Pengaturan."
        )
    if r.status_code != 200:
        raise TrackingError(f"Gateway ZPI HTTP {r.status_code}")

    try:
        raw_json = r.json()
    except ValueError as e:
        raise TrackingError(f"Gateway ZPI tidak membalas JSON (HTTP {r.status_code})") from e

    data = raw_json.get("data") or {}
    if not data or not data.get("ok", True):
        raise NotFound(f"ZPI: Resi {resi} ({courier}) tidak ditemukan")

    res = TrackResult(
        resi=resi,
        courier=courier,
        source=f"ZPI ({data.get('courierName') or courier})",
        raw=raw_json,
    )

    history = data.get("history") or []
    for h in history:
        date_str = _fmt_str(h.get("date") or "")
        desc = h.get("description") or ""
        loc = h.get("location") or ""
        res.events.append(TrackEvent(date_str, desc, loc))

    if data.get("delivered"):
        res.status = ST_DELIVERED
    elif data.get("status"):
        res.status = classify(data.get("status"))

    return _finish(res)


# --------------------------------------------------------------------------- BinderByte (JNE, POS, fallback)
BB_CODES = {"SPX": "spx", "JNE": "jne", "POS": "pos", "ANTERAJA": "anteraja"}


def track_binderbyte(resi: str, courier: str, api_key: str) -> TrackResult:
    if not api_key:
        raise TrackingError("API key BinderByte belum diisi (Pengaturan). Wajib untuk JNE & POS.")
    try:
        r = _session.get("https://api.binderbyte.com/v1/track",
                         params={"api_key": api_key, "courier": BB_CODES[courier], "awb": resi}, timeout=45)
    except requests.RequestException as e:
        raise TrackingError(f"BinderByte koneksi gagal: {e}") from e
    try:
        data = r.json()
    except ValueError as e:
        raise TrackingError(f"BinderByte tidak membalas JSON (HTTP {r.status_code})") from e
    if data.get("status") != 200:
        msg = data.get("message") or f"HTTP {r.status_code}"
        if r.status_code in (400, 404) or "not found" in str(msg).lower():
            raise NotFound(f"BinderByte: {msg}")
        raise TrackingError(f"BinderByte: {msg}")
    d = data.get("data") or {}
    summ = d.get("summary") or {}
    res = TrackResult(resi=resi, courier=courier, source="BinderByte", raw=data)
    for h in d.get("history") or []:
        res.events.append(TrackEvent(_fmt_str(h.get("date", "")), h.get("desc", ""), h.get("location", "")))
    st = (summ.get("status") or "").upper()
    if st == "DELIVERED":
        res.status = ST_DELIVERED
    elif "RETURN" in st:
        res.status = ST_RETURNED
    elif st in ("ON PROCESS", "ON_PROCESS") and not res.events:
        res.status = ST_PENDING
    return _finish(res)


# --------------------------------------------------------------------------- dispatcher
def track(resi: str, courier: str, settings: dict,
          shopee_tracker: Callable[[], TrackResult] | None = None) -> TrackResult:
    """Track one resi using the best available source, with fallbacks."""
    resi = normalize_resi(resi)
    if resi.startswith("ORDER-") and shopee_tracker is None:
        raise NotFound("Pesanan belum memiliki nomor resi.")
    courier = (courier or detect_courier(resi)).upper()
    errors: list[str] = []
    api_key = settings.get("binderbyte_api_key", "")

    chain: list[tuple[str, Callable[[], TrackResult]]] = []
    # 1. Shopee Open API (if order SN is known and authorized)
    if shopee_tracker is not None:
        chain.append(("Shopee API", shopee_tracker))

    # 2. Direct courier endpoints
    if courier == "SPX":
        chain.append(("SPX Direct", lambda: track_spx(resi)))
    elif courier == "ANTERAJA":
        chain.append(("Anteraja Direct", lambda: track_anteraja(resi)))

    # 3. Open GitHub community aggregator (ZPI: works for SPX, JNE, POS, Anteraja)
    if courier in ZPI_COURIERS:
        chain.append(("ZPI Gateway", lambda: track_zpi(resi, courier)))

    # 4. BinderByte API fallback (if user configured a custom key)
    if api_key and courier in BB_CODES and settings.get("use_binderbyte_fallback", True):
        chain.append(("BinderByte", lambda: track_binderbyte(resi, courier, api_key)))

    if not chain:
        raise TrackingError(f"Kurir tidak dikenali untuk resi {resi}. Isi kolom kurir secara manual.")

    for name, fn in chain:
        try:
            return fn()
        except TrackingError as e:
            errors.append(str(e))
            time.sleep(0.2)
    if all("tidak ditemukan" in e.lower() or "not found" in e.lower() for e in errors):
        raise NotFound(" | ".join(errors))
    raise TrackingError(" | ".join(errors))
