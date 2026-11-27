"""Shared data structures, status taxonomy, and intelligent status resolver."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

# ==============================================================================
# Status Taxonomy & Lifecycle Categories (Shopee Seller Operations Standard)
# ==============================================================================
ST_PENDING = "Menunggu Pickup"
ST_TRANSIT = "Dalam Perjalanan"
ST_OUT = "Sedang Diantar"
ST_DELIVERED = "Terkirim"
ST_COMPLETED = "Selesai"
ST_RTS_RETURN = "Pengembalian"
ST_RTS_REJECTED = "Gagal - Ditolak"
ST_RTS_BALSIS = "Gagal - Balsis"
ST_CANCELED_BUYER = "Canceled - Pembeli"
ST_CANCELED_SYSTEM = "Canceled - Sistem"
ST_CANCELED = ST_CANCELED_BUYER
ST_UNKNOWN = "Tidak Diketahui"
ST_ERROR = "Error"

# Aliases for backward compatibility
ST_FAILED = ST_RTS_REJECTED
ST_RETURNED = ST_RTS_RETURN

CAT_ACTIVE = "berjalan"
CAT_FINISHED = "selesai"
CAT_OTHER = "lainnya"
FILTER_DUPLICATE = "__DUPLICATE__"

RTS_STATUSES = {ST_RTS_BALSIS, ST_RTS_REJECTED, ST_RTS_RETURN}
CANCELED_STATUSES = {ST_CANCELED_BUYER, ST_CANCELED_SYSTEM}

STATUS_SHORT_LABELS = {
    ST_RTS_BALSIS: "Gagal - Balsis",
    ST_RTS_REJECTED: "Gagal - Ditolak",
    ST_COMPLETED: "Selesai",
    ST_CANCELED_BUYER: "Canceled - Pembeli",
    ST_CANCELED_SYSTEM: "Canceled - Sistem",
}

ALL_STATUSES = [
    ST_PENDING,
    ST_TRANSIT,
    ST_OUT,
    ST_DELIVERED,
    ST_COMPLETED,
    ST_RTS_RETURN,
    ST_RTS_REJECTED,
    ST_RTS_BALSIS,
    ST_CANCELED_BUYER,
    ST_CANCELED_SYSTEM,
    ST_UNKNOWN,
    ST_ERROR,
]

FINAL_STATUSES = {ST_COMPLETED, ST_RTS_RETURN, ST_RTS_REJECTED, ST_RTS_BALSIS, ST_CANCELED_BUYER, ST_CANCELED_SYSTEM}
COURIERS = ["SPX", "JNE", "POS", "ANTERAJA"]

_LEGACY_STATUS_MAP = {
    "Pengiriman Gagal - BalSis": ST_RTS_BALSIS,
    "Pengiriman Gagal - Balsis": ST_RTS_BALSIS,
    "Pengiriman Gagal - Ditolak Pembeli": ST_RTS_REJECTED,
    "Pengiriman Gagal - Ditolak": ST_RTS_REJECTED,
    "Canceled": ST_CANCELED_BUYER,
}

SYSTEM_CANCEL_KEYWORDS = (
    "oleh sistem",
    "otomatis oleh sistem",
    "dibatalkan secara otomatis oleh sistem",
    "oleh sistem shopee",
    "dibatalkan sistem",
    "aktivitas tidak wajar",
    "aktivitas kurang wajar",
    "tidak wajar",
    "kurang wajar",
    "unusual activity",
    "suspicious",
    "fraud",
    "dibatasi",
    "terdeteksi",
    "pelanggaran",
    "payment_expired",
    "invoice_overdue",
    "fulfillment_expired",
    "pembayaran belum",
    "melewati batas waktu",
    "system cancel",
    "cancelled by system",
    "canceled by system",
    "auto cancel",
)

FAILED_DELIVERY_CANCEL_KEYWORDS = (
    "pengiriman gagal",
    "gagal kirim",
    "delivery failed",
    "failed delivery",
    "undeliverable",
)

DELIVERED_KEYWORDS = (
    "delivered",
    "terkirim",
    "diterima oleh",
    "delivery sukses",
    "received by",
    "sukses diterima",
    "telah diterima",
    "paket telah sampai",
    "tiba di alamat tujuan",
    "berhasil dikirim",
    "paket diterima",
)

_STRONG_DELIVERED_KEYWORDS = (
    "tiba di alamat tujuan",
    "delivery sukses",
    "sukses diterima",
    "paket telah sampai",
)

_FACILITY_RECEIPT_GUARD = (
    "drop point",
    "droppoint",
    "drop-point",
    "mitra anteraja",
    "agen ",
    "agen/",
    "agent",
    "counter",
    "konter",
    "gudang",
    "sortir",
    "sorting",
    "staging",
    "warehouse",
    "hub",
    "gerai",
    "outlet",
    "cabang",
    "kantor pos",
    "loket",
    "pickup",
    "pick up",
    "penjemputan",
    "pihak jasa kirim",
    "pihak ekspedisi",
    "oleh kurir",
    "oleh agen",
    "oleh mitra",
    "oleh ekspedisi",
    "received at",
    "facility",
    "origin",
    "transit",
    "stasiun",
    "gateway",
    "pusat sortir",
)

_DELIVERED_NEGATION_GUARD = (
    "belum terkirim",
    "belum diterima",
    "tidak terkirim",
    "gagal kirim",
    "gagal antar",
)

_RETURN_RECIPIENT_GUARD = (
    "penjual",
    "pengirim",
    "seller",
    "sender",
    "dikembalikan",
    "diterima kembali",
    "return",
    "retur",
)


def is_delivered_event(text: str) -> bool:
    """Return True only when an event description indicates buyer/destination delivery (never drop-off/hub/seller receipts)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(n in t for n in _DELIVERED_NEGATION_GUARD):
        return False
    if any(r in t for r in _RETURN_RECIPIENT_GUARD):
        return False
    if any(k in t for k in _STRONG_DELIVERED_KEYWORDS):
        return True
    if any(k in t for k in DELIVERED_KEYWORDS) and not any(g in t for g in _FACILITY_RECEIPT_GUARD):
        return True
    return False

