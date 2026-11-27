"""SQLite storage for orders/shipments with duplicate order/resi prevention."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any

from . import config
from .couriers import normalize_resi
from .models import (
    CAT_ACTIVE,
    CAT_OTHER,
    ST_CANCELED_BUYER,
    ST_CANCELED_SYSTEM,
    ST_DELIVERED,
    ST_PENDING,
    ST_RTS_BALSIS,
    ST_RTS_REJECTED,
    ST_RTS_RETURN,
    ST_TRANSIT,
    infer_date_from_order_sn,
    is_masked_blank,
    merge_products,
    resolve_overall_status,
    row_lifecycle,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    resi               TEXT NOT NULL UNIQUE,
    courier            TEXT NOT NULL DEFAULT '',
    order_sn           TEXT DEFAULT '',          -- No. Pesanan Shopee
    buyer_username     TEXT DEFAULT '',          -- Username (Pembeli) Shopee
    recipient_name     TEXT DEFAULT '',
    recipient_phone    TEXT DEFAULT '',
    city               TEXT DEFAULT '',
    product            TEXT DEFAULT '',
    order_source       TEXT DEFAULT 'manual',    -- manual | csv | shopee_api
    order_status       TEXT DEFAULT '',          -- Status Pesanan Shopee (Selesai, Batal, Dikirim, dll)
    status             TEXT DEFAULT 'Tidak Diketahui',
    last_event         TEXT DEFAULT '',
    last_event_time    TEXT DEFAULT '',
    tracking_source    TEXT DEFAULT '',
    events_json        TEXT DEFAULT '[]',
    error              TEXT DEFAULT '',
    note               TEXT DEFAULT '',
    archived           INTEGER DEFAULT 0,
    lifecycle_override TEXT DEFAULT '',          -- '' (auto) | 'berjalan' | 'selesai'
    cancel_by          TEXT DEFAULT '',          -- buyer | system | seller | ops
    cancel_reason      TEXT DEFAULT '',          -- Alasan pembatalan (pembeli / sistem)
    payment_method     TEXT DEFAULT '',          -- COD (Bayar di Tempat) | QRIS | Saldo ShopeePay | dll
    total_payment      INTEGER DEFAULT 0,        -- Total Pembayaran pembeli (IDR)
    payment_details_json TEXT DEFAULT '{}',      -- Rincian biaya pesanan (subtotal, ongkir, voucher, layanan)
    order_date         TEXT DEFAULT '',          -- Waktu pesanan dibuat (YYYY-MM-DD atau YYYY-MM-DD HH:MM)
    created_at         TEXT,
    last_checked       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ship_buyer ON shipments(buyer_username);
CREATE INDEX IF NOT EXISTS idx_ship_order ON shipments(order_sn);
"""

FIELDS = [
    "resi", "courier", "order_sn", "buyer_username", "recipient_name",
    "recipient_phone", "city", "product", "order_source", "order_status",
    "cancel_by", "cancel_reason", "payment_method", "total_payment",
    "payment_details_json", "order_date", "note"
]


def _norm_order_sn(val: str) -> str:
    return (val or "").strip().upper()


def _norm_resi_key(raw_resi: str, order_sn: str = "") -> str:
    r = (raw_resi or "").strip()
    sn = _norm_order_sn(order_sn)
    if not r:
        return f"ORDER-{sn}" if sn else ""
    if r.upper().startswith("ORDER-"):
        return f"ORDER-{r[6:].strip().upper()}"
    return normalize_resi(r) or r.upper()


def _merge_payment_details_json(cur_raw: str, inc_raw: str) -> str:
    """Merge non-zero numeric keys from two payment_details_json strings."""
    cur_pd = (cur_raw or "").strip()
    inc_pd = (inc_raw or "").strip()
    if not inc_pd or inc_pd == "{}":
        return cur_pd or "{}"
    if not cur_pd or cur_pd == "{}":
        return inc_pd
    try:
        cur_d = json.loads(cur_pd)
        inc_d = json.loads(inc_pd)
        if isinstance(cur_d, dict) and isinstance(inc_d, dict):
            merged_d = dict(cur_d)
            for dk, dv in inc_d.items():
                if dv and not merged_d.get(dk):
                    merged_d[dk] = dv
            if merged_d != cur_d:
                return json.dumps(merged_d, ensure_ascii=False)
    except Exception:
        pass
    return cur_pd


