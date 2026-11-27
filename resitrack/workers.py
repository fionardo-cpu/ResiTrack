"""Background workers (run in QThreadPool so the UI never freezes)."""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QRunnable, Signal

from . import couriers
from .models import NotFound, TrackingError


class WorkerSignals(QObject):
    progress = Signal(str)                 # status bar text
    progress_count = Signal(int, int, str) # done_count, total_count, current_resi
    item_done = Signal(str)                # resi updated
    sync_completed = Signal(int, int, list)  # new_count, updated_count, changed_resis
    finished = Signal(str)                 # summary
    error = Signal(str)


class TrackWorker(QRunnable):
    """Track a list of shipments (db rows) with polite bounded concurrency."""

    def __init__(self, db, rows, settings, shopee_client=None):
        super().__init__()
        self.setAutoDelete(False)
        self.db, self.rows, self.settings, self.shopee = db, list(rows), settings, shopee_client
        self.signals = WorkerSignals()
        self.cancelled = False
        self._lock = threading.Lock()
        self._done = 0
        self._ok = 0
        self._fail = 0

    def _safe_emit(self, sig, *args):
        try:
            sig.emit(*args)
        except RuntimeError:
            pass

    def _track_one(self, row, total: int, pace: float, use_shopee: bool):
        if self.cancelled:
            return
        resi = row["resi"]
        courier = row["courier"] or "?"
        self._safe_emit(self.signals.progress, f"Melacak {resi} ({courier})...")

        shopee_fn = None
        if use_shopee and row["order_sn"]:
            sn = row["order_sn"]
            pkg = row["note"][4:] if (row["note"] or "").startswith("pkg:") else ""
            shopee_fn = (lambda sn=sn, r=resi, c=row["courier"], p=pkg:
                         self.shopee.tracking_info(sn, r, c, p))

        ok_inc = fail_inc = 0
        try:
            result = couriers.track(resi, row["courier"], self.settings, shopee_fn)
            if not self.cancelled:
                self.db.save_tracking(resi, result)
            ok_inc = 1
        except NotFound as e:
            if not self.cancelled:
                self.db.save_tracking(resi, error=f"Belum ada data: {e}")
            fail_inc = 1
        except TrackingError as e:
            if not self.cancelled:
                self.db.save_tracking(resi, error=str(e))
            fail_inc = 1
        except Exception as e:  # noqa: BLE001
            if not self.cancelled:
                self.db.save_tracking(resi, error=f"{type(e).__name__}: {e}")
            fail_inc = 1

        if self.cancelled:
            return

        with self._lock:
            self._done += 1
            self._ok += ok_inc
            self._fail += fail_inc
            done_now = self._done

        self._safe_emit(self.signals.item_done, resi)
        self._safe_emit(self.signals.progress_count, done_now, total, resi)

        if pace > 0 and done_now < total and not self.cancelled:
            time.sleep(pace)

    def run(self):
        total = len(self.rows)
        if total == 0:
            self._safe_emit(self.signals.finished, "Tidak ada resi yang perlu dilacak.")
            return

        delay = float(self.settings.get("request_delay_seconds", 1.0))
        pace = delay if delay <= 0.5 else min(delay, 0.35)
        if total == 1:
            pace = 0.0

        use_shopee = (self.shopee is not None and self.shopee.authorised()
                      and self.settings.get("shopee_use_tracking_api", True))
        workers = 1 if total == 1 else min(4, total)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(self._track_one, row, total, pace, use_shopee) for row in self.rows]
            for fut in futures:
                if self.cancelled:
                    break
                try:
                    fut.result()
                except Exception:
                    pass

        if self.cancelled:
            self._safe_emit(self.signals.finished, f"Pelacakan dihentikan ({self._done}/{total} selesai).")
        else:
            self._safe_emit(
                self.signals.finished,
                f"Selesai: {self._ok} berhasil diperbarui, {self._fail} belum ada data / kendala."
            )


class ShopeeSyncWorker(QRunnable):
    def __init__(self, db, client, days: int = 15, since_ts: int | None = None,
                 until_ts: int | None = None, time_range_field: str = "create_time"):
        super().__init__()
        self.setAutoDelete(False)
        self.db, self.client, self.days = db, client, days
        self.since_ts = since_ts
        self.until_ts = until_ts
        self.time_range_field = time_range_field
        self.signals = WorkerSignals()
        self.cancelled = False

    def _safe_emit(self, sig, *args):
        try:
            sig.emit(*args)
        except RuntimeError:
            pass

    def run(self):
        try:
            self._safe_emit(self.signals.progress, "Sinkron pesanan Shopee...")
            records = self.client.sync(
                self.days,
                progress=lambda m: self._safe_emit(self.signals.progress, m),
                since_ts=self.since_ts,
                until_ts=self.until_ts,
                time_range_field=self.time_range_field,
            )
            if self.cancelled:
                self._safe_emit(self.signals.finished, "Sinkronisasi Shopee dibatalkan.")
                return
            new = upd = 0
            changed_resis: list[str] = []
            for r in records:
                if self.cancelled:
                    self._safe_emit(self.signals.finished, f"Sinkronisasi Shopee dihentikan ({new + upd} diproses).")
                    return
                res = self.db.upsert(r)
                if res == "new":
                    new += 1
                    changed_resis.append(r["resi"])
                elif res == "updated":
                    upd += 1
                    changed_resis.append(r["resi"])
            self._safe_emit(self.signals.sync_completed, new, upd, changed_resis)
            self._safe_emit(self.signals.finished, f"Sync Shopee selesai: {len(records)} resi ({new} baru, {upd} diperbarui).")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._safe_emit(self.signals.error, f"Sync Shopee gagal: {e}")