RETURN_COMPLETED_KEYWORDS = (
    "pesanan dikembalikan ke agen / pengirim",
    "pesanan dikembalikan ke agen/pengirim",
    "dikembalikan ke agen / pengirim",
    "dikembalikan ke agen/pengirim",
    "pesanan dikembalikan ke agen",
    "paket dikembalikan ke agen",
    "pesanan dikembalikan ke pengirim",
    "paket dikembalikan ke pengirim",
    "returned to sender",
    "berhasil dikembalikan",
    "dikembalikan ke pengirim berhasil",
    "diterima oleh penjual",
    "diterima kembali oleh penjual",
    "diterima pengirim",
    "returned to seller",
    "returned to sender successfully",
    "return delivered",
    "paket telah dikembalikan ke penjual",
    "paket telah dikembalikan kepada penjual",
    "paket telah dikembalikan ke pengirim",
    "telah dikembalikan ke pengirim",
    "telah diterima penjual",
    "telah diterima pengirim",
    "diterima kembali",
    "paket kembali diterima",
    "paket berhasil dikembalikan",
    "pengembalian berhasil",
    "pengembalian selesai",
    "retur selesai",
    "return completed",
    "berhasil diterima penjual",
    "dikembalikan dan diterima",
)

_RETURN_IN_PROGRESS_GUARD = (
    "sedang dikembalikan",
    "akan dikembalikan",
    "untuk dikembalikan",
    "dalam proses",
    "(rts)",
)


def merge_products(existing: str, incoming: str) -> str:
    """Merge '; '-separated product strings, grouping different variants under one product name."""
    raw_parts: list[str] = []
    for src in (existing, incoming):
        for part in (src or "").split(";"):
            p = part.strip()
            if p:
                raw_parts.append(p)
    if not raw_parts:
        return ""

    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for part in raw_parts:
        m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", part)
        if m and m.group(1).strip():
            base = m.group(1).strip()
            inner = m.group(2).strip()
            variants = [v.strip() for v in inner.split(" · ") if v.strip()]
        else:
            base = part
            variants = []

        key = base.casefold()
        if key not in groups:
            groups[key] = {"base": base, "variants": []}
            order.append(key)
        vlist = groups[key]["variants"]
        seen_v = {v.casefold() for v in vlist}
        for v in variants:
            if v.casefold() not in seen_v:
                vlist.append(v)
                seen_v.add(v.casefold())

    out: list[str] = []
    for k in order:
        g = groups[k]
        if g["variants"]:
            out.append(f"{g['base']} ({' · '.join(g['variants'])})")
        else:
            out.append(g["base"])
    return "; ".join(out)


@dataclass
class TrackEvent:
    time: str          # "YYYY-MM-DD HH:MM"
    description: str
    location: str = ""


@dataclass
class TrackResult:
    resi: str
    courier: str
    status: str = ST_UNKNOWN
    last_event: str = ""
    last_time: str = ""
    source: str = ""
    events: list[TrackEvent] = field(default_factory=list)
    raw: Any = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


class TrackingError(Exception):
    """Provider failed (network, blocked, bad response)."""


class NotFound(TrackingError):
    """Provider does not know this resi (yet)."""


# ==============================================================================
# Helper classifiers for events
# ==============================================================================
DELIVERY_ATTEMPT_KEYWORDS = (
    "out for delivery", "sedang diantar", "diantar ke alamat", "driver assigned",
    "kurir sedang mengantar", "dibawa kurir", "with delivery courier", "pengantaran oleh",
    "dalam proses pengantaran", "kurir sudah ditugaskan. pesanan akan dikirim",
    "pesanan ditolak pembeli", "pengiriman ditunda karena",
    "pembeli tidak ditempat", "pembeli menjadwalkan ulang",
    "on delivery", "delivery attempt", "penerima menolak", "ditolak pembeli", "cod ditolak",
    "alamat tidak ditemukan", "rumah kosong", "pembeli menolak", "gagal serah terima",
    "delivery ticket creation", "delivering", "mengantarkan paket", "kurir ditugaskan",
    "assigned to courier", "satria sudah ditugaskan", "akan segera diantar ke penerima",
    "diantar ke penerima",
)

OUT_FOR_DELIVERY_KEYWORDS = (
    "out for delivery",
    "sedang diantar",
    "dibawa kurir",
    "with delivery courier",
    "kurir sedang",
    "dalam proses pengantaran",
    "akan dikirim ke alamat",
    "proses antar",
    "delivery courier",
    "satria sudah ditugaskan",
    "akan segera diantar ke penerima",
    "diantar ke penerima",
)

RTS_KEYWORDS = (
    "return to sender", "rts", "kembali ke penjual", "dikembalikan ke pengirim",
    "dikembalikan ke penjual", "akan dikembalikan", "sedang dikembalikan", "untuk dikembalikan",
    "retur", "returning to seller", "returned to sender", "paket kembali",
    "kembali ke pengirim", "batal sistem", "dibatalkan sistem", "balsis",
    "pengembalian pesanan", "dikembalikan ke agen",
)

REDELIVERY_KEYWORDS = (
    "akan dikirim ulang",
    "pengiriman ulang",
    "pengantaran ulang",
    "akan diantar ulang",
    "akan dilakukan kembali",
    "dijadwalkan ulang",
    "menjadwalkan ulang",
    "percobaan pengantaran",
    "redelivery",
    "re-attempt",
    "pengiriman ditunda",
)

_RTS_WORD_RE = re.compile(r"\brts\b")