def _order_status_rank(status_text: str) -> int:
    """Progression rank so re-importing an older file never downgrades a newer order status."""
    s = (status_text or "").strip().lower()
    if not s:
        return 0
    if any(k in s for k in ("selesai", "completed", "batal", "cancel", "pengembalian", "retur", "return", "refund")):
        return 5
    if any(k in s for k in ("terkirim", "delivered", "to_confirm_receive")):
        return 4
    if any(k in s for k in ("sedang dikirim", "telah dikirim", "shipped", "in_transit")):
        return 3
    if any(k in s for k in ("processed", "siap dikirim", "sedang diproses")):
        return 2
    if any(k in s for k in ("perlu dikirim", "to_ship", "ready_to_ship", "retry_ship", "sedang dikemas", "belum")):
        return 1
    return 1


class DB:
    def __init__(self, path=None):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path or config.DB_PATH), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add missing columns, normalize legacy status labels, and deduplicate order_sn rows."""
        for ddl in (
            "ALTER TABLE shipments ADD COLUMN order_status TEXT DEFAULT ''",
            "ALTER TABLE shipments ADD COLUMN lifecycle_override TEXT DEFAULT ''",
            "ALTER TABLE shipments ADD COLUMN cancel_by TEXT DEFAULT ''",
            "ALTER TABLE shipments ADD COLUMN cancel_reason TEXT DEFAULT ''",
            "ALTER TABLE shipments ADD COLUMN payment_method TEXT DEFAULT ''",
            "ALTER TABLE shipments ADD COLUMN total_payment INTEGER DEFAULT 0",
            "ALTER TABLE shipments ADD COLUMN payment_details_json TEXT DEFAULT '{}'",
            "ALTER TABLE shipments ADD COLUMN order_date TEXT DEFAULT ''",
        ):
            try:
                self.conn.execute(ddl)
                self.conn.commit()
            except Exception:
                pass
        missing_dates = self.conn.execute(
            "SELECT id, order_sn, created_at FROM shipments WHERE order_date IS NULL OR order_date=''"
        ).fetchall()
        for md in missing_dates:
            inferred = infer_date_from_order_sn(md["order_sn"] or "") or ((md["created_at"] or "")[:10])
            if inferred:
                self.conn.execute("UPDATE shipments SET order_date=? WHERE id=?", (inferred, md["id"]))
        masked_rows = self.conn.execute(
            "SELECT id, recipient_name, recipient_phone, city FROM shipments "
            "WHERE recipient_name LIKE '%*%' OR recipient_phone LIKE '%*%' OR city LIKE '%*%'"
        ).fetchall()
        for mr in masked_rows:
            upd_cols: dict[str, str] = {}
            for col in ("recipient_name", "recipient_phone", "city"):
                v = (mr[col] or "").strip()
                if v and is_masked_blank(v):
                    upd_cols[col] = ""
            if upd_cols:
                sets = ",".join(f"{k}=?" for k in upd_cols)
                self.conn.execute(f"UPDATE shipments SET {sets} WHERE id=?", (*upd_cols.values(), mr["id"]))
        self.conn.execute(
            "UPDATE shipments SET status=? WHERE status IN ('Pengiriman Gagal - BalSis', 'Pengiriman Gagal - Balsis')",
            (ST_RTS_BALSIS,),
        )
        self.conn.execute(
            "UPDATE shipments SET status=? WHERE status IN ('Pengiriman Gagal - Ditolak Pembeli', 'Pengiriman Gagal - Ditolak')",
            (ST_RTS_REJECTED,),
        )
        legacy_canc = self.conn.execute("SELECT id, order_status, cancel_by, cancel_reason, events_json FROM shipments WHERE status='Canceled'").fetchall()
        for lc in legacy_canc:
            evs = json.loads(lc["events_json"] or "[]")
            new_st = resolve_overall_status(lc["order_status"] or "", "Canceled", evs, lc["cancel_by"] or "", lc["cancel_reason"] or "")
            self.conn.execute("UPDATE shipments SET status=? WHERE id=?", (new_st, lc["id"]))
        for row in self.conn.execute("SELECT id, product FROM shipments WHERE product LIKE '%; %'").fetchall():
            grouped = merge_products("", row["product"] or "")
            if grouped != (row["product"] or ""):
                self.conn.execute("UPDATE shipments SET product=? WHERE id=?", (grouped, row["id"]))

        # Re-evaluate RTS rows that already have tracking events (heals false-positive postponements like F650)
        rts_rows = self.conn.execute(
            "SELECT id, status, order_status, cancel_by, cancel_reason, events_json FROM shipments "
            "WHERE status IN (?, ?, ?) AND events_json IS NOT NULL AND events_json != '' AND events_json != '[]'",
            (ST_RTS_BALSIS, ST_RTS_REJECTED, ST_RTS_RETURN),
        ).fetchall()
        for rr in rts_rows:
            try:
                evs = json.loads(rr["events_json"] or "[]")
            except Exception:
                evs = []
            if not evs:
                continue
            new_st = resolve_overall_status(
                rr["order_status"] or "", "", evs, rr["cancel_by"] or "", rr["cancel_reason"] or ""
            )
            if new_st and new_st != (rr["status"] or ""):
                self.conn.execute("UPDATE shipments SET status=? WHERE id=?", (new_st, rr["id"]))

        # Re-evaluate Canceled rows that already carry cancel metadata
        canc_rows = self.conn.execute(
            "SELECT id, resi, status, order_status, cancel_by, cancel_reason, events_json FROM shipments "
            "WHERE status IN (?, ?) AND (cancel_by != '' OR cancel_reason != '')",
            (ST_CANCELED_BUYER, ST_CANCELED_SYSTEM),
        ).fetchall()
        for cr_row in canc_rows:
            try:
                evs = json.loads(cr_row["events_json"] or "[]")
            except Exception:
                evs = []
            new_st = resolve_overall_status(
                cr_row["order_status"] or "", "", evs, cr_row["cancel_by"] or "", cr_row["cancel_reason"] or ""
            )
            if str(cr_row["resi"] or "").startswith("ORDER-") and new_st == ST_TRANSIT:
                new_st = ST_CANCELED_SYSTEM
            if new_st and new_st != (cr_row["status"] or ""):
                self.conn.execute("UPDATE shipments SET status=? WHERE id=?", (new_st, cr_row["id"]))

        # Heal ST_TRANSIT rows that only have pre-pickup events or pre-pickup order statuses
        pre_rows = self.conn.execute(
            "SELECT id, order_status, cancel_by, cancel_reason, events_json FROM shipments "
            "WHERE status=? AND ((events_json IS NOT NULL AND events_json != '' AND events_json != '[]') "
            "OR LOWER(order_status) LIKE '%perlu%' OR LOWER(order_status) LIKE '%kemas%' "
            "OR LOWER(order_status) LIKE '%ready_to_ship%' OR LOWER(order_status) LIKE '%processed%' "
            "OR LOWER(order_status) LIKE '%belum%')",
            (ST_TRANSIT,),
        ).fetchall()
        for pr in pre_rows:
            try:
                evs = json.loads(pr["events_json"] or "[]")
            except Exception:
                evs = []
            new_st = resolve_overall_status(
                pr["order_status"] or "", "", evs, pr["cancel_by"] or "", pr["cancel_reason"] or ""
            )
            if new_st == ST_PENDING:
                self.conn.execute("UPDATE shipments SET status=? WHERE id=?", (ST_PENDING, pr["id"]))

        from .models import ST_OUT
        # Heal ST_DELIVERED drop-point/facility false positives & Anteraja SATRIA out-for-delivery rows
        heal_rows = self.conn.execute(
            "SELECT id, status, order_status, cancel_by, cancel_reason, events_json FROM shipments "
            "WHERE status IN (?, ?) AND events_json IS NOT NULL AND events_json != '' AND events_json != '[]'",
            (ST_DELIVERED, ST_TRANSIT),
        ).fetchall()
        for hr in heal_rows:
            try:
                evs = json.loads(hr["events_json"] or "[]")
            except Exception:
                evs = []
            if not evs:
                continue
            new_st = resolve_overall_status(
                hr["order_status"] or "", "", evs, hr["cancel_by"] or "", hr["cancel_reason"] or ""
            )
            cur_st = hr["status"] or ""
            if cur_st == ST_DELIVERED and new_st != ST_DELIVERED:
                self.conn.execute("UPDATE shipments SET status=? WHERE id=?", (new_st, hr["id"]))
            elif cur_st == ST_TRANSIT and new_st == ST_OUT:
                self.conn.execute("UPDATE shipments SET status=? WHERE id=?", (ST_OUT, hr["id"]))

        self.conn.commit()
        self._deduplicate_existing()

    def _merge_row_pair_updates(self, keeper, dup_rows: list) -> dict[str, Any]:
        """Compute merged column updates on keeper from one or more duplicate rows."""
        updates: dict[str, Any] = {}
        cur_prod = keeper["product"] or ""
        cur_ord_st = keeper["order_status"] or ""
        cur_pd = (keeper["payment_details_json"] or "").strip()

        for dup in dup_rows:
            for f in ("courier", "order_sn", "buyer_username", "cancel_by", "cancel_reason", "payment_method", "note"):
                cur_val = (updates.get(f, keeper[f]) or "").strip()
                d_val = _norm_order_sn(dup[f] or "") if f == "order_sn" else (dup[f] or "").strip()
                if not cur_val and d_val:
                    updates[f] = d_val

            for f in ("recipient_name", "recipient_phone"):
                cur_val = (updates.get(f, keeper[f]) or "").strip()
                d_val = (dup[f] or "").strip()
                if d_val and not is_masked_blank(d_val) and (
                    not cur_val or is_masked_blank(cur_val) or ("*" in cur_val and "*" not in d_val)
                ):
                    updates[f] = d_val

            cur_city = (updates.get("city", keeper["city"]) or "").strip()
            d_city = (dup["city"] or "").strip()
            if d_city and not is_masked_blank(d_city) and (
                not cur_city
                or is_masked_blank(cur_city)
                or ("*" in cur_city and "*" not in d_city)
                or (len(d_city) > len(cur_city) and cur_city in d_city)
            ):
                updates["city"] = d_city

            cur_od = (updates.get("order_date", keeper["order_date"]) or "").strip()
            d_od = (dup["order_date"] or "").strip()
            if d_od and (not cur_od or (len(d_od) > len(cur_od) and d_od.startswith(cur_od))):
                updates["order_date"] = d_od

            if int(updates.get("total_payment", keeper["total_payment"]) or 0) == 0 and int(dup["total_payment"] or 0) > 0:
                updates["total_payment"] = int(dup["total_payment"])

            merged_pd = _merge_payment_details_json(cur_pd, dup["payment_details_json"] or "")
            if merged_pd != cur_pd:
                cur_pd = merged_pd
                updates["payment_details_json"] = merged_pd

            # Preserve tracking history if keeper hasn't been tracked yet while dup has tracking data
            cur_lc = (updates.get("last_checked", keeper["last_checked"]) or "").strip()
            d_lc = (dup["last_checked"] or "").strip()
            cur_ev = (updates.get("events_json", keeper["events_json"]) or "").strip()
            d_ev = (dup["events_json"] or "").strip()
            if (not cur_lc and d_lc) or ((not cur_ev or cur_ev == "[]") and d_ev and d_ev != "[]"):
                for tf in ("last_event", "last_event_time", "tracking_source", "events_json", "last_checked", "error"):
                    if dup[tf] is not None:
                        updates[tf] = dup[tf]

            cur_prod = merge_products(cur_prod, dup["product"] or "")
            if _order_status_rank(dup["order_status"] or "") > _order_status_rank(cur_ord_st):
                cur_ord_st = dup["order_status"] or ""

        if cur_prod != (keeper["product"] or ""):
            updates["product"] = cur_prod
        if cur_ord_st != (keeper["order_status"] or "") or "last_checked" in updates:
            if cur_ord_st != (keeper["order_status"] or ""):
                updates["order_status"] = cur_ord_st
            evs = json.loads(updates.get("events_json", keeper["events_json"] or "[]") or "[]")
            trk_hint = keeper["status"] if not evs else ""
            new_st = resolve_overall_status(
                cur_ord_st,
                trk_hint,
                evs,
                updates.get("cancel_by", keeper["cancel_by"] or ""),
                updates.get("cancel_reason", keeper["cancel_reason"] or ""),
            )
            if new_st != (keeper["status"] or ""):
                updates["status"] = new_st
        return updates

    def _deduplicate_existing(self):
        """Clean up pre-existing duplicate order_sn rows safely (keeping best tracking record)."""
        dup_orders = self.conn.execute(
            "SELECT UPPER(TRIM(order_sn)) AS norm_sn FROM shipments "
            "WHERE TRIM(order_sn) != '' GROUP BY UPPER(TRIM(order_sn)) HAVING COUNT(*) > 1"
        ).fetchall()
        for drow in dup_orders:
            sn = drow["norm_sn"]
            rows = self.conn.execute(
                """SELECT * FROM shipments WHERE UPPER(TRIM(order_sn))=?
                   ORDER BY CASE WHEN resi LIKE 'ORDER-%' THEN 1 ELSE 0 END ASC,
                            CASE WHEN last_checked != '' THEN 0 ELSE 1 END ASC,
                            id ASC""",
                (sn,),
            ).fetchall()
            keeper = rows[0]
            updates = self._merge_row_pair_updates(keeper, rows[1:])
            if updates:
                sets = ",".join(f"{k}=?" for k in updates)
                self.conn.execute(f"UPDATE shipments SET {sets} WHERE id=?", (*updates.values(), keeper["id"]))
            dup_ids = [(dup["id"],) for dup in rows[1:]]
            self.conn.executemany("DELETE FROM shipments WHERE id=?", dup_ids)
        if dup_orders:
            self.conn.commit()

    # ---------------------------------------------------------------- writes
    def upsert(self, data: dict) -> str:
        """Insert or smart-merge a shipment by resi OR order_sn.

        - Prevents duplicate orders when re-importing Shopee exports or mixing API + Excel.
        - Upgrades ORDER-{sn} placeholder to a real resi in-place.
        - Never downgrades a finalized order status when an older export is imported.
        Returns 'new' | 'updated' | 'same'.
        """
        order_sn = _norm_order_sn(data.get("order_sn") or "")
        resi = _norm_resi_key(data.get("resi") or "", order_sn)
        if not resi and not order_sn:
            raise ValueError("Resi atau order_sn kosong")

        with self._lock:
            row_resi = self.conn.execute(
                "SELECT * FROM shipments WHERE UPPER(TRIM(resi))=?", (resi,)
            ).fetchone()
            row_sn = None
            if order_sn:
                row_sn = self.conn.execute(
                    """SELECT * FROM shipments WHERE UPPER(TRIM(order_sn))=?
                       ORDER BY CASE WHEN resi LIKE 'ORDER-%' THEN 1 ELSE 0 END ASC,
                                CASE WHEN last_checked != '' THEN 0 ELSE 1 END ASC,
                                id ASC LIMIT 1""",
                    (order_sn,),
                ).fetchone()

            merged_dup = False
            if row_resi is not None and row_sn is not None and row_resi["id"] != row_sn["id"]:
                resi_is_ph = str(row_resi["resi"] or "").startswith("ORDER-")
                sn_is_ph = str(row_sn["resi"] or "").startswith("ORDER-")
                if resi_is_ph and not sn_is_ph:
                    keeper, loser = row_sn, row_resi
                else:
                    keeper, loser = row_resi, row_sn
                merge_upd = self._merge_row_pair_updates(keeper, [loser])
                self.conn.execute("DELETE FROM shipments WHERE id=?", (loser["id"],))
                if merge_upd:
                    sets = ",".join(f"{k}=?" for k in merge_upd)
                    self.conn.execute(f"UPDATE shipments SET {sets} WHERE id=?", (*merge_upd.values(), keeper["id"]))
                self.conn.commit()
                row = self.conn.execute("SELECT * FROM shipments WHERE id=?", (keeper["id"],)).fetchone()
                merged_dup = True
            else:
                row = row_resi if row_resi is not None else row_sn

            if row is None:
                clean_data = dict(data, resi=resi)
                if order_sn:
                    clean_data["order_sn"] = order_sn
                for pii_f in ("recipient_name", "recipient_phone", "city"):
                    if pii_f in clean_data and is_masked_blank(clean_data[pii_f] or ""):
                        clean_data[pii_f] = ""
                if not (clean_data.get("order_date") or "").strip():
                    inferred_od = infer_date_from_order_sn(order_sn)
                    if inferred_od:
                        clean_data["order_date"] = inferred_od
                cols = [f for f in FIELDS if f in clean_data]
                vals = []
                for f in cols:
                    if f == "total_payment":
                        vals.append(int(clean_data.get(f) or 0))
                    elif f == "payment_details_json":
                        vals.append((clean_data.get(f) or "").strip() or "{}")
                    else:
                        vals.append(clean_data[f] or "")
                init_status = resolve_overall_status(
                    clean_data.get("order_status", ""),
                    clean_data.get("status", ""),
                    [],
                    clean_data.get("cancel_by", ""),
                    clean_data.get("cancel_reason", ""),
                )
                if resi.startswith("ORDER-") and init_status == ST_TRANSIT and "batal" in (clean_data.get("order_status") or "").lower():
                    init_status = ST_CANCELED_SYSTEM
                cols.append("status")
                vals.append(init_status)
                self.conn.execute(
                    f"INSERT INTO shipments ({','.join(cols)}, created_at) VALUES ({','.join('?' * len(cols))}, ?)",
                    (*vals, datetime.now().strftime("%Y-%m-%d %H:%M")),
                )
                self.conn.commit()
                return "new"

            changes: dict[str, Any] = {}
            old_resi = row["resi"] or ""
            old_is_ph = old_resi.startswith("ORDER-")
            new_is_ph = resi.startswith("ORDER-")

            if old_resi != resi:
                if old_is_ph and not new_is_ph:
                    # Upgrade placeholder to real tracking number in-place
                    changes["resi"] = resi
                    changes["error"] = ""
                elif not old_is_ph and not new_is_ph:
                    # Resi changed/re-generated for the same order_sn: replace cleanly & reset tracking
                    changes["resi"] = resi
                    changes["events_json"] = "[]"
                    changes["last_event"] = ""
                    changes["last_event_time"] = ""
                    changes["last_checked"] = ""
                    changes["tracking_source"] = ""
                    changes["error"] = ""

            for f in ("courier", "order_sn", "buyer_username", "cancel_by", "cancel_reason", "payment_method", "note"):
                val = order_sn if f == "order_sn" else (data.get(f) or "").strip()
                if val and not (row[f] or "").strip():
                    changes[f] = val

            # Preserve partially masked Excel recipient/phone/city; never overwrite with empty or "****",
            # but allow automatic upgrade from partial ("M******o") to full unmasked PII ("Mulyono").
            for f in ("recipient_name", "recipient_phone"):
                val = (data.get(f) or "").strip()
                ex_val = (row[f] or "").strip()
                if val and not is_masked_blank(val) and (
                    not ex_val or is_masked_blank(ex_val) or ("*" in ex_val and "*" not in val)
                ):
                    changes[f] = val

            in_od = (data.get("order_date") or "").strip() or infer_date_from_order_sn(order_sn)
            ex_od = (row["order_date"] or "").strip()
            if in_od and (not ex_od or (len(in_od) > len(ex_od) and in_od.startswith(ex_od))):
                changes["order_date"] = in_od

            in_tp = int(data.get("total_payment") or 0)
            if in_tp > 0 and int(row["total_payment"] or 0) == 0:
                changes["total_payment"] = in_tp

            ex_pd = (row["payment_details_json"] or "").strip()
            merged_pd = _merge_payment_details_json(ex_pd, data.get("payment_details_json") or "")
            if merged_pd != ex_pd and merged_pd != "{}":
                changes["payment_details_json"] = merged_pd

            in_city = (data.get("city") or "").strip()
            ex_city = (row["city"] or "").strip()
            if in_city and not is_masked_blank(in_city) and (
                not ex_city
                or is_masked_blank(ex_city)
                or ("*" in ex_city and "*" not in in_city)
                or (len(in_city) > len(ex_city) and ex_city in in_city)
            ):
                changes["city"] = in_city

            merged_prod = merge_products(row["product"] or "", data.get("product") or "")
            if merged_prod != (row["product"] or ""):
                changes["product"] = merged_prod

            in_ord_st = (data.get("order_status") or "").strip()
            ex_ord_st = (row["order_status"] or "").strip()
            effective_ord_st = in_ord_st if (in_ord_st and _order_status_rank(in_ord_st) >= _order_status_rank(ex_ord_st)) else ex_ord_st
            if in_ord_st and in_ord_st != ex_ord_st and _order_status_rank(in_ord_st) >= _order_status_rank(ex_ord_st):
                changes["order_status"] = in_ord_st

            if "order_status" in changes or "cancel_by" in changes or "cancel_reason" in changes:
                evs = json.loads(changes.get("events_json", row["events_json"] or "[]"))
                trk_hint = "" if (evs or "cancel_by" in changes or "cancel_reason" in changes) else row["status"]
                new_st = resolve_overall_status(
                    effective_ord_st,
                    trk_hint,
                    evs,
                    changes.get("cancel_by", row["cancel_by"] or ""),
                    changes.get("cancel_reason", row["cancel_reason"] or ""),
                )
                eff_resi = changes.get("resi") or old_resi
                if eff_resi.startswith("ORDER-") and new_st == ST_TRANSIT and "batal" in effective_ord_st.lower():
                    new_st = ST_CANCELED_SYSTEM
                if new_st != (row["status"] or ""):
                    changes["status"] = new_st

            if not changes:
                return "updated" if merged_dup else "same"

            sets = ",".join(f"{k}=?" for k in changes)
            self.conn.execute(f"UPDATE shipments SET {sets} WHERE id=?", (*changes.values(), row["id"]))
            self.conn.commit()
            return "updated"

    def save_tracking(self, resi: str, result=None, error: str = "") -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self._lock:
            row = self.conn.execute("SELECT * FROM shipments WHERE resi=?", (resi,)).fetchone()
            order_status = row["order_status"] if row else ""
            c_by = row["cancel_by"] if row else ""
            c_reason = row["cancel_reason"] if row else ""

            if result is not None:
                final_status = resolve_overall_status(order_status, result.status, result.events, c_by, c_reason)
                events_json = json.dumps([e.__dict__ for e in result.events], ensure_ascii=False)
                self.conn.execute(
                    """UPDATE shipments SET status=?, last_event=?, last_event_time=?, tracking_source=?,
                       events_json=?, error='', last_checked=?, courier=CASE WHEN courier='' THEN ? ELSE courier END
                       WHERE resi=?""",
                    (final_status, result.last_event, result.last_time, result.source,
                     events_json, now, result.courier, resi)
                )
            else:
                self.conn.execute("UPDATE shipments SET error=?, last_checked=? WHERE resi=?", (error, now, resi))
            self.conn.commit()

    def set_status(self, resi: str, status: str) -> None:
        with self._lock:
            if status == "Error":
                self.conn.execute("UPDATE shipments SET status=? WHERE resi=?", (status, resi))
            else:
                self.conn.execute("UPDATE shipments SET status=?, error='' WHERE resi=?", (status, resi))
            self.conn.commit()

    def update_field(self, resi: str, field: str, value: str) -> None:
        if field not in FIELDS:
            raise ValueError(field)
        with self._lock:
            row = self.conn.execute("SELECT * FROM shipments WHERE resi=?", (resi,)).fetchone()
            if field == "order_status" and row:
                evs = json.loads(row["events_json"] or "[]")
                new_status = resolve_overall_status(value, row["status"], evs, row["cancel_by"] or "", row["cancel_reason"] or "")
                self.conn.execute("UPDATE shipments SET order_status=?, status=? WHERE resi=?", (value, new_status, resi))
            else:
                self.conn.execute(f"UPDATE shipments SET {field}=? WHERE resi=?", (value, resi))
            self.conn.commit()

    def set_archived(self, resis: list[str], archived: bool) -> None:
        with self._lock:
            self.conn.executemany("UPDATE shipments SET archived=? WHERE resi=?", [(int(archived), r) for r in resis])
            self.conn.commit()

    def delete(self, resis: list[str]) -> None:
        with self._lock:
            self.conn.executemany("DELETE FROM shipments WHERE resi=?", [(r,) for r in resis])
            self.conn.commit()

    def clear_all(self, include_archived: bool = True) -> int:
        with self._lock:
            if include_archived:
                cur = self.conn.execute("DELETE FROM shipments")
            else:
                cur = self.conn.execute("DELETE FROM shipments WHERE archived=0")
            self.conn.commit()
            return int(cur.rowcount or 0)

    # ---------------------------------------------------------------- reads
    def all(self, include_archived: bool = False) -> list[sqlite3.Row]:
        q = "SELECT * FROM shipments"
        if not include_archived:
            q += " WHERE archived=0"
        q += " ORDER BY created_at DESC, id DESC"
        with self._lock:
            return self.conn.execute(q).fetchall()

    def get(self, resi: str):
        with self._lock:
            return self.conn.execute("SELECT * FROM shipments WHERE resi=?", (resi,)).fetchone()

    def active_resis(self) -> list[sqlite3.Row]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM shipments WHERE archived=0 AND resi NOT LIKE 'ORDER-%'"
            ).fetchall()
        return [
            r for r in rows
            if row_lifecycle(dict(r)) in (CAT_ACTIVE, CAT_OTHER) and r["status"] != ST_DELIVERED
        ]

    def status_counts(self, include_archived: bool = False) -> dict[str, int]:
        with self._lock:
            q = "SELECT status, COUNT(*) as cnt FROM shipments"
            if not include_archived:
                q += " WHERE archived=0"
            q += " GROUP BY status"
            rows = self.conn.execute(q).fetchall()
            return {r["status"]: r["cnt"] for r in rows}

    def stale_active_resis(self, max_age_minutes: int = 60) -> list[sqlite3.Row]:
        """Return active shipments whose last_checked is empty or older than max_age_minutes."""
        active = self.active_resis()
        if max_age_minutes <= 0:
            cutoff_str = ""
        else:
            cutoff_str = (datetime.now() - timedelta(minutes=max_age_minutes)).strftime("%Y-%m-%d %H:%M")
        stale = []
        for r in active:
            lc = (r["last_checked"] or "").strip()
            if not lc or (cutoff_str and lc < cutoff_str):
                stale.append(r)
        return stale

    def archived_count(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) as cnt FROM shipments WHERE archived=1").fetchone()
            return int(row["cnt"]) if row else 0

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass
