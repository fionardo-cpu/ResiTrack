"""SQLite storage for orders/shipments with duplicate order/resi prevention."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any

from . import config
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
    "payment_details_json", "note"
]


def _order_status_rank(status_text: str) -> int:
    """Progression rank so re-importing an older file never downgrades a newer order status."""
    s = (status_text or "").strip().lower()
    if not s:
        return 0
    if any(k in s for k in ("selesai", "completed", "batal", "cancel", "pengembalian", "retur", "return", "refund")):
        return 4
    if any(k in s for k in ("terkirim", "delivered")):
        return 3
    if any(k in s for k in ("sedang dikirim", "telah dikirim", "shipped", "in_transit")):
        return 2
    if any(k in s for k in ("perlu dikirim", "to_ship", "ready_to_ship", "sedang dikemas", "belum")):
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
        ):
            try:
                self.conn.execute(ddl)
                self.conn.commit()
            except Exception:
                pass
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

        self.conn.commit()
        self._deduplicate_existing()

    def _deduplicate_existing(self):
        """Clean up pre-existing duplicate order_sn rows safely (keeping best tracking record)."""
        dup_orders = self.conn.execute(
            "SELECT order_sn FROM shipments WHERE order_sn != '' GROUP BY order_sn HAVING COUNT(*) > 1"
        ).fetchall()
        for drow in dup_orders:
            sn = drow["order_sn"]
            rows = self.conn.execute(
                """SELECT * FROM shipments WHERE order_sn=?
                   ORDER BY CASE WHEN resi LIKE 'ORDER-%' THEN 1 ELSE 0 END ASC,
                            CASE WHEN last_checked != '' THEN 0 ELSE 1 END ASC,
                            id ASC""",
                (sn,),
            ).fetchall()
            keeper = rows[0]
            keeper_id = keeper["id"]
            updates: dict[str, Any] = {}
            cur_prod = keeper["product"] or ""
            cur_ord_st = keeper["order_status"] or ""

            for dup in rows[1:]:
                for f in ("courier", "buyer_username", "recipient_name", "recipient_phone", "city", "payment_method", "note"):
                    if not (keeper[f] or updates.get(f)) and dup[f]:
                        updates[f] = dup[f]
                if int(keeper["total_payment"] or updates.get("total_payment") or 0) == 0 and int(dup["total_payment"] or 0) > 0:
                    updates["total_payment"] = int(dup["total_payment"])
                cur_pd = (updates.get("payment_details_json") or keeper["payment_details_json"] or "").strip()
                dup_pd = (dup["payment_details_json"] or "").strip()
                if (not cur_pd or cur_pd == "{}") and dup_pd and dup_pd != "{}":
                    updates["payment_details_json"] = dup_pd
                cur_prod = merge_products(cur_prod, dup["product"] or "")
                if _order_status_rank(dup["order_status"] or "") > _order_status_rank(cur_ord_st):
                    cur_ord_st = dup["order_status"] or ""

            if cur_prod != (keeper["product"] or ""):
                updates["product"] = cur_prod
            if cur_ord_st != (keeper["order_status"] or ""):
                updates["order_status"] = cur_ord_st
                evs = json.loads(keeper["events_json"] or "[]")
                updates["status"] = resolve_overall_status(
                    cur_ord_st, keeper["status"], evs,
                    updates.get("cancel_by", keeper["cancel_by"] or ""),
                    updates.get("cancel_reason", keeper["cancel_reason"] or ""),
                )

            if updates:
                sets = ",".join(f"{k}=?" for k in updates)
                self.conn.execute(f"UPDATE shipments SET {sets} WHERE id=?", (*updates.values(), keeper_id))
            dup_ids = [(dup["id"],) for dup in rows[1:]]
            self.conn.executemany("DELETE FROM shipments WHERE id=?", dup_ids)
        if dup_orders:
            self.conn.commit()

    # ---------------------------------------------------------------- writes
    def upsert(self, data: dict) -> str:
        """Insert or smart-merge a shipment by resi OR order_sn.

        - Prevents duplicate orders when re-importing Shopee exports.
        - Upgrades ORDER-{sn} placeholder to a real resi in-place.
        - Never downgrades a finalized order status when an older export is imported.
        Returns 'new' | 'updated' | 'same'.
        """
        resi = (data.get("resi") or "").strip()
        order_sn = (data.get("order_sn") or "").strip()
        if not resi and not order_sn:
            raise ValueError("Resi atau order_sn kosong")
        if not resi:
            resi = f"ORDER-{order_sn}"

        with self._lock:
            row = self.conn.execute("SELECT * FROM shipments WHERE resi=?", (resi,)).fetchone()
            if row is None and order_sn:
                row = self.conn.execute(
                    """SELECT * FROM shipments WHERE order_sn=?
                       ORDER BY CASE WHEN resi LIKE 'ORDER-%' THEN 1 ELSE 0 END ASC, id ASC LIMIT 1""",
                    (order_sn,),
                ).fetchone()

            if row is None:
                clean_data = dict(data, resi=resi)
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

            for f in ("courier", "order_sn", "buyer_username", "recipient_name", "recipient_phone", "cancel_by", "cancel_reason", "payment_method", "note"):
                val = (data.get(f) or "").strip()
                if val and not (row[f] or "").strip():
                    changes[f] = val

            in_tp = int(data.get("total_payment") or 0)
            if in_tp > 0 and int(row["total_payment"] or 0) == 0:
                changes["total_payment"] = in_tp

            in_pd = (data.get("payment_details_json") or "").strip()
            ex_pd = (row["payment_details_json"] or "").strip()
            if in_pd and in_pd != "{}" and (not ex_pd or ex_pd == "{}"):
                changes["payment_details_json"] = in_pd

            in_city = (data.get("city") or "").strip()
            ex_city = (row["city"] or "").strip()
            if in_city and (not ex_city or (len(in_city) > len(ex_city) and ex_city in in_city)):
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
                return "same"

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