def is_rts_event(text: str) -> bool:
    """Return True when tracking text explicitly indicates returning to seller/sender."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if _RTS_WORD_RE.search(t):
        return True
    return any(w in t for w in RTS_KEYWORDS if w != "rts")


def is_redelivery_event(text: str) -> bool:
    """Return True when delivery failed/postponed temporarily and courier is rescheduling/redelivering (not RTS)."""
    t = (text or "").strip().lower()
    if not t or is_rts_event(t):
        return False
    return any(k in t for k in REDELIVERY_KEYWORDS)

FAILURE_KEYWORDS = (
    "gagal", "fail", "unsuccessful", "tidak berhasil", "bermasalah", "problem",
    "delivery marker", "difficulty handover", "missroute", "rusak", "hilang", "lost", "damage"
)

PRE_PICKUP_KEYWORDS = (
    "menunggu pesanan diserahkan",
    "menunggu pickup",
    "waiting for pickup",
    "belum di pickup",
    "belum dipickup",
    "pengirim telah mengatur pengiriman",
    "seller has arranged shipment",
    "sender is preparing to ship",
    "preparing to ship",
    "label created",
    "order created",
    "pesanan dibuat",
    "manifested",
    "slstn created",
    "pickup requested",
    "permintaan pickup",
    "shipment data received",
    "data pengiriman diterima",
    "ready to ship",
    "menunggu kurir",
)


def classify(text: str) -> str:
    """Guess a basic status from free text (ID/EN)."""
    t = (text or "").lower()
    if is_rts_event(t):
        return "RTS"
    if is_delivered_event(t):
        return ST_DELIVERED
    if is_redelivery_event(t):
        return ST_OUT
    if any(w in t for w in FAILURE_KEYWORDS):
        return "GAGAL"
    if any(w in t for w in OUT_FOR_DELIVERY_KEYWORDS):
        return ST_OUT
    if any(w in t for w in PRE_PICKUP_KEYWORDS):
        return ST_PENDING
    if t:
        return ST_TRANSIT
    return ST_UNKNOWN


def resolve_overall_status(
    order_status: str,
    tracking_status: str,
    events: list[TrackEvent] | list[dict],
    cancel_by: str = "",
    cancel_reason: str = "",
) -> str:
    """Intelligently combine Shopee Order Status (lacak pesanan) and Tracking History (lacak resi).

    Special handling:
      - Selesai: Dana sudah cair / status pesanan di Shopee selesai.
      - RTS -> Pengembalian vs Gagal - Ditolak vs Gagal - Balsis.
      - Balsis: Pesanan belum sampai pembeli, kurir belum pernah ditugaskan mengantar
                (hanya scan gudang sortir/transit), tapi sudah RTS.
      - Ditolak: Kurir sudah pernah ditugaskan mengantar ke pintu pembeli.
      - Canceled -> Canceled - Pembeli (dengan alasan) vs Canceled - Sistem (aktivitas kurang wajar / otomatis sistem).
    """
    ord_st = (order_status or "").strip().lower()
    trk_st = (tracking_status or "").strip()
    if trk_st not in ("Canceled", ST_CANCELED_BUYER, ST_CANCELED_SYSTEM):
        trk_st = _LEGACY_STATUS_MAP.get(trk_st, trk_st)

    # 1. Check if Shopee order is marked COMPLETED / SELESAI (Dana sudah dicairkan)
    # Exclude return/cancel/failure completions so "Pengembalian Selesai" stays ST_RTS_RETURN.
    if any(s in ord_st for s in ("selesai", "completed")) and not any(
        x in ord_st for x in ("retur", "return", "pengembalian", "refund", "gagal", "batal", "cancel")
    ):
        return ST_COMPLETED

    # Normalize events into text list (description + location for RTS/cancel, description-only for delivery checks)
    event_texts: list[str] = []
    event_descs: list[str] = []
    for e in events or []:
        if isinstance(e, TrackEvent):
            desc = (e.description or "").lower()
            loc = (e.location or "").lower()
        elif isinstance(e, dict):
            desc = str(e.get("description", "") or "").lower()
            loc = str(e.get("location", "") or "").lower()
        else:
            continue
        event_descs.append(desc)
        event_texts.append(f"{desc} {loc}".strip())

    all_event_history_text = " ".join(event_texts)
    latest_desc = event_descs[0] if event_descs else ""

    # 2. Check for formal Shopee return/refund request
    if any(w in ord_st for w in ("pengembalian", "return", "refund", "to_return")):
        return ST_RTS_RETURN
    if any(w in all_event_history_text for w in ("retur barang pembeli", "pengembalian pembeli", "komplain retur")):
        return ST_RTS_RETURN

    # 2a. Buyer delivery always wins over earlier failed/redelivery/RTS attempts
    if event_descs and is_delivered_event(latest_desc):
        return ST_DELIVERED

    # 2b. Check for RTS (Return to Sender), respecting chronology vs redelivery
    rts_idx = next((i for i, ev in enumerate(event_texts) if is_rts_event(ev)), None)
    if event_texts:
        if rts_idx is not None:
            has_newer_forward = any(
                is_redelivery_event(event_descs[i])
                or any(k in event_descs[i] for k in OUT_FOR_DELIVERY_KEYWORDS)
                for i in range(rts_idx)
            )
            is_rts = not has_newer_forward
        else:
            is_rts = (
                trk_st in (ST_RTS_BALSIS, ST_RTS_RETURN, "RTS", "Retur", "RETUR")
                and not is_redelivery_event(latest_desc)
                and not any(k in latest_desc for k in OUT_FOR_DELIVERY_KEYWORDS)
            )
    else:
        is_rts = trk_st in (ST_RTS_BALSIS, ST_RTS_REJECTED, ST_RTS_RETURN, "RTS", "Retur", "RETUR", "Gagal Antar", "GAGAL")

    if is_rts:
        if not event_texts and trk_st in (ST_RTS_RETURN, ST_RTS_REJECTED, ST_RTS_BALSIS):
            return trk_st

        # It is Pengiriman Gagal! Now differentiate: Ditolak Pembeli vs Balsis (Batal Sistem)
        # Check if courier was EVER dispatched/assigned to deliver to buyer door
        has_delivery_attempt = any(
            any(k in ev for k in DELIVERY_ATTEMPT_KEYWORDS)
            for ev in event_texts
        )

        if has_delivery_attempt:
            return ST_RTS_REJECTED
        else:
            # Package was only in warehouse / sorting hub / transit, never dispatched to buyer!
            return ST_RTS_BALSIS

    # 3. Check for Canceled order (Buyer vs System)
    if (
        any(s in ord_st for s in ("batal", "cancel", "cancelled", "canceled", "dibatalkan"))
        or trk_st in (ST_CANCELED_BUYER, ST_CANCELED_SYSTEM, "Canceled")
    ):
        cr = (cancel_reason or "").strip().lower()
        if not event_texts and any(k in cr for k in FAILED_DELIVERY_CANCEL_KEYWORDS):
            return ST_TRANSIT

        # Return Canceled if tracking never progressed past pre-pickup / cancellation events
        if (
            not event_texts
            or trk_st in (ST_PENDING, ST_UNKNOWN, ST_CANCELED_BUYER, ST_CANCELED_SYSTEM, "Canceled", "")
            or all(
                any(
                    k in ev
                    for k in ("menunggu", "waiting", "created", "label", "cancel", "batal", "dibuat", "mengatur pengiriman")
                )
                for ev in event_texts
            )
        ):
            cb = (cancel_by or "").strip().lower()
            combined_cancel = f"{ord_st} {cb} {cr} {all_event_history_text}"
            if (
                cb in ("system", "ops", "sistem", "shopee")
                or trk_st == ST_CANCELED_SYSTEM
                or any(k in combined_cancel for k in SYSTEM_CANCEL_KEYWORDS)
            ):
                return ST_CANCELED_SYSTEM
            return ST_CANCELED_BUYER

    # 4. Standard shipping progression
    latest_desc = event_descs[0] if event_descs else ""
    delivered_by_trk = (
        trk_st == ST_DELIVERED
        and (
            not latest_desc
            or is_delivered_event(latest_desc)
            or not any(g in latest_desc for g in _FACILITY_RECEIPT_GUARD)
        )
    )
    if delivered_by_trk or any(is_delivered_event(ev) for ev in event_descs[:1]):
        # Delivered to buyer, but Shopee order status is not yet "Selesai" (dana belum cair)
        return ST_DELIVERED

    if (
        trk_st == ST_OUT
        or is_redelivery_event(latest_desc)
        or any(k in latest_desc for k in OUT_FOR_DELIVERY_KEYWORDS)
    ):
        return ST_OUT

    if event_texts and all(any(k in ev for k in PRE_PICKUP_KEYWORDS) for ev in event_texts):
        return ST_PENDING

    if trk_st == ST_PENDING and not event_texts:
        return ST_PENDING

    if trk_st == ST_TRANSIT or event_texts:
        return ST_TRANSIT

    # Active orders exported from Shopee that have already been dispatched
    if any(s in ord_st for s in ("sedang dikirim", "telah dikirim", "shipped", "in_transit")):
        return ST_TRANSIT

    if any(
        s in ord_st
        for s in (
            "perlu dikirim", "sedang dikemas", "to_ship", "ready_to_ship",
            "processed", "retry_ship", "belum dikirim", "belum bayar", "unpaid",
        )
    ):
        return ST_PENDING

    return trk_st or ST_PENDING


def effective_status(row: dict) -> str:
    """Resolve effective status consistently across table delegates, filters, sidebar, and inspector."""
    raw_st = (row.get("status") or "").strip()
    raw_st = _LEGACY_STATUS_MAP.get(raw_st, raw_st)
    if raw_st not in FINAL_STATUSES and row.get("error") and not row.get("last_event"):
        return ST_ERROR
    return raw_st or ST_UNKNOWN


def resolve_lifecycle(
    status: str,
    order_status: str = "",
    events: list[TrackEvent] | list[dict] | None = None,
) -> str:
    """Automatically classify an order into CAT_ACTIVE ('berjalan'), CAT_FINISHED ('selesai'), or CAT_OTHER ('lainnya')."""
    st = _LEGACY_STATUS_MAP.get((status or "").strip(), (status or "").strip())
    if st in (ST_COMPLETED, ST_CANCELED_BUYER, ST_CANCELED_SYSTEM):
        return CAT_FINISHED

    if st in (ST_ERROR, ST_UNKNOWN):
        return CAT_OTHER

    if st in RTS_STATUSES:
        event_texts: list[str] = []
        for e in events or []:
            if isinstance(e, TrackEvent):
                event_texts.append(f"{e.description} {e.location}".lower())
            elif isinstance(e, dict):
                event_texts.append(f"{e.get('description', '')} {e.get('location', '')}".lower())

        for ev in event_texts:
            if any(k in ev for k in RETURN_COMPLETED_KEYWORDS) and not any(g in ev for g in _RETURN_IN_PROGRESS_GUARD):
                return CAT_FINISHED

        ord_st = (order_status or "").strip().lower()
        if any(
            k in ord_st
            for k in (
                "banding", "dispute", "sedang dikembalikan", "diproses", "to_return",
                "in_cancel", "sedang dikirim", "telah dikirim", "shipped", "perlu dikirim", "pending",
            )
        ):
            return CAT_ACTIVE

        if any(
            k in ord_st
            for k in (
                "pengembalian selesai", "retur selesai", "return_completed", "selesai dikembalikan",
                "pengiriman gagal selesai", "gagal selesai", "refund selesai", "dibatalkan selesai",
            )
        ):
            return CAT_FINISHED

        return CAT_ACTIVE

    return CAT_ACTIVE


def row_lifecycle(row: dict) -> str:
    """Compute CAT_ACTIVE / CAT_FINISHED purely from real tracking & order state."""
    try:
        events = json.loads(row.get("events_json") or "[]")
    except Exception:
        events = []
    return resolve_lifecycle(
        effective_status(row),
        row.get("order_status") or "",
        events,
    )


def parse_idr(val) -> int:
    """Parse IDR currency strings ('100.500', 'Rp 87.000', '87000.00', '7.288', '1010') into non-negative int."""
    if val is None or isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        return max(0, int(round(float(val))))
    s = str(val).strip()
    if not s or s.upper() in ("N", "-", "NONE", "NULL", "NAN"):
        return 0
    s = re.sub(r"(?i)rp\.?", "", s).strip().replace(" ", "")
    if not s:
        return 0
    # Indonesian decimal format like "87.000,00"
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").split(",", 1)[0]
        else:
            s = s.replace(",", "").split(".", 1)[0]
    elif "." in s:
        parts = s.split(".")
        if all(p.isdigit() for p in parts) and len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
            s = "".join(parts)
        elif re.fullmatch(r"\d+\.\d{1,2}", s):
            try:
                return max(0, int(round(float(s))))
            except ValueError:
                return 0
        else:
            s = re.sub(r"[^\d]", "", s)
    elif "," in s:
        parts = s.split(",")
        if all(p.isdigit() for p in parts) and len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
            s = "".join(parts)
        elif re.fullmatch(r"\d+,\d{1,2}", s):
            try:
                return max(0, int(round(float(s.replace(",", ".")))))
            except ValueError:
                return 0
        else:
            s = re.sub(r"[^\d]", "", s)
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return 0
    try:
        return max(0, int(digits))
    except ValueError:
        return 0


def format_idr(amount: int) -> str:
    """Format integer Rupiah amount like 469700 -> 'Rp469.700'."""
    try:
        n = int(amount or 0)
    except (TypeError, ValueError):
        n = 0
    if n < 0:
        return f"-Rp{abs(n):,}".replace(",", ".")
    return f"Rp{n:,}".replace(",", ".")


def is_cod_payment(method: str) -> bool:
    m = (method or "").strip().lower()
    return any(k in m for k in ("cod", "bayar di tempat", "cash on delivery"))


def payment_short_label(method: str) -> str:
    """Short badge label for payment method ('COD', 'QRIS', 'ShopeePay', etc.)."""
    raw = (method or "").strip()
    if not raw:
        return ""
    m = raw.lower()
    if is_cod_payment(raw):
        return "COD"
    if "qris" in m:
        return "QRIS"
    if "shopeepay" in m:
        return "ShopeePay"
    if "spaylater" in m:
        return "SPayLater"
    if "transfer" in m or "bank" in m or re.search(r"\bva\b", m):
        return "Transfer"
    if "kartu" in m or "credit" in m or "debit" in m:
        return "Kartu"
    if "online" in m:
        return "Online"
    return raw[:12]


def payment_details_dict(row: dict) -> dict[str, int]:
    """Parse payment_details_json cleanly into {key: int_amount}."""
    raw = (row or {}).get("payment_details_json") or ""
    if not raw or raw == "{}":
        return {}
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            return {str(k): int(v) for k, v in d.items() if v is not None}
    except Exception:
        pass
    return {}


def is_masked_blank(val: str) -> bool:
    """Return True if value is empty, '-', or purely non-alphanumeric masking like '****'."""
    s = (val or "").strip()
    if not s or s == "-":
        return True
    return not any(ch.isalnum() for ch in s)


def recipient_dup_key(row: dict) -> str:
    """Normalized key for detecting orders sharing the same recipient name & phone (with city guard for masked PII)."""
    raw_name = (row.get("recipient_name") or "").strip()
    raw_phone_in = (row.get("recipient_phone") or "").strip()
    if is_masked_blank(raw_name) or is_masked_blank(raw_phone_in):
        return ""
    name = re.sub(r"\s+", " ", raw_name).lower()
    raw_phone = re.sub(r"[\s\-+()]", "", raw_phone_in).lower()
    if "*" not in name and "*" not in raw_phone:
        digits = re.sub(r"\D", "", raw_phone)
        if digits.startswith("62"):
            digits = "0" + digits[2:]
        return f"{name}|{digits or raw_phone}"
    raw_city = (row.get("city") or "").strip()
    city = "" if is_masked_blank(raw_city) else re.sub(r"\s+", " ", raw_city).lower()
    return f"{name}|{raw_phone}|{city}"


def compute_duplicate_groups(rows: list[dict]) -> dict[str, list[dict]]:
    """Group orders sharing the same recipient_dup_key (size >= 2)."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = recipient_dup_key(r)
        if not k:
            continue
        groups.setdefault(k, []).append(r)
    return {k: grp for k, grp in groups.items() if len(grp) >= 2}


def duplicate_resi_map(rows: list[dict]) -> dict[str, list[dict]]:
    """Map each duplicate resi -> full list of peer rows sharing that recipient."""
    out: dict[str, list[dict]] = {}
    for grp in compute_duplicate_groups(rows).values():
        for r in grp:
            resi = r.get("resi") or ""
            if resi:
                out[resi] = grp
    return out


_MONTHS_ID = ("", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")

_DATE_WITH_TIME_FMTS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%S",
)

_DATE_ONLY_FMTS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
)


def parse_date_str(val: str) -> str:
    """Normalize date/datetime strings into 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'."""
    s = str(val or "").strip()
    if not s or s.upper() in ("-", "NONE", "NULL", "NAN", "NAT"):
        return ""
    s_clean = re.sub(r"\.\d+(Z|[+-]\d{2}:?\d{2})?$", "", s).rstrip("Z")
    for fmt in _DATE_WITH_TIME_FMTS:
        try:
            dt = datetime.strptime(s_clean[:19], fmt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    head = s_clean.split()[0].split("T")[0]
    for fmt in _DATE_ONLY_FMTS:
        try:
            dt = datetime.strptime(head[:10], fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def infer_date_from_order_sn(order_sn: str) -> str:
    """Infer 'YYYY-MM-DD' from Shopee order_sn YYMMDD prefix (e.g., '261127ABC...' -> '2026-11-27')."""
    sn = (order_sn or "").strip().upper()
    m = re.match(r"^(\d{2})(\d{2})(\d{2})[A-Z0-9]{6,}$", sn)
    if not m:
        return ""
    yy, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        d = date(2000 + yy, mm, dd)
        return d.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def row_order_date(row: dict) -> date | None:
    """Return canonical order date using stored order_date -> order_sn YYMMDD -> created_at -> last_event_time."""
    if not isinstance(row, dict):
        return None
    candidates = (
        row.get("order_date") or "",
        infer_date_from_order_sn(row.get("order_sn") or ""),
        row.get("created_at") or "",
        row.get("last_event_time") or "",
    )
    for raw in candidates:
        s = str(raw or "").strip()
        if not s:
            continue
        norm = parse_date_str(s) or s
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", norm)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


def format_date_short(d: date) -> str:
    """Format date cleanly in Indonesian (e.g., '27 Nov 2026')."""
    return f"{d.day:02d} {_MONTHS_ID[d.month]} {d.year}"


def resolve_date_range(
    preset: str,
    custom_from: date | None = None,
    custom_to: date | None = None,
    today: date | None = None,
) -> tuple[date | None, date | None]:
    """Resolve date filter preset ('today'|'yesterday'|'this_week'|'last_7d'|'this_month'|'last_month'|'custom') -> (start, end)."""
    ref = today or date.today()
    p = (preset or "").strip().lower()
    if not p or p == "all":
        return (None, None)
    if p == "today":
        return (ref, ref)
    if p == "yesterday":
        y = ref - timedelta(days=1)
        return (y, y)
    if p == "this_week":
        return (ref - timedelta(days=ref.weekday()), ref)
    if p == "last_7d":
        return (ref - timedelta(days=6), ref)
    if p == "this_month":
        return (ref.replace(day=1), ref)
    if p == "last_month":
        last_prev = ref.replace(day=1) - timedelta(days=1)
        return (last_prev.replace(day=1), last_prev)
    if p == "custom":
        d1, d2 = custom_from, custom_to
        if d1 is not None and d2 is not None and d1 > d2:
            d1, d2 = d2, d1
        return (d1, d2)
    return (None, None)
