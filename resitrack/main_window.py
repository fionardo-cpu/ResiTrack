"""Modern Minimalist 3-Column ResiTrack Desktop Workspace (Alabaster & Ink / Obsidian & Mist)."""
from __future__ import annotations

import time
import webbrowser
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import (
    QDate,
    QItemSelection,
    QItemSelectionModel,
    QPoint,
    QRect,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import __version__, importer
from .config import assets_dir, load_settings, save_settings
from .db import DB
from .dialogs import AddDialog, SettingsDialog, ShopeeSyncDialog
from .icons import svg_icon, svg_pixmap
from .inspector import InspectorPanel
from .models import (
    ALL_STATUSES,
    CAT_ACTIVE,
    CAT_FINISHED,
    CAT_OTHER,
    COURIERS,
    FILTER_DUPLICATE,
    RTS_STATUSES,
    ST_CANCELED_BUYER,
    ST_CANCELED_SYSTEM,
    ST_COMPLETED,
    ST_DELIVERED,
    ST_ERROR,
    ST_OUT,
    ST_PENDING,
    ST_RTS_BALSIS,
    ST_RTS_REJECTED,
    ST_RTS_RETURN,
    ST_TRANSIT,
    ST_UNKNOWN,
    duplicate_resi_map,
    effective_status,
    format_date_short,
    format_idr,
    is_cod_payment,
    is_masked_blank,
    payment_short_label,
    recipient_dup_key,
    resolve_date_range,
    row_lifecycle,
    row_order_date,
)
from .shopee_api import ShopeeClient
from .theme import (
    STATUS_META,
    apply_windows_titlebar_theme,
    build_palette,
    build_stylesheet,
    courier_style,
    duplicate_style,
    get_tokens,
    payment_style,
    status_style,
)
from .workers import ShopeeSyncWorker, TrackWorker

ASSETS_DIR = assets_dir()
ICON_PATH = ASSETS_DIR / "icon.png"

ROW_DATA_ROLE = Qt.UserRole + 1

# Master 4-column structure (zero horizontal scrolling, plenty of breathing space)
COLUMNS = [
    ("buyer", "Pembeli"),
    ("resi", "Resi & Kurir"),
    ("status", "Status"),
    ("event", "Riwayat Terakhir"),
]
COL = {k: i for i, (k, _) in enumerate(COLUMNS)}


# ==============================================================================
# Custom Clean Table Delegates (Zero HTML, Crisp Typography, Hover & Selection)
# ==============================================================================
class _BaseRowDelegate(QStyledItemDelegate):
    """Paints consistent row hover, selected background, and bottom hairline."""

    def __init__(self, table_view: QTableView, is_dark: bool = False):
        super().__init__(table_view)
        self._table = table_view
        self.is_dark = is_dark

    def _paint_row_bg(self, painter: QPainter, option, index, draw_left_bar: bool = False):
        t = get_tokens(self.is_dark)
        is_sel = bool(option.state & QStyle.State_Selected)
        hovered_row = getattr(self._table, "hovered_row", -1)
        is_hover = (not is_sel) and (index.row() == hovered_row)

        if is_sel:
            bg = t["bg_selected"]
        elif is_hover:
            bg = t["bg_hover"]
        else:
            bg = t["bg_surface"]

        painter.fillRect(option.rect, QColor(bg))

        # Subtle bottom hairline
        painter.setPen(QPen(QColor(t["border_hairline"]), 1))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        # Crisp 3px accent bar on selected row's first column
        if is_sel and draw_left_bar:
            bar = QRect(option.rect.left(), option.rect.top() + 8, 3, option.rect.height() - 16)
            painter.fillRect(bar, QColor(t["accent"]))

    @staticmethod
    def _row_dict(index) -> dict:
        d = index.model().index(index.row(), 0).data(ROW_DATA_ROLE)
        return d if isinstance(d, dict) else {}


class BuyerCellDelegate(_BaseRowDelegate):
    @staticmethod
    def sub_text(row_dict: dict) -> tuple[str, bool]:
        """Return (subtitle_text, is_recipient) so recipient name & phone always appear under buyer username."""
        rec_name = (row_dict.get("recipient_name") or "").strip()
        rec_phone = (row_dict.get("recipient_phone") or "").strip()
        if is_masked_blank(rec_name):
            rec_name = ""
        if is_masked_blank(rec_phone):
            rec_phone = ""
        if rec_name or rec_phone:
            txt = f"{rec_name} ({rec_phone})" if (rec_name and rec_phone) else (rec_name or rec_phone)
            return txt, True
        return ((row_dict.get("order_sn") or "").strip() or "-", False)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        self._paint_row_bg(painter, option, index, draw_left_bar=True)
        d = self._row_dict(index)
        t = get_tokens(self.is_dark)

        r = option.rect.adjusted(16, 10, -10, -10)
        buyer = (d.get("buyer_username") or "").strip() or "Tanpa Username"
        sub_txt, is_rec = self.sub_text(d)

        f_top = QFont(option.font)
        f_top.setPointSize(10)
        f_top.setBold(bool(d.get("buyer_username")))
        painter.setFont(f_top)
        painter.setPen(QColor(t["text_primary"] if d.get("buyer_username") else t["text_muted"]))
        fm1 = QFontMetrics(f_top)
        painter.drawText(r.left(), r.top(), r.width(), 20, Qt.AlignLeft | Qt.AlignVCenter,
                         fm1.elidedText(buyer, Qt.ElideRight, r.width()))

        if is_rec:
            f_sub = QFont(option.font)
            f_sub.setPointSizeF(8.5)
        else:
            f_sub = QFont("Consolas", 8.5)
        painter.setFont(f_sub)
        painter.setPen(QColor(t["text_muted"]))
        fm2 = QFontMetrics(f_sub)
        painter.drawText(r.left(), r.top() + 21, r.width(), 18, Qt.AlignLeft | Qt.AlignVCenter,
                         fm2.elidedText(sub_txt, Qt.ElideRight, r.width()))
        painter.restore()


class ResiCellDelegate(_BaseRowDelegate):
    @staticmethod
    def copy_button_rect(cell_rect: QRect, resi: str) -> QRect:
        """Compute hit/paint rect for inline copy button right beside monospace resi text."""
        if not resi or resi == "-":
            return QRect()
        r = cell_rect.adjusted(10, 10, -10, -10)
        f_resi = QFont("Consolas", 9.5, QFont.DemiBold)
        fm_r = QFontMetrics(f_resi)
        max_text_w = max(40, r.width() - 26)
        elided = fm_r.elidedText(resi, Qt.ElideRight, max_text_w)
        text_w = min(max_text_w, fm_r.horizontalAdvance(elided))
        btn_x = r.left() + text_w + 5
        btn_y = r.top()
        return QRect(btn_x, btn_y, 20, 20)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        self._paint_row_bg(painter, option, index)
        d = self._row_dict(index)
        t = get_tokens(self.is_dark)

        r = option.rect.adjusted(10, 10, -10, -10)
        resi = d.get("resi") or "-"
        courier = (d.get("courier") or "").upper()
        shop_st = (d.get("order_status") or "").strip() or "Shopee"

        # Top line: monospace resi + subtle inline copy button
        f_resi = QFont("Consolas", 9.5, QFont.DemiBold)
        painter.setFont(f_resi)
        painter.setPen(QColor(t["text_primary"]))
        fm_r = QFontMetrics(f_resi)
        has_copy = bool(resi and resi != "-")
        max_text_w = (r.width() - 26) if has_copy else r.width()
        painter.drawText(r.left(), r.top(), max_text_w, 20, Qt.AlignLeft | Qt.AlignVCenter,
                         fm_r.elidedText(resi, Qt.ElideRight, max_text_w))

        if has_copy:
            btn_rect = self.copy_button_rect(option.rect, resi)
            is_copied = (getattr(self._table, "copied_resi", "") == resi)
            is_hovered = (getattr(self._table, "hovered_copy_row", -1) == index.row())
            if is_hovered and not is_copied:
                painter.setBrush(QColor(t["bg_subtle"]))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(btn_rect, 5, 5)
            icon_name = "check" if is_copied else "copy"
            icon_col = "#16A34A" if is_copied else (t["text_primary"] if is_hovered else t["text_muted"])
            pm = svg_pixmap(icon_name, icon_col, 13)
            painter.drawPixmap(btn_rect.left() + 3, btn_rect.top() + 3, pm)

        # Bottom line: courier micro-tag + payment micro-tag + total / Shopee status
        x = r.left()
        y_bot = r.top() + 22
        if courier:
            c_bg, c_fg = courier_style(courier, self.is_dark)
            f_tag = QFont(option.font)
            f_tag.setPointSize(7.5)
            f_tag.setBold(True)
            fm_tag = QFontMetrics(f_tag)
            tw = fm_tag.horizontalAdvance(courier) + 12
            tag_rect = QRect(x, y_bot, tw, 17)
            painter.setBrush(QColor(c_bg))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(tag_rect, 4, 4)
            painter.setFont(f_tag)
            painter.setPen(QColor(c_fg))
            painter.drawText(tag_rect, Qt.AlignCenter, courier)
            x += tw + 6

        pm = (d.get("payment_method") or "").strip()
        pm_lbl = payment_short_label(pm)
        if pm_lbl:
            p_bg, p_fg = payment_style(pm, self.is_dark)
            f_ptag = QFont(option.font)
            f_ptag.setPointSize(7.5)
            f_ptag.setBold(True)
            fm_ptag = QFontMetrics(f_ptag)
            pw = fm_ptag.horizontalAdvance(pm_lbl) + 10
            ptag_rect = QRect(x, y_bot, pw, 17)
            painter.setBrush(QColor(p_bg))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(ptag_rect, 4, 4)
            painter.setFont(f_ptag)
            painter.setPen(QColor(p_fg))
            painter.drawText(ptag_rect, Qt.AlignCenter, pm_lbl)
            x += pw + 6

        tp = int(d.get("total_payment") or 0)
        sub_label = f"{format_idr(tp)} · {shop_st}" if tp > 0 else shop_st
        f_sub = QFont(option.font)
        f_sub.setPointSize(8.5)
        painter.setFont(f_sub)
        painter.setPen(QColor(t["text_muted"]))
        rem_w = max(10, r.right() - x)
        fm_s = QFontMetrics(f_sub)
        painter.drawText(x, y_bot, rem_w, 17, Qt.AlignLeft | Qt.AlignVCenter,
                         fm_s.elidedText(sub_label, Qt.ElideRight, rem_w))
        painter.restore()


class StatusPillDelegate(_BaseRowDelegate):
    @staticmethod
    def dup_badge_rect(cell_rect: QRect, row_dict: dict, status: str, base_font: QFont) -> QRect:
        """Compute exact hit/paint rect for clickable 'Duplikat · Nx' badge inside the Status column."""
        dup_cnt = int(row_dict.get("_dup_count") or 0)
        if dup_cnt < 2:
            return QRect()
        is_rts = status in RTS_STATUSES
        pill_h = 24
        px = cell_rect.left() + 10
        py = cell_rect.top() + 8
        x_sub = px + 4
        y_sub = py + pill_h + 3
        if is_rts:
            lc = row_lifecycle(row_dict)
            lc_lbl = "Selesai" if lc == CAT_FINISHED else "Berjalan"
            f_sub = QFont(base_font)
            f_sub.setPointSize(7.5)
            f_sub.setBold(lc == CAT_ACTIVE)
            x_sub += QFontMetrics(f_sub).horizontalAdvance(lc_lbl) + 6

        dup_lbl = f"Duplikat · {dup_cnt}x"
        f_dup = QFont(base_font)
        f_dup.setPointSize(7.2)
        f_dup.setBold(True)
        fm_dup = QFontMetrics(f_dup)
        dw = min(max(44, cell_rect.right() - 6 - x_sub), fm_dup.horizontalAdvance(dup_lbl) + 10)
        if dw < 44:
            return QRect()
        return QRect(x_sub, y_sub, dw, 15)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        self._paint_row_bg(painter, option, index)

        d = self._row_dict(index)
        t = get_tokens(self.is_dark)
        status = index.data(Qt.DisplayRole) or ST_UNKNOWN
        bg, fg, dot_col, short_lbl = status_style(status, self.is_dark)

        f_pill = QFont(option.font)
        f_pill.setPointSize(8.5)
        f_pill.setBold(True)
        fm = QFontMetrics(f_pill)

        is_rts = status in RTS_STATUSES
        dup_cnt = int(d.get("_dup_count") or 0)
        is_dup = dup_cnt >= 2
        two_line = is_rts or is_dup
        text_w = fm.horizontalAdvance(short_lbl)
        pill_w = min(option.rect.width() - 12, text_w + 32)
        pill_h = 24 if two_line else 26
        px = option.rect.left() + 10
        py = (option.rect.top() + 8) if two_line else (option.rect.top() + (option.rect.height() - pill_h) // 2)
        pill_rect = QRect(px, py, pill_w, pill_h)

        painter.setBrush(QColor(bg))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(pill_rect, 12 if two_line else 13, 12 if two_line else 13)

        # 6px semantic indicator dot
        dot_x = px + 10
        dot_y = py + (pill_h - 6) // 2
        painter.setBrush(QColor(dot_col))
        painter.drawEllipse(dot_x, dot_y, 6, 6)

        # Text
        painter.setFont(f_pill)
        painter.setPen(QColor(fg))
        txt_rect = QRect(dot_x + 12, py, pill_w - 26, pill_h)
        painter.drawText(txt_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(short_lbl, Qt.ElideRight, txt_rect.width()))

        if two_line:
            if is_rts:
                x_sub = px + 4
                y_sub = py + pill_h + 3
                lc = row_lifecycle(d)
                lc_lbl = "Selesai" if lc == CAT_FINISHED else "Berjalan"
                f_sub = QFont(option.font)
                f_sub.setPointSize(7.5)
                f_sub.setBold(lc == CAT_ACTIVE)
                painter.setFont(f_sub)
                painter.setPen(QColor(t["text_muted"] if lc == CAT_FINISHED else t["text_secondary"]))
                painter.drawText(x_sub, y_sub, option.rect.width() - 16, 15,
                                 Qt.AlignLeft | Qt.AlignVCenter, lc_lbl)

            dup_rect = self.dup_badge_rect(option.rect, d, status, option.font)
            if not dup_rect.isEmpty():
                dup_lbl = f"Duplikat · {dup_cnt}x"
                d_bg, d_fg = duplicate_style(self.is_dark)
                f_dup = QFont(option.font)
                f_dup.setPointSize(7.2)
                f_dup.setBold(True)
                fm_dup = QFontMetrics(f_dup)
                is_dup_hover = (getattr(self._table, "hovered_dup_row", -1) == index.row())
                painter.setBrush(QColor(d_bg))
                painter.setPen(QPen(QColor(d_fg), 1.0) if is_dup_hover else Qt.NoPen)
                painter.drawRoundedRect(dup_rect, 4, 4)
                painter.setFont(f_dup)
                painter.setPen(QColor(d_fg))
                painter.drawText(dup_rect, Qt.AlignCenter,
                                 fm_dup.elidedText(dup_lbl, Qt.ElideRight, dup_rect.width() - 6))
        painter.restore()


class EventCellDelegate(_BaseRowDelegate):
    def paint(self, painter: QPainter, option, index):
        painter.save()
        self._paint_row_bg(painter, option, index)
        d = self._row_dict(index)
        t = get_tokens(self.is_dark)

        r = option.rect.adjusted(10, 10, -16, -10)
        last_ev = (d.get("last_event") or "").strip()
        err = (d.get("error") or "").strip()
        last_time = (d.get("last_event_time") or "").strip()
        last_checked = (d.get("last_checked") or "").strip()
        src = (d.get("tracking_source") or "").strip()

        if last_ev:
            top_text = last_ev
            top_color = t["text_primary"]
        elif err:
            top_text = err
            top_color = t["danger_fg"]
        elif (d.get("cancel_reason") or "").strip():
            top_text = (d.get("cancel_reason") or "").strip()
            top_color = t["text_primary"]
        else:
            top_text = "Belum dicek ke kurir"
            top_color = t["text_muted"]

        f_top = QFont(option.font)
        f_top.setPointSize(9.5)
        painter.setFont(f_top)
        painter.setPen(QColor(top_color))
        fm1 = QFontMetrics(f_top)
        painter.drawText(r.left(), r.top(), r.width(), 20, Qt.AlignLeft | Qt.AlignVCenter,
                         fm1.elidedText(top_text, Qt.ElideRight, r.width()))

        parts = []
        c_reason = (d.get("cancel_reason") or "").strip()
        if not last_ev and not err and c_reason:
            if not c_reason.lower().startswith("dibatalkan"):
                c_by = (d.get("cancel_by") or "").strip()
                actor_map = {"buyer": "Pembeli", "system": "Sistem", "seller": "Penjual"}
                actor = actor_map.get(c_by.lower(), c_by) or (
                    "Sistem" if effective_status(d) == ST_CANCELED_SYSTEM else "Pembeli"
                )
                parts.append(f"Dibatalkan oleh {actor}")
        if last_time:
            parts.append(last_time)
        elif last_checked:
            parts.append(f"Dicek {last_checked}")
        if src:
            parts.append(src)
        sub_text = " · ".join(parts) if parts else "Otomatis disinkronkan"

        f_sub = QFont(option.font)
        f_sub.setPointSize(8.5)
        painter.setFont(f_sub)
        painter.setPen(QColor(t["text_muted"]))
        fm2 = QFontMetrics(f_sub)
        painter.drawText(r.left(), r.top() + 21, r.width(), 18, Qt.AlignLeft | Qt.AlignVCenter,
                         fm2.elidedText(sub_text, Qt.ElideRight, r.width()))
        painter.restore()


class HoverTableView(QTableView):
    """QTableView subclass tracking full-row hover, inline copy-resi icons, and duplicate badges."""

    copy_resi_requested = Signal(str)
    duplicate_badge_clicked = Signal(str, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hovered_row = -1
        self.hovered_copy_row = -1
        self.hovered_dup_row = -1
        self.copied_resi = ""
        self.duplicate_view_mode = False
        self.setMouseTracking(True)

    def _hit_copy_resi(self, pos) -> tuple[int, str]:
        idx = self.indexAt(pos)
        if not idx.isValid() or idx.column() != COL["resi"]:
            return -1, ""
        d = self.model().index(idx.row(), 0).data(ROW_DATA_ROLE)
        resi = (d.get("resi") or "") if isinstance(d, dict) else ""
        btn_rect = ResiCellDelegate.copy_button_rect(self.visualRect(idx), resi)
        if not btn_rect.isEmpty() and btn_rect.contains(pos):
            return idx.row(), resi
        return -1, ""

    def _hit_dup_badge(self, pos) -> tuple[int, str, QRect]:
        idx = self.indexAt(pos)
        if not idx.isValid() or idx.column() != COL["status"]:
            return -1, "", QRect()
        d = self.model().index(idx.row(), 0).data(ROW_DATA_ROLE)
        if not isinstance(d, dict):
            return -1, "", QRect()
        resi = d.get("resi") or ""
        status = idx.data(Qt.DisplayRole) or effective_status(d)
        dup_rect = StatusPillDelegate.dup_badge_rect(self.visualRect(idx), d, status, self.font())
        if not dup_rect.isEmpty() and dup_rect.adjusted(-2, -2, 2, 2).contains(pos):
            return idx.row(), resi, dup_rect
        return -1, "", QRect()

    def flash_copied_resi(self, resi: str) -> None:
        self.copied_resi = resi
        self.viewport().update()

        def _clear(r=resi):
            try:
                if self.copied_resi == r:
                    self.copied_resi = ""
                    self.viewport().update()
            except RuntimeError:
                pass

        QTimer.singleShot(1200, _clear)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        idx = self.indexAt(pos)
        new_row = idx.row() if idx.isValid() else -1
        copy_row, _resi = self._hit_copy_resi(pos)
        dup_row, _dresi, _drect = self._hit_dup_badge(pos)
        changed = (
            (new_row != self.hovered_row)
            or (copy_row != self.hovered_copy_row)
            or (dup_row != self.hovered_dup_row)
        )
        self.hovered_row = new_row
        self.hovered_copy_row = copy_row
        self.hovered_dup_row = dup_row
        self.viewport().setCursor(
            Qt.PointingHandCursor if (copy_row != -1 or dup_row != -1) else Qt.ArrowCursor
        )
        if changed:
            self.viewport().update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            copy_row, resi = self._hit_copy_resi(pos)
            if copy_row != -1 and resi:
                self.copy_resi_requested.emit(resi)
                event.accept()
                return
            dup_row, dup_resi, dup_rect = self._hit_dup_badge(pos)
            if dup_row != -1 and dup_resi:
                self.duplicate_badge_clicked.emit(
                    dup_resi, self.viewport().mapToGlobal(dup_rect.bottomLeft())
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        changed = (
            (self.hovered_row != -1)
            or (self.hovered_copy_row != -1)
            or (self.hovered_dup_row != -1)
        )
        self.hovered_row = -1
        self.hovered_copy_row = -1
        self.hovered_dup_row = -1
        self.viewport().setCursor(Qt.ArrowCursor)
        if changed:
            self.viewport().update()
        super().leaveEvent(event)


def _payment_filter_bucket(method: str) -> str:
    raw = (method or "").strip()
    if is_cod_payment(raw):
        return "COD"
    low = raw.lower()
    if "qris" in low:
        return "QRIS"
    if "shopeepay" in low:
        return "ShopeePay"
    return "Lainnya"


_FILTER_STATUS_ITEMS: list[tuple[str, str]] = [
    (ST_PENDING, "Menunggu Pickup"),
    (ST_TRANSIT, "Dalam Perjalanan"),
    (ST_OUT, "Sedang Diantar"),
    (ST_DELIVERED, "Terkirim"),
    (ST_COMPLETED, "Selesai · Dana Cair"),
    (ST_RTS_BALSIS, "Gagal - Balsis"),
    (ST_RTS_REJECTED, "Gagal - Ditolak"),
    (ST_RTS_RETURN, "Pengembalian"),
    (ST_CANCELED_BUYER, "Canceled - Pembeli"),
    (ST_CANCELED_SYSTEM, "Canceled - Sistem"),
    (ST_ERROR, "Kendala Lacak"),
]
_FILTER_STATUS_LABELS = dict(_FILTER_STATUS_ITEMS)


_DATE_PRESET_ITEMS: list[tuple[str, str]] = [
    ("", "Semua"),
    ("today", "Hari Ini"),
    ("yesterday", "Kemarin"),
    ("this_week", "Minggu Ini"),
    ("last_7d", "7 Hari"),
    ("this_month", "Bulan Ini"),
    ("last_month", "Bulan Lalu"),
    ("custom", "Rentang..."),
]
_DATE_PRESET_LABELS = dict(_DATE_PRESET_ITEMS)


class FilterProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.category = ""
        self.status = ""
        self.duplicates_only = False
        self.duplicate_resis: set[str] = set()
        self.dup_group_resis: set[str] = set()
        self.lifecycles_inc: set[str] = set()
        self.lifecycles_exc: set[str] = set()
        self.statuses_inc: set[str] = set()
        self.statuses_exc: set[str] = set()
        self.couriers_inc: set[str] = set()
        self.couriers_exc: set[str] = set()
        self.payments_inc: set[str] = set()
        self.payments_exc: set[str] = set()
        self.dup_filter: str = ""
        self.date_from: date | None = None
        self.date_to: date | None = None

    def filterAcceptsRow(self, row, parent):
        d = self.sourceModel().index(row, 0, parent).data(ROW_DATA_ROLE)
        if not isinstance(d, dict):
            return True

        resi_val = d.get("resi") or ""
        row_status = effective_status(d)
        row_lc = row_lifecycle(d)
        row_courier = (d.get("courier") or "").upper()
        row_pay = _payment_filter_bucket(d.get("payment_method") or "")

        # 1. Primary view filter (Sidebar / Dup Group)
        if self.dup_group_resis and resi_val not in self.dup_group_resis:
            return False

        if self.duplicates_only:
            if resi_val not in self.duplicate_resis:
                return False
        else:
            if self.status and row_status != self.status:
                return False
            if self.category and row_lc != self.category:
                return False

        # 2. Date range filter (Order Date)
        if self.date_from is not None or self.date_to is not None:
            od = row_order_date(d)
            if od is None:
                return False
            if self.date_from is not None and od < self.date_from:
                return False
            if self.date_to is not None and od > self.date_to:
                return False

        # 3. Simultaneous Tampilkan (Include) + Kecualikan (Exclude) across all dimensions
        if self.lifecycles_inc and row_lc not in self.lifecycles_inc:
            return False
        if self.lifecycles_exc and row_lc in self.lifecycles_exc:
            return False

        if self.statuses_inc and row_status not in self.statuses_inc:
            return False
        if self.statuses_exc and row_status in self.statuses_exc:
            return False

        if self.couriers_inc and row_courier not in self.couriers_inc:
            return False
        if self.couriers_exc and row_courier in self.couriers_exc:
            return False

        if self.payments_inc and row_pay not in self.payments_inc:
            return False
        if self.payments_exc and row_pay in self.payments_exc:
            return False

        if self.dup_filter == "only":
            if resi_val not in self.duplicate_resis:
                return False
        elif self.dup_filter == "non_dup":
            if resi_val in self.duplicate_resis:
                return False

        # 4. Search box
        if self.text:
            q = self.text.lower()
            hay = " ".join(
                str(d.get(k) or "")
                for k in (
                    "buyer_username", "order_sn", "resi", "courier",
                    "recipient_name", "city", "product", "order_status", "status",
                    "cancel_by", "cancel_reason", "payment_method", "last_event",
                )
            ).lower()
            if resi_val in self.duplicate_resis:
                hay += " duplikat"
            if q not in hay:
                return False
        return True


class _DualFilterChip(QPushButton):
    """Minimalist 3-state chip: Left-click cycles Off -> ✓ Include -> ✕ Exclude -> Off."""

    mode_changed = Signal()

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.base_label = label
        self.filter_mode = ""  # "" | "include" | "exclude"
        self.setObjectName("DualFilterChip")
        self.setCheckable(True)
        self.setProperty("mode", "off")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Klik 1× tampilkan · 2× kecualikan · 3× hapus")
        self.clicked.connect(self._on_left_clicked)

    def _on_left_clicked(self) -> None:
        cycle = {"": "include", "include": "exclude", "exclude": ""}
        self.set_filter_mode(cycle.get(self.filter_mode, ""), emit=True)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            new_mode = "" if self.filter_mode == "exclude" else "exclude"
            self.set_filter_mode(new_mode, emit=True)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_filter_mode(self, mode: str, emit: bool = False) -> None:
        mode = mode if mode in ("include", "exclude") else ""
        changed = (mode != self.filter_mode)
        self.filter_mode = mode
        if mode == "include":
            self.setText(f"✓ {self.base_label}")
            self.setProperty("mode", "include")
        elif mode == "exclude":
            self.setText(f"✕ {self.base_label}")
            self.setProperty("mode", "exclude")
        else:
            self.setText(self.base_label)
            self.setProperty("mode", "off")
        super().setChecked(bool(mode))
        self.style().unpolish(self)
        self.style().polish(self)
        if emit and changed:
            self.mode_changed.emit()

    def setChecked(self, checked: bool) -> None:
        self.set_filter_mode("include" if checked else "", emit=(not self.signalsBlocked()))


class FilterPopover(QFrame):
    """Minimalist 3-tab floating filter (Status | Tanggal | Lainnya) with simultaneous ✓/✕."""

    filters_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("FilterPopover")
        self.setFixedWidth(356)
        self._date_preset = ""
        self._anchor_widget: QWidget | None = None
        self._closed_by_anchor = False
        self._closed_at = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 1. Clean Header (Title + Reset)
        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        title = QLabel("Filter")
        title.setStyleSheet("font-size: 13px; font-weight: 700;")
        btn_reset = QPushButton("Reset")
        btn_reset.setObjectName("GhostBtn")
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(self.reset_all)
        hdr.addWidget(title)
        hdr.addStretch(1)
        hdr.addWidget(btn_reset)
        root.addLayout(hdr)

        # 2. Calm 3-Tab Segmented Switcher
        tab_bar = QFrame()
        tab_bar.setObjectName("SegmentedBar")
        t_lay = QHBoxLayout(tab_bar)
        t_lay.setContentsMargins(2, 2, 2, 2)
        t_lay.setSpacing(2)

        def _make_tab(lbl: str, idx: int) -> QPushButton:
            b = QPushButton(lbl)
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setChecked(idx == 0)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, i=idx: self._set_tab(i))
            t_lay.addWidget(b, 1)
            return b

        self._tab_status = _make_tab("Status", 0)
        self._tab_date = _make_tab("Tanggal", 1)
        self._tab_other = _make_tab("Lainnya", 2)
        self._tab_btns = [self._tab_status, self._tab_date, self._tab_other]
        root.addWidget(tab_bar)

        def _over(txt: str) -> QLabel:
            lbl = QLabel(txt)
            lbl.setObjectName("SectionOverline")
            return lbl

        # 3. Stacked Tab Pages
        self._tabs_stack = QStackedWidget()
        root.addWidget(self._tabs_stack)

        # ---- Page 0: Status
        pg_status = QWidget()
        s_lay = QVBoxLayout(pg_status)
        s_lay.setContentsMargins(0, 2, 0, 0)
        s_lay.setSpacing(8)

        s_top = QHBoxLayout()
        s_top.setSpacing(6)
        self._btn_no_canc = QPushButton("Tanpa Canceled")
        self._btn_no_canc.setObjectName("PresetChip")
        self._btn_no_canc.setCheckable(True)
        self._btn_no_canc.setCursor(Qt.PointingHandCursor)
        self._btn_no_canc.setToolTip("Kecualikan semua Canceled (Pembeli & Sistem) tanpa menghapus filter lain")
        self._btn_no_canc.clicked.connect(self.apply_no_canceled_preset)
        lbl_hint_s = QLabel("1× ✓ · 2× ✕")
        lbl_hint_s.setObjectName("MutedText")
        lbl_hint_s.setStyleSheet("font-size: 10.5px;")
        s_top.addWidget(self._btn_no_canc)
        s_top.addStretch(1)
        s_top.addWidget(lbl_hint_s)
        s_lay.addLayout(s_top)

        st_grid = QGridLayout()
        st_grid.setHorizontalSpacing(4)
        st_grid.setVerticalSpacing(3)
        self._st_checks: dict[str, _DualFilterChip] = {}
        for idx, (st_key, lbl) in enumerate(_FILTER_STATUS_ITEMS):
            cb = _DualFilterChip(lbl)
            cb.mode_changed.connect(self._on_changed)
            self._st_checks[st_key] = cb
            st_grid.addWidget(cb, idx // 2, idx % 2)
        s_lay.addLayout(st_grid)
        s_lay.addStretch(1)
        self._tabs_stack.addWidget(pg_status)

        # ---- Page 1: Tanggal Pesanan
        pg_date = QWidget()
        d_lay = QVBoxLayout(pg_date)
        d_lay.setContentsMargins(0, 2, 0, 0)
        d_lay.setSpacing(10)

        dt_grid = QGridLayout()
        dt_grid.setHorizontalSpacing(6)
        dt_grid.setVerticalSpacing(6)
        self._date_btns: dict[str, QPushButton] = {}
        for idx, (dk, dlbl) in enumerate(_DATE_PRESET_ITEMS):
            b = QPushButton(dlbl)
            b.setObjectName("PresetChip")
            b.setCheckable(True)
            b.setChecked(dk == "")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, p=dk: self._set_date_preset(p))
            self._date_btns[dk] = b
            dt_grid.addWidget(b, idx // 2, idx % 2)
        d_lay.addLayout(dt_grid)

        self._date_range_row = QWidget()
        dr_lay = QHBoxLayout(self._date_range_row)
        dr_lay.setContentsMargins(0, 2, 0, 0)
        dr_lay.setSpacing(6)
        today_qd = QDate.currentDate()
        month_start_qd = QDate(today_qd.year(), today_qd.month(), 1)
        self._dt_from = QDateEdit(month_start_qd)
        self._dt_from.setCalendarPopup(True)
        self._dt_from.setDisplayFormat("dd/MM/yyyy")
        self._dt_to = QDateEdit(today_qd)
        self._dt_to.setCalendarPopup(True)
        self._dt_to.setDisplayFormat("dd/MM/yyyy")
        self._dt_from.dateChanged.connect(self._on_custom_date_changed)
        self._dt_to.dateChanged.connect(self._on_custom_date_changed)
        lbl_sd = QLabel("s/d")
        lbl_sd.setObjectName("MutedText")
        lbl_sd.setStyleSheet("font-size: 11px;")
        dr_lay.addWidget(self._dt_from, 1)
        dr_lay.addWidget(lbl_sd)
        dr_lay.addWidget(self._dt_to, 1)
        self._date_range_row.hide()
        d_lay.addWidget(self._date_range_row)
        d_lay.addStretch(1)
        self._tabs_stack.addWidget(pg_date)

        # ---- Page 2: Lainnya (Kategori, Kurir, Pembayaran, Duplikat)
        pg_other = QWidget()
        o_lay = QVBoxLayout(pg_other)
        o_lay.setContentsMargins(0, 2, 0, 0)
        o_lay.setSpacing(7)

        o_lay.addWidget(_over("KATEGORI"))
        lc_row = QHBoxLayout()
        lc_row.setSpacing(4)
        self._lc_checks: dict[str, _DualFilterChip] = {}
        for k, lbl in ((CAT_ACTIVE, "Berjalan"), (CAT_FINISHED, "Selesai"), (CAT_OTHER, "Lainnya")):
            cb = _DualFilterChip(lbl)
            cb.mode_changed.connect(self._on_changed)
            self._lc_checks[k] = cb
            lc_row.addWidget(cb)
        lc_row.addStretch(1)
        o_lay.addLayout(lc_row)

        o_lay.addWidget(_over("KURIR"))
        c_row = QHBoxLayout()
        c_row.setSpacing(4)
        self._courier_checks: dict[str, _DualFilterChip] = {}
        for c in COURIERS:
            cb = _DualFilterChip(c)
            cb.mode_changed.connect(self._on_changed)
            self._courier_checks[c] = cb
            c_row.addWidget(cb)
        c_row.addStretch(1)
        o_lay.addLayout(c_row)

        o_lay.addWidget(_over("PEMBAYARAN"))
        p_row = QHBoxLayout()
        p_row.setSpacing(4)
        self._pay_checks: dict[str, _DualFilterChip] = {}
        for p_key in ("COD", "QRIS", "ShopeePay", "Lainnya"):
            cb = _DualFilterChip(p_key)
            cb.mode_changed.connect(self._on_changed)
            self._pay_checks[p_key] = cb
            p_row.addWidget(cb)
        p_row.addStretch(1)
        o_lay.addLayout(p_row)

        o_lay.addWidget(_over("DUPLIKAT"))
        d_row = QHBoxLayout()
        d_row.setSpacing(6)
        self._dup_btns: dict[str, QPushButton] = {}
        for dk, dlbl in (("", "Semua"), ("only", "Duplikat"), ("non_dup", "Non-Duplikat")):
            b = QPushButton(dlbl)
            b.setObjectName("PresetChip")
            b.setCheckable(True)
            b.setChecked(dk == "")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, mode=dk: self._set_dup_mode(mode))
            self._dup_btns[dk] = b
            d_row.addWidget(b)
        d_row.addStretch(1)
        o_lay.addLayout(d_row)

        lbl_hint_o = QLabel("1× ✓ tampilkan · 2× ✕ kecualikan")
        lbl_hint_o.setObjectName("MutedText")
        lbl_hint_o.setStyleSheet("font-size: 10.5px;")
        o_lay.addWidget(lbl_hint_o)
        o_lay.addStretch(1)
        self._tabs_stack.addWidget(pg_other)

        self._sync_tab_badges()

    def _set_tab(self, idx: int) -> None:
        self._tabs_stack.setCurrentIndex(idx)
        for i, b in enumerate(self._tab_btns):
            b.setChecked(i == idx)

    def _sync_tab_badges(self) -> None:
        st = self.state()
        n_st = len(st["statuses_inc"]) + len(st["statuses_exc"])
        n_dt = 1 if st["date_preset"] else 0
        n_oth = (
            len(st["lifecycles_inc"]) + len(st["lifecycles_exc"])
            + len(st["couriers_inc"]) + len(st["couriers_exc"])
            + len(st["payments_inc"]) + len(st["payments_exc"])
            + (1 if st["dup_filter"] else 0)
        )
        self._tab_status.setText(f"Status · {n_st}" if n_st else "Status")
        self._tab_date.setText(f"Tanggal · {n_dt}" if n_dt else "Tanggal")
        self._tab_other.setText(f"Lainnya · {n_oth}" if n_oth else "Lainnya")

    def _set_date_preset(self, preset: str) -> None:
        self._date_preset = preset
        for k, b in self._date_btns.items():
            b.setChecked(k == preset)
        self._date_range_row.setVisible(preset == "custom")
        self._sync_tab_badges()
        self.adjustSize()
        self.filters_changed.emit()

    def _on_custom_date_changed(self, *_args) -> None:
        if self._date_preset == "custom":
            self._sync_tab_badges()
            self.filters_changed.emit()

    def _set_dup_mode(self, mode: str) -> None:
        for k, b in self._dup_btns.items():
            b.setChecked(k == mode)
        self._sync_tab_badges()
        self.filters_changed.emit()

    def _sync_preset_state(self) -> None:
        is_no_canc = (
            self._st_checks[ST_CANCELED_BUYER].filter_mode == "exclude"
            and self._st_checks[ST_CANCELED_SYSTEM].filter_mode == "exclude"
        )
        self._btn_no_canc.setChecked(is_no_canc)
        self._sync_tab_badges()

    def _on_changed(self, *_args) -> None:
        self._sync_preset_state()
        self.filters_changed.emit()

    @staticmethod
    def _split_modes(grp: dict[str, _DualFilterChip]) -> tuple[set[str], set[str]]:
        inc = {k for k, c in grp.items() if c.filter_mode == "include"}
        exc = {k for k, c in grp.items() if c.filter_mode == "exclude"}
        return inc, exc

    def state(self) -> dict:
        dup_mode = next((k for k, b in self._dup_btns.items() if b.isChecked()), "")
        lc_inc, lc_exc = self._split_modes(self._lc_checks)
        st_inc, st_exc = self._split_modes(self._st_checks)
        c_inc, c_exc = self._split_modes(self._courier_checks)
        p_inc, p_exc = self._split_modes(self._pay_checks)
        qd_from = self._dt_from.date()
        qd_to = self._dt_to.date()
        c_from = date(qd_from.year(), qd_from.month(), qd_from.day())
        c_to = date(qd_to.year(), qd_to.month(), qd_to.day())
        d_from, d_to = resolve_date_range(self._date_preset, c_from, c_to)
        return {
            "lifecycles_inc": lc_inc,
            "lifecycles_exc": lc_exc,
            "statuses_inc": st_inc,
            "statuses_exc": st_exc,
            "couriers_inc": c_inc,
            "couriers_exc": c_exc,
            "payments_inc": p_inc,
            "payments_exc": p_exc,
            "dup_filter": dup_mode,
            "date_preset": self._date_preset,
            "date_from": d_from,
            "date_to": d_to,
        }

    def active_group_count(self) -> int:
        st = self.state()
        return (
            int(bool(st["date_preset"]))
            + int(bool(st["lifecycles_inc"]))
            + int(bool(st["lifecycles_exc"]))
            + int(bool(st["statuses_inc"]))
            + int(bool(st["statuses_exc"]))
            + int(bool(st["couriers_inc"]))
            + int(bool(st["couriers_exc"]))
            + int(bool(st["payments_inc"]))
            + int(bool(st["payments_exc"]))
            + int(bool(st["dup_filter"]))
        )

    def apply_no_canceled_preset(self) -> None:
        want = self._btn_no_canc.isChecked()
        for k in (ST_CANCELED_BUYER, ST_CANCELED_SYSTEM):
            cb = self._st_checks[k]
            if want:
                cb.set_filter_mode("exclude", emit=False)
            elif cb.filter_mode == "exclude":
                cb.set_filter_mode("", emit=False)
        self._sync_preset_state()
        self.filters_changed.emit()

    def clear_group(self, group: str) -> None:
        base_map = {
            "lifecycles": self._lc_checks,
            "statuses": self._st_checks,
            "couriers": self._courier_checks,
            "payments": self._pay_checks,
        }
        if group in base_map:
            for cb in base_map[group].values():
                cb.set_filter_mode("", emit=False)
        elif group.endswith("_inc") or group.endswith("_exc"):
            dim, mode = group[:-4], ("include" if group.endswith("_inc") else "exclude")
            if dim in base_map:
                for cb in base_map[dim].values():
                    if cb.filter_mode == mode:
                        cb.set_filter_mode("", emit=False)
        elif group == "dup_filter":
            for k, b in self._dup_btns.items():
                b.setChecked(k == "")
        elif group == "date":
            self._date_preset = ""
            for k, b in self._date_btns.items():
                b.setChecked(k == "")
            self._date_range_row.hide()
            self.adjustSize()
        self._sync_preset_state()
        self.filters_changed.emit()

    def reset_all(self) -> None:
        for grp in (self._lc_checks, self._st_checks, self._courier_checks, self._pay_checks):
            for cb in grp.values():
                cb.set_filter_mode("", emit=False)
        for k, b in self._dup_btns.items():
            b.setChecked(k == "")
        self._date_preset = ""
        for k, b in self._date_btns.items():
            b.setChecked(k == "")
        self._date_range_row.hide()
        self.adjustSize()
        self._sync_preset_state()
        self.filters_changed.emit()

    def show_below(self, anchor: QWidget) -> None:
        self._anchor_widget = anchor
        self._closed_by_anchor = False
        self.adjustSize()
        pos = anchor.mapToGlobal(anchor.rect().bottomRight())
        x = pos.x() - self.width()
        y = pos.y() + 6
        scr = anchor.screen() or QApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            x = max(geo.left() + 8, min(x, geo.right() - self.width() - 8))
            y = max(geo.top() + 8, min(y, geo.bottom() - self.sizeHint().height() - 8))
        self.move(x, y)
        self.show()

    def hideEvent(self, event) -> None:
        self._closed_at = time.monotonic()
        if self._anchor_widget is not None and self._anchor_widget.isVisible():
            local_pos = self._anchor_widget.mapFromGlobal(QCursor.pos())
            self._closed_by_anchor = self._anchor_widget.rect().contains(local_pos)
        else:
            self._closed_by_anchor = False
        super().hideEvent(event)

    def consume_anchor_close(self) -> bool:
        hit = self._closed_by_anchor and (time.monotonic() - self._closed_at) < 0.8
        self._closed_by_anchor = False
        return hit


class _DupGroupItemFrame(QFrame):
    """Clickable row card inside DuplicateGroupPopover."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DupPopoverItem")
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DuplicateGroupPopover(QFrame):
    """Floating minimalist popover showing all duplicate orders sharing a recipient."""

    order_selected = Signal(str)
    filter_group_requested = Signal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("FilterPopover")
        self.setFixedWidth(396)
        self._group_label = ""
        self._group_resis: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        self._lbl_title = QLabel("Duplikat Penerima")
        self._lbl_title.setStyleSheet("font-size: 13px; font-weight: 700;")
        self._lbl_sub = QLabel("")
        self._lbl_sub.setObjectName("MutedText")
        self._lbl_sub.setStyleSheet("font-size: 11px;")
        title_col.addWidget(self._lbl_title)
        title_col.addWidget(self._lbl_sub)

        self._btn_filter_tbl = QPushButton("Filter di tabel")
        self._btn_filter_tbl.setObjectName("PresetChip")
        self._btn_filter_tbl.setCursor(Qt.PointingHandCursor)
        self._btn_filter_tbl.clicked.connect(self._on_filter_clicked)

        hdr.addLayout(title_col, 1)
        hdr.addWidget(self._btn_filter_tbl, 0, Qt.AlignTop)
        root.addLayout(hdr)

        div = QFrame()
        div.setObjectName("HairlineH")
        root.addWidget(div)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(6)
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll)

    def _clear_items(self) -> None:
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _on_filter_clicked(self) -> None:
        resis = list(self._group_resis)
        label = self._group_label
        self.close()
        if resis:
            self.filter_group_requested.emit(label, resis)

    def _on_item_clicked(self, resi: str) -> None:
        self.close()
        if resi:
            self.order_selected.emit(resi)

    def show_group(self, anchor_global_pos: QPoint, current_resi: str, group_rows: list[dict], is_dark: bool) -> None:
        self._clear_items()
        t = get_tokens(is_dark)
        cnt = len(group_rows)
        self._lbl_title.setText(f"Duplikat Penerima · {cnt}x")

        sample = group_rows[0] if group_rows else {}
        rec_name = (sample.get("recipient_name") or "").strip() or "Penerima"
        rec_phone = (sample.get("recipient_phone") or "").strip()
        city = (sample.get("city") or "").strip()
        sub_parts = [p for p in (rec_name, rec_phone, city) if p]
        self._lbl_sub.setText(" · ".join(sub_parts))
        self._group_label = f"{rec_name} ({cnt}x)"
        self._group_resis = [str(r.get("resi") or "") for r in group_rows if r.get("resi")]

        ordered = sorted(
            group_rows,
            key=lambda r: (0 if (r.get("resi") or "") == current_resi else 1, r.get("created_at") or "", r.get("resi") or ""),
        )
        for row in ordered:
            resi = str(row.get("resi") or "")
            card = _DupGroupItemFrame()
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(10, 8, 10, 8)
            c_lay.setSpacing(4)

            top = QHBoxLayout()
            top.setSpacing(6)
            buyer = (row.get("buyer_username") or "").strip() or "Tanpa Username"
            lbl_buyer = QLabel(buyer)
            lbl_buyer.setStyleSheet(f"font-size: 12px; font-weight: {'700' if row.get('buyer_username') else '500'}; color: {t['text_primary']};")
            lbl_buyer.setAttribute(Qt.WA_TransparentForMouseEvents)
            top.addWidget(lbl_buyer)

            if resi == current_resi:
                cur_tag = QLabel("Sedang dipilih")
                cur_tag.setObjectName("DupCurrentTag")
                cur_tag.setAttribute(Qt.WA_TransparentForMouseEvents)
                top.addWidget(cur_tag)

            top.addStretch(1)
            st = effective_status(row)
            s_bg, s_fg, _dot, s_lbl = status_style(st, is_dark)
            pill = QLabel(s_lbl)
            pill.setStyleSheet(
                f"background-color: {s_bg}; color: {s_fg}; border-radius: 9px; "
                "padding: 2px 8px; font-size: 10.5px; font-weight: 700;"
            )
            pill.setAttribute(Qt.WA_TransparentForMouseEvents)
            top.addWidget(pill)
            c_lay.addLayout(top)

            bot = QHBoxLayout()
            bot.setSpacing(6)
            meta_parts = [resi]
            courier = (row.get("courier") or "").strip().upper()
            if courier:
                meta_parts.append(courier)
            tp = int(row.get("total_payment") or 0)
            if tp > 0:
                meta_parts.append(format_idr(tp))
            dt = (row.get("last_event_time") or row.get("created_at") or "").strip()
            if dt:
                meta_parts.append(dt)
            lbl_meta = QLabel(" · ".join(meta_parts))
            lbl_meta.setStyleSheet(f"font-family: Consolas, monospace; font-size: 10.5px; color: {t['text_muted']};")
            lbl_meta.setAttribute(Qt.WA_TransparentForMouseEvents)
            bot.addWidget(lbl_meta, 1)
            c_lay.addLayout(bot)

            card.clicked.connect(lambda _r=resi: self._on_item_clicked(_r))
            self._list_lay.addWidget(card)

        self._list_host.adjustSize()
        scroll_h = min(320, max(64, self._list_host.sizeHint().height() + 4))
        self._scroll.setFixedHeight(scroll_h)
        self.adjustSize()

        x = anchor_global_pos.x()
        y = anchor_global_pos.y() + 6
        scr = QApplication.screenAt(anchor_global_pos) or QApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            x = max(geo.left() + 8, min(x, geo.right() - self.width() - 8))
            if y + self.sizeHint().height() > geo.bottom() - 8:
                y = max(geo.top() + 8, anchor_global_pos.y() - self.sizeHint().height() - 22)
        self.move(x, y)
        self.show()


# ==============================================================================
# Sidebar Navigation Button
# ==============================================================================
class NavItemButton(QPushButton):
    """Left rail navigation pill with status dot / SVG icon, label, and optional right count."""

    def __init__(self, key: object, label: str, dot_color: str = "", icon_name: str = "",
                 show_count: bool = True, parent=None):
        super().__init__(parent)
        self.key = key
        self._dot_color = dot_color
        self._icon_name = icon_name
        self._show_count = show_count
        self._accent_badge = False
        self._is_dark = False
        self.setObjectName("NavItem")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(9)

        self._lead = QLabel()
        self._lead.setFixedSize(14, 14)
        self._lead.setAlignment(Qt.AlignCenter)
        self._lead.setAttribute(Qt.WA_TransparentForMouseEvents)

        self._lbl = QLabel(label)
        self._lbl.setAttribute(Qt.WA_TransparentForMouseEvents)

        self._cnt = QLabel("0")
        self._cnt.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._cnt.setAlignment(Qt.AlignCenter)
        if not show_count:
            self._cnt.hide()

        lay.addWidget(self._lead)
        lay.addWidget(self._lbl, 1)
        lay.addWidget(self._cnt)

        self._apply_visuals()

    def set_icon_and_label(self, icon_name: str = "", label: str | None = None) -> None:
        if icon_name:
            self._icon_name = icon_name
        if label is not None:
            self._lbl.setText(label)
        self._apply_visuals()

    def set_count(self, count: int) -> None:
        self._cnt.setText(str(count))

    def set_accent_badge(self, active: bool) -> None:
        if self._accent_badge != active:
            self._accent_badge = active
            self._apply_visuals()

    def set_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self._apply_visuals()

    def _apply_visuals(self) -> None:
        t = get_tokens(self._is_dark)
        if self._dot_color:
            self._lead.setPixmap(QPixmap())
            self._lead.setFixedSize(7, 7)
            self._lead.setStyleSheet(f"background-color: {self._dot_color}; border-radius: 3px;")
        elif self._icon_name:
            self._lead.setFixedSize(14, 14)
            self._lead.setStyleSheet("background: transparent;")
            self._lead.setPixmap(svg_pixmap(self._icon_name, t["text_secondary"], 14))

        self._lbl.setStyleSheet(
            f"color: {t['text_primary'] if self.isChecked() else t['text_secondary']}; "
            f"font-size: 12px; font-weight: {'600' if self.isChecked() else '500'};"
        )
        if self._accent_badge:
            self._cnt.setStyleSheet(
                f"background-color: {t['accent_soft']}; color: {t['accent']}; "
                "border-radius: 8px; padding: 1px 7px; font-size: 10.5px; font-weight: 700;"
            )
        else:
            self._cnt.setStyleSheet(
                f"color: {t['text_muted']}; font-size: 11px; font-weight: 600; background: transparent;"
            )

    def nextCheckState(self):
        # Keep group controlled explicitly by parent
        pass

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._apply_visuals()


class CategoryNavButton(QPushButton):
    """Clickable uppercase category header (BERJALAN / SELESAI) with live total count pill."""

    def __init__(self, category_key: str, label: str, parent=None):
        super().__init__(parent)
        self.key = (category_key, "")
        self._is_dark = False
        self.setObjectName("CatNavItem")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(24)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(6)

        self._lbl = QLabel(label)
        self._lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._cnt = QLabel("0")
        self._cnt.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._cnt.setAlignment(Qt.AlignCenter)

        lay.addWidget(self._lbl, 1)
        lay.addWidget(self._cnt)
        self._apply_visuals()

    def set_count(self, count: int) -> None:
        self._cnt.setText(str(count))

    def set_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self._apply_visuals()

    def _apply_visuals(self) -> None:
        t = get_tokens(self._is_dark)
        lbl_col = t["accent"] if self.isChecked() else t["text_muted"]
        self._lbl.setStyleSheet(
            f"color: {lbl_col}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        self._cnt.setStyleSheet(
            f"background-color: {t['bg_subtle']}; color: {t['text_secondary']}; "
            "border-radius: 8px; padding: 1px 7px; font-size: 10.5px; font-weight: 700;"
        )

    def nextCheckState(self):
        pass

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._apply_visuals()


# ==============================================================================
# Main Application Window
# ==============================================================================
class MainWindow(QMainWindow):
    def __init__(self, auto_sync: bool = True):
        super().__init__()
        self.setWindowTitle(f"ResiTrack {__version__} — Pelacakan Resi & Pesanan Shopee")
        scr = QApplication.primaryScreen()
        def_h = min(900, max(740, scr.availableGeometry().height() - 48)) if scr is not None else 900
        self.resize(1400, def_h)
        self.setMinimumSize(1160, 720)
        self.setAcceptDrops(True)

        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self.settings = load_settings()
        self.current_theme = self.settings.get("theme", "light")

        self.db = DB()
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(6)

        self._batch_worker: TrackWorker | None = None
        self._single_worker: TrackWorker | None = None
        self._sync_worker: ShopeeSyncWorker | None = None
        self._active_workers: set = set()
        self._auto_sync_enabled = auto_sync

        self.active_category_filter = ""
        self.active_status_filter = ""
        self.show_archived = False
        self._saved_inspector_width = 360
        self._dup_map: dict[str, list[dict]] = {}

        self._build_ui()
        self.apply_theme(self.current_theme)
        self._refresh_last_sync_label()
        if not self.settings.get("inspector_open", True):
            self.close_inspector(save=False)

        self.statusBar().showMessage("Siap memantau pesanan tokomu.")
        self.reload()

        # Background periodic refresh timer (courier status & realtime Shopee polling)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.track_rows(self.db.active_resis(), auto=True))
        self._shopee_sync_timer = QTimer(self)
        self._shopee_sync_timer.timeout.connect(self._auto_sync_shopee_realtime)
        self._apply_timer()

        # Silent startup auto-sync so new Shopee orders & tracking statuses update automatically
        if self._auto_sync_enabled:
            QTimer.singleShot(450, self._auto_sync_stale_on_startup)
            if self.settings.get("shopee_auto_sync_enabled", True) and self._shopee().authorised():
                QTimer.singleShot(1400, self._auto_sync_shopee_realtime)

    # ------------------------------------------------------------------ Theme Application
    def apply_theme(self, theme_name: str):
        self.current_theme = "dark" if theme_name == "dark" else "light"
        self.settings["theme"] = self.current_theme
        save_settings(self.settings)

        is_dark = self.current_theme == "dark"
        t = get_tokens(is_dark)

        app = QApplication.instance()
        if app is not None:
            app.setPalette(build_palette(is_dark))
            app.setStyleSheet(build_stylesheet(is_dark))
        apply_windows_titlebar_theme(self, is_dark)

        # Refresh icons across header & sidebar
        self._lbl_version.setStyleSheet(
            f"background-color: {t['bg_subtle']}; color: {t['text_muted']}; "
            "border-radius: 6px; padding: 2px 6px; font-size: 10px; font-weight: 600;"
        )
        self._lbl_brand.setStyleSheet(f"color: {t['text_primary']}; font-size: 14.5px; font-weight: 700;")

        self._search_act.setIcon(svg_icon("search", t["text_muted"], 14))
        self.btn_import.setIcon(svg_icon("upload", t["text_primary"], 14))
        self.btn_add.setIcon(svg_icon("plus", t["accent_text"], 14))
        self.btn_more.setIcon(svg_icon("more", t["text_secondary"], 15))
        self._refresh_inspector_toggle_btn()
        self._btn_empty_import.setIcon(svg_icon("upload", t["text_primary"], 14))
        self._empty_table_icon.setPixmap(svg_pixmap("inbox", t["text_muted"], 24))

        self.btn_arch.set_theme(is_dark)
        for nav in self.nav_items.values():
            nav.set_theme(is_dark)

        self.btn_settings.setIcon(svg_icon("settings", t["text_secondary"], 14))
        self.btn_theme.setIcon(svg_icon("sun" if is_dark else "moon", t["text_secondary"], 14))
        self.btn_theme.setText("Terang" if is_dark else "Gelap")
        self._refresh_filter_btn_state()
        self._rebuild_filter_chips()

        self._refresh_batch_button_state()
        self._refresh_more_menu_icons()

        # Delegates & Table
        for dlg in self._delegates:
            dlg.is_dark = is_dark
        self.table.viewport().update()

        # Inspector
        self.inspector.set_theme(is_dark)

    def toggle_theme(self):
        self.apply_theme("light" if self.current_theme == "dark" else "dark")

    # ------------------------------------------------------------------ UI Construction
    def _build_ui(self):
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 1. Left Sidebar Rail (232px, Clean Non-Scrolling White-Space Layout)
        rail = QFrame()
        rail.setObjectName("LeftRail")
        rail.setFixedWidth(232)
        r_lay = QVBoxLayout(rail)
        r_lay.setContentsMargins(12, 14, 12, 10)
        r_lay.setSpacing(1)

        # Brand Row
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 2, 4, 8)
        brand_row.setSpacing(9)
        logo = QLabel()
        mini_icon = ASSETS_DIR / "icon_32.png"
        if mini_icon.exists():
            pm = QPixmap(str(mini_icon))
            pm.setDevicePixelRatio(1.25)
            logo.setPixmap(pm)
        logo.setFixedSize(26, 26)
        self._lbl_brand = QLabel("ResiTrack")
        self._lbl_version = QLabel(f"v{__version__}")
        brand_row.addWidget(logo)
        brand_row.addWidget(self._lbl_brand)
        brand_row.addStretch(1)
        brand_row.addWidget(self._lbl_version)
        r_lay.addLayout(brand_row)

        self.nav_items: dict[tuple[str, str], QPushButton] = {}

        def add_cat_hdr(cat_key: str, text: str) -> CategoryNavButton:
            btn = CategoryNavButton(cat_key, text)
            btn.clicked.connect(lambda _checked=False, ck=cat_key: self._on_nav_clicked(ck, ""))
            self.nav_items[(cat_key, "")] = btn
            r_lay.addSpacing(9)
            r_lay.addWidget(btn)
            return btn

        def add_nav(cat_key: str, st_key: str, label: str, dot_color: str = "", icon_name: str = "") -> NavItemButton:
            btn = NavItemButton((cat_key, st_key), label, dot_color=dot_color, icon_name=icon_name)
            btn.clicked.connect(lambda _checked=False, ck=cat_key, sk=st_key: self._on_nav_clicked(ck, sk))
            self.nav_items[(cat_key, st_key)] = btn
            r_lay.addWidget(btn)
            return btn

        add_nav("", "", "Semua Pesanan", icon_name="inbox")
        self.nav_items[("", "")].setChecked(True)

        add_cat_hdr(CAT_ACTIVE, "BERJALAN")
        for st_key, lbl in (
            (ST_PENDING, "Menunggu Pickup"),
            (ST_TRANSIT, "Dalam Perjalanan"),
            (ST_OUT, "Sedang Diantar"),
            (ST_DELIVERED, "Terkirim"),
            (ST_RTS_BALSIS, "Gagal - Balsis"),
            (ST_RTS_REJECTED, "Gagal - Ditolak"),
            (ST_RTS_RETURN, "Pengembalian"),
        ):
            add_nav(CAT_ACTIVE, st_key, lbl, dot_color=STATUS_META[st_key]["dot"])

        add_cat_hdr(CAT_FINISHED, "SELESAI")
        for st_key, lbl in (
            (ST_COMPLETED, "Selesai · Dana Cair"),
            (ST_RTS_BALSIS, "Gagal - Balsis"),
            (ST_RTS_REJECTED, "Gagal - Ditolak"),
            (ST_RTS_RETURN, "Pengembalian"),
            (ST_CANCELED_BUYER, "Canceled - Pembeli"),
            (ST_CANCELED_SYSTEM, "Canceled - Sistem"),
        ):
            add_nav(CAT_FINISHED, st_key, lbl, dot_color=STATUS_META[st_key]["dot"])

        add_cat_hdr(CAT_OTHER, "LAINNYA")
        self._err_nav_btn = add_nav(CAT_OTHER, ST_ERROR, "Kendala Lacak", dot_color=STATUS_META[ST_ERROR]["dot"])
        self._dup_nav_btn = add_nav(CAT_OTHER, FILTER_DUPLICATE, "Duplikat", dot_color="#7C3AED")
        self._unk_nav_btn = add_nav(CAT_OTHER, ST_UNKNOWN, "Tidak Diketahui", dot_color=STATUS_META[ST_UNKNOWN]["dot"])
        self._unk_nav_btn.hide()

        r_lay.addStretch(1)

        div_rail = QFrame()
        div_rail.setObjectName("HairlineH")
        r_lay.addWidget(div_rail)
        r_lay.addSpacing(5)

        self.btn_arch = NavItemButton("__ARCHIVE__", "Arsip", icon_name="archive")
        self.btn_arch.clicked.connect(self._on_archive_toggled)
        r_lay.addWidget(self.btn_arch)
        r_lay.addSpacing(4)

        util_row = QHBoxLayout()
        util_row.setContentsMargins(0, 0, 0, 0)
        util_row.setSpacing(6)

        self.btn_settings = QPushButton("Pengaturan")
        self.btn_settings.setObjectName("RailUtilBtn")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setFixedHeight(30)
        self.btn_settings.clicked.connect(self.open_settings)

        self.btn_theme = QPushButton("Gelap")
        self.btn_theme.setObjectName("RailUtilBtn")
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        self.btn_theme.setFixedHeight(30)
        self.btn_theme.clicked.connect(self.toggle_theme)

        util_row.addWidget(self.btn_settings, 1)
        util_row.addWidget(self.btn_theme, 1)
        r_lay.addLayout(util_row)

        root.addWidget(rail)

        # ---- 2. Center + Right Splitter
        center_pane = QWidget()
        center_pane.setObjectName("CenterWorkspace")
        c_lay = QVBoxLayout(center_pane)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(0)

        # Tier 1: Workspace Header (54px) - Title & Action Buttons
        hdr = QWidget()
        hdr.setObjectName("WorkspaceHeader")
        hdr.setFixedHeight(54)
        h_lay = QHBoxLayout(hdr)
        h_lay.setContentsMargins(20, 0, 16, 0)
        h_lay.setSpacing(8)

        self._lbl_view_title = QLabel("Semua Pesanan")
        self._lbl_view_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self._lbl_view_count = QLabel("0")
        self._lbl_view_count.setObjectName("CountPill")
        self._lbl_view_count.setFixedHeight(22)
        self._lbl_view_count.setAlignment(Qt.AlignCenter)

        self._lbl_last_sync = QLabel("")
        self._lbl_last_sync.setObjectName("MutedText")
        self._lbl_last_sync.setStyleSheet("font-size: 11px; padding-left: 4px;")
        self._lbl_last_sync.setMaximumWidth(168)
        self._lbl_last_sync.hide()

        h_lay.addWidget(self._lbl_view_title, 0, Qt.AlignVCenter)
        h_lay.addWidget(self._lbl_view_count, 0, Qt.AlignVCenter)
        h_lay.addWidget(self._lbl_last_sync, 0, Qt.AlignVCenter)
        h_lay.addStretch(1)

        self.btn_import = QPushButton("Impor")
        self.btn_import.setObjectName("SecondaryBtn")
        self.btn_import.setCursor(Qt.PointingHandCursor)
        self.btn_import.setToolTip("Impor file pesanan Shopee (.xlsx / .xls / .csv) — bisa pilih beberapa file sekaligus")
        self.btn_import.clicked.connect(self.import_file)

        self.btn_batch_track = QPushButton("Lacak")
        self.btn_batch_track.setObjectName("SecondaryBtn")
        self.btn_batch_track.setCursor(Qt.PointingHandCursor)
        self.btn_batch_track.setToolTip("Lacak semua paket aktif di kurir")
        self.btn_batch_track.clicked.connect(self._on_batch_btn_clicked)

        self.btn_add = QPushButton("Tambah")
        self.btn_add.setObjectName("PrimaryBtn")
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setToolTip("Input nomor resi secara manual")
        self.btn_add.clicked.connect(self.add_resi)

        self.btn_toggle_inspector = QPushButton()
        self.btn_toggle_inspector.setObjectName("IconBtn")
        self.btn_toggle_inspector.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_inspector.setToolTip("Tutup panel detail kanan")
        self.btn_toggle_inspector.clicked.connect(self.toggle_inspector)

        self.btn_more = QPushButton()
        self.btn_more.setObjectName("IconBtn")
        self.btn_more.setCursor(Qt.PointingHandCursor)
        self.btn_more.setToolTip("Opsi lainnya (Shopee API, Ekspor CSV, Hapus Semua)")
        self._more_menu = QMenu(self)
        self._act_shopee = self._more_menu.addAction("Tarik dari Shopee API", self.sync_shopee)
        self._act_export = self._more_menu.addAction("Ekspor ke CSV", self.export)
        self._act_template = self._more_menu.addAction("Unduh Template CSV", self.save_template)
        self._more_menu.addSeparator()
        self._act_settings_menu = self._more_menu.addAction("Pengaturan", self.open_settings)
        self._more_menu.addSeparator()
        self._act_clear_all = self._more_menu.addAction("Hapus Semua Data Resi", self.clear_all_data)
        self.btn_more.setMenu(self._more_menu)

        h_lay.addWidget(self.btn_import)
        h_lay.addWidget(self.btn_batch_track)
        h_lay.addWidget(self.btn_add)
        h_lay.addWidget(self.btn_toggle_inspector)
        h_lay.addWidget(self.btn_more)
        c_lay.addWidget(hdr)

        # Tier 2: Futuristic Segmented Lifecycle Switcher + Search + Courier Filter (48px)
        sub_hdr = QWidget()
        sub_hdr.setObjectName("WorkspaceSubHeader")
        sub_hdr.setFixedHeight(48)
        sh_lay = QHBoxLayout(sub_hdr)
        sh_lay.setContentsMargins(20, 6, 16, 6)
        sh_lay.setSpacing(8)

        seg_bar = QFrame()
        seg_bar.setObjectName("SegmentedBar")
        seg_lay = QHBoxLayout(seg_bar)
        seg_lay.setContentsMargins(2, 2, 2, 2)
        seg_lay.setSpacing(2)

        def _make_seg(text: str, cat_key: str) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, ck=cat_key: self._on_nav_clicked(ck, ""))
            seg_lay.addWidget(b)
            return b

        self._seg_all = _make_seg("Semua · 0", "")
        self._seg_active = _make_seg("Berjalan · 0", CAT_ACTIVE)
        self._seg_done = _make_seg("Selesai · 0", CAT_FINISHED)
        self._seg_other = _make_seg("Lainnya · 0", CAT_OTHER)
        self._seg_all.setChecked(True)

        self.search = QLineEdit()
        self.search.setObjectName("SearchInput")
        self.search.setCursor(Qt.IBeamCursor)
        self.search.setMinimumHeight(34)
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Cari resi, no. pesanan, buyer...")
        self._search_act = self.search.addAction(svg_icon("search", "#8C93A1", 14), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence(Qt.Key_Escape), self).activated.connect(
            lambda: self.close_inspector() if self.inspector.isVisible() else None
        )

        self.btn_filter = QPushButton("Filter")
        self.btn_filter.setObjectName("SecondaryBtn")
        self.btn_filter.setCursor(Qt.PointingHandCursor)
        self.btn_filter.setMinimumHeight(34)
        self.btn_filter.setToolTip("Filter multi-kriteria (Kategori, Status tampil/kecuali, Kurir, Pembayaran, Duplikat)")
        self.btn_filter.clicked.connect(self._open_filter_popover)

        self._filter_popover = FilterPopover(self)
        self._filter_popover.filters_changed.connect(self._filter)

        sh_lay.addWidget(seg_bar)
        sh_lay.addWidget(self.search, 1)
        sh_lay.addWidget(self.btn_filter)
        c_lay.addWidget(sub_hdr)

        # Collapsible Active Filter Chips Bar (hidden unless checklist filters are active)
        self._filter_bar = QFrame()
        self._filter_bar.setObjectName("FilterChipBar")
        self._filter_bar_lay = QHBoxLayout(self._filter_bar)
        self._filter_bar_lay.setContentsMargins(20, 6, 16, 6)
        self._filter_bar_lay.setSpacing(6)
        self._filter_bar.hide()
        c_lay.addWidget(self._filter_bar)

        # Collapsible Inline Background Sync Banner
        self._sync_banner = QFrame()
        self._sync_banner.setObjectName("SyncBanner")
        sb_lay = QVBoxLayout(self._sync_banner)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(0)

        sb_row = QHBoxLayout()
        sb_row.setContentsMargins(24, 6, 20, 6)
        sb_row.setSpacing(10)
        self._sync_lbl = QLabel("Menyinkronkan status kurir...")
        self._sync_lbl.setObjectName("MutedText")
        self._sync_stop = QPushButton("Hentikan")
        self._sync_stop.setObjectName("GhostBtn")
        self._sync_stop.setCursor(Qt.PointingHandCursor)
        self._sync_stop.clicked.connect(self.stop_batch)
        sb_row.addWidget(self._sync_lbl, 1)
        sb_row.addWidget(self._sync_stop)
        sb_lay.addLayout(sb_row)

        self._sync_prog = QProgressBar()
        self._sync_prog.setTextVisible(False)
        self._sync_prog.setFixedHeight(3)
        sb_lay.addWidget(self._sync_prog)
        self._sync_banner.hide()
        c_lay.addWidget(self._sync_banner)

        # Table / Empty State Stack
        self._center_stack = QStackedWidget()
        c_lay.addWidget(self._center_stack, 1)

        # Page 0: Clean 4-Column Master Table
        self.model = QStandardItemModel(0, len(COLUMNS))
        self.model.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.proxy = FilterProxy()
        self.proxy.setSourceModel(self.model)

        self.table = HoverTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(60)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        d_buyer = BuyerCellDelegate(self.table)
        d_resi = ResiCellDelegate(self.table)
        d_status = StatusPillDelegate(self.table)
        d_event = EventCellDelegate(self.table)
        self._delegates = [d_buyer, d_resi, d_status, d_event]
        self.table.setItemDelegateForColumn(COL["buyer"], d_buyer)
        self.table.setItemDelegateForColumn(COL["resi"], d_resi)
        self.table.setItemDelegateForColumn(COL["status"], d_status)
        self.table.setItemDelegateForColumn(COL["event"], d_event)

        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setMinimumSectionSize(110)
        hh.setSortIndicatorShown(True)
        hh.setSectionResizeMode(COL["buyer"], QHeaderView.Interactive)
        hh.setSectionResizeMode(COL["resi"], QHeaderView.Interactive)
        hh.setSectionResizeMode(COL["status"], QHeaderView.Fixed)
        hh.setSectionResizeMode(COL["event"], QHeaderView.Stretch)
        self.table.setColumnWidth(COL["buyer"], 176)
        self.table.setColumnWidth(COL["resi"], 244)
        self.table.setColumnWidth(COL["status"], 166)
        self.table.sortByColumn(-1, Qt.AscendingOrder)

        self._dup_group_resis: set[str] = set()
        self._dup_group_label: str = ""
        self._dup_popover = DuplicateGroupPopover(self)
        self._dup_popover.order_selected.connect(self._select_and_focus_resi)
        self._dup_popover.filter_group_requested.connect(self._apply_dup_group_filter)

        self.table.copy_resi_requested.connect(self._copy_resi_inline)
        self.table.duplicate_badge_clicked.connect(self._on_duplicate_badge_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.selectionModel().selectionChanged.connect(self._on_table_selection_changed)
        self.table.clicked.connect(self._on_table_clicked)
        self.table.activated.connect(self._on_table_clicked)
        self._del_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self.table)
        self._del_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._del_shortcut.activated.connect(lambda: self._delete(self.selected_rows()))
        self._center_stack.addWidget(self.table)

        # Page 1: Clean Empty / No Match View
        empty_pg = QWidget()
        e_lay = QVBoxLayout(empty_pg)
        e_lay.setAlignment(Qt.AlignCenter)
        e_lay.setContentsMargins(40, 40, 40, 40)

        e_circle = QFrame()
        e_circle.setObjectName("EmptyCircle")
        e_circle.setFixedSize(56, 56)
        ec_lay = QVBoxLayout(e_circle)
        ec_lay.setAlignment(Qt.AlignCenter)
        self._empty_table_icon = QLabel()
        self._empty_table_icon.setFixedSize(24, 24)
        ec_lay.addWidget(self._empty_table_icon)

        self._empty_table_title = QLabel("Belum ada pesanan di tampilan ini")
        self._empty_table_title.setAlignment(Qt.AlignCenter)
        self._empty_table_title.setStyleSheet("font-size: 15px; font-weight: 600;")

        self._empty_table_sub = QLabel("Impor file export Shopee atau tambahkan nomor resi untuk mulai melacak.")
        self._empty_table_sub.setAlignment(Qt.AlignCenter)
        self._empty_table_sub.setWordWrap(True)
        self._empty_table_sub.setObjectName("MutedText")

        e_btns = QHBoxLayout()
        e_btns.setSpacing(10)
        self._btn_empty_import = QPushButton("Impor File Shopee")
        self._btn_empty_import.setObjectName("SecondaryBtn")
        self._btn_empty_import.setCursor(Qt.PointingHandCursor)
        self._btn_empty_import.clicked.connect(self.import_file)

        self._btn_empty_reset = QPushButton("Reset Filter")
        self._btn_empty_reset.setObjectName("SecondaryBtn")
        self._btn_empty_reset.setCursor(Qt.PointingHandCursor)
        self._btn_empty_reset.clicked.connect(self._reset_filters)
        self._btn_empty_reset.hide()

        e_btns.addStretch(1)
        e_btns.addWidget(self._btn_empty_import)
        e_btns.addWidget(self._btn_empty_reset)
        e_btns.addStretch(1)

        e_lay.addStretch(1)
        e_lay.addWidget(e_circle, 0, Qt.AlignCenter)
        e_lay.addSpacing(14)
        e_lay.addWidget(self._empty_table_title)
        e_lay.addSpacing(6)
        e_lay.addWidget(self._empty_table_sub)
        e_lay.addSpacing(16)
        e_lay.addLayout(e_btns)
        e_lay.addStretch(1)
        self._center_stack.addWidget(empty_pg)

        # ---- 3. Right Native Inspector Panel
        self.inspector = InspectorPanel()
        self.inspector.setMinimumWidth(330)
        self.inspector.refresh_requested.connect(self.track_single_resi)
        self.inspector.track_many_requested.connect(lambda rows: self.track_rows(rows))
        self.inspector.copy_toast.connect(self.statusBar().showMessage)
        self.inspector.open_web_requested.connect(self._open_courier_web_row)
        self.inspector.action_menu_requested.connect(self._open_row_menu_at)
        self.inspector.archive_requested.connect(self._archive_rows)
        self.inspector.delete_requested.connect(self._delete)
        self.inspector.select_resi_requested.connect(self._select_and_focus_resi)
        self.inspector.close_requested.connect(self.close_inspector)

        self._split = QSplitter(Qt.Horizontal)
        self._split.setHandleWidth(1)
        self._split.addWidget(center_pane)
        self._split.addWidget(self.inspector)
        self._split.setStretchFactor(0, 1)
        self._split.setStretchFactor(1, 0)
        self._split.setSizes([815, 337])

        root.addWidget(self._split, 1)
        self.setCentralWidget(central)

    # ------------------------------------------------------------------ Navigation & Filters
    def _refresh_more_menu_icons(self):
        t = get_tokens(self.current_theme == "dark")
        self._act_shopee.setIcon(svg_icon("shopping-bag", t["text_secondary"], 14))
        self._act_export.setIcon(svg_icon("download", t["text_secondary"], 14))
        self._act_template.setIcon(svg_icon("file-spreadsheet", t["text_secondary"], 14))
        self._act_settings_menu.setIcon(svg_icon("settings", t["text_secondary"], 14))
        self._act_clear_all.setIcon(svg_icon("trash", t["danger_fg"], 14))

    def _on_nav_clicked(self, cat_key: str = "", st_key: str = ""):
        self.show_archived = False
        self.btn_arch.setChecked(False)
        self.active_category_filter = cat_key
        self.active_status_filter = st_key
        for k, btn in self.nav_items.items():
            btn.setChecked(k == (cat_key, st_key))

        self._seg_all.setChecked(cat_key == "" and st_key == "")
        self._seg_active.setChecked(cat_key == CAT_ACTIVE)
        self._seg_done.setChecked(cat_key == CAT_FINISHED)
        self._seg_other.setChecked(cat_key == CAT_OTHER)

        if st_key == "":
            title_map = {
                "": "Semua Pesanan",
                CAT_ACTIVE: "Pesanan Berjalan",
                CAT_FINISHED: "Pesanan Selesai",
                CAT_OTHER: "Pesanan Lainnya",
            }
            self._lbl_view_title.setText(title_map.get(cat_key, "Semua Pesanan"))
        else:
            titles = {
                ST_PENDING: "Menunggu Pickup",
                ST_TRANSIT: "Dalam Perjalanan",
                ST_OUT: "Sedang Diantar",
                ST_DELIVERED: "Terkirim",
                ST_COMPLETED: "Selesai · Dana Cair",
                ST_RTS_BALSIS: "Gagal - Balsis",
                ST_RTS_REJECTED: "Gagal - Ditolak",
                ST_RTS_RETURN: "Pengembalian",
                ST_CANCELED_BUYER: "Canceled - Pembeli",
                ST_CANCELED_SYSTEM: "Canceled - Sistem",
                ST_ERROR: "Kendala Lacak",
                FILTER_DUPLICATE: "Pesanan Duplikat",
                ST_UNKNOWN: "Tidak Diketahui",
            }
            suffix = " (Selesai)" if (cat_key == CAT_FINISHED and st_key in RTS_STATUSES) else (
                " (Berjalan)" if (cat_key == CAT_ACTIVE and st_key in RTS_STATUSES) else ""
            )
            self._lbl_view_title.setText(titles.get(st_key, st_key) + suffix)
        self.reload()

    def _on_archive_toggled(self):
        self.show_archived = not self.show_archived
        if self.show_archived:
            self.active_category_filter = ""
            self.active_status_filter = ""
            for btn in self.nav_items.values():
                btn.setChecked(False)
            self._seg_all.setChecked(False)
            self._seg_active.setChecked(False)
            self._seg_done.setChecked(False)
            self._seg_other.setChecked(False)
            self.btn_arch.setChecked(True)
            self._lbl_view_title.setText("Arsip Pesanan")
        else:
            self.btn_arch.setChecked(False)
            self.nav_items[("", "")].setChecked(True)
            self._seg_all.setChecked(True)
            self._lbl_view_title.setText("Semua Pesanan")
        self.reload()

    def _reset_filters(self):
        self.search.clear()
        self._dup_group_resis.clear()
        self._dup_group_label = ""
        if hasattr(self, "_filter_popover"):
            self._filter_popover.blockSignals(True)
            self._filter_popover.reset_all()
            self._filter_popover.blockSignals(False)
        if self.show_archived:
            self.show_archived = False
            self.btn_arch.setChecked(False)
        self._on_nav_clicked("", "")

    def _on_duplicate_badge_clicked(self, resi: str, global_pos: QPoint):
        group_rows = list(self._dup_map.get(resi) or [])
        if len(group_rows) < 2:
            return
        self._dup_popover.show_group(
            global_pos, resi, group_rows, is_dark=(self.current_theme == "dark")
        )

    def _apply_dup_group_filter(self, label: str, resis: list[str]):
        self._dup_group_resis = {str(r) for r in resis if r}
        self._dup_group_label = label or f"{len(self._dup_group_resis)}x"
        if self.show_archived or self.active_category_filter or (
            self.active_status_filter and self.active_status_filter != FILTER_DUPLICATE
        ):
            self._on_nav_clicked("", "")
        else:
            self._filter()

    def _clear_dup_group_filter(self):
        self._dup_group_resis.clear()
        self._dup_group_label = ""
        self._filter()

    def _open_filter_popover(self):
        if self._filter_popover.isVisible():
            self._filter_popover.hide()
            return
        if self._filter_popover.consume_anchor_close():
            return
        self._filter_popover.show_below(self.btn_filter)

    def _refresh_filter_btn_state(self):
        if not hasattr(self, "btn_filter") or not hasattr(self, "_filter_popover"):
            return
        t = get_tokens(self.current_theme == "dark")
        cnt = self._filter_popover.active_group_count() + (1 if self._dup_group_resis else 0)
        if cnt > 0:
            self.btn_filter.setText(f"Filter · {cnt}")
            self.btn_filter.setObjectName("FilterActiveBtn")
            self.btn_filter.setIcon(svg_icon("sliders", t["accent"], 14))
        else:
            self.btn_filter.setText("Filter")
            self.btn_filter.setObjectName("SecondaryBtn")
            self.btn_filter.setIcon(svg_icon("sliders", t["text_primary"], 14))
        self.btn_filter.style().unpolish(self.btn_filter)
        self.btn_filter.style().polish(self.btn_filter)

    def _rebuild_filter_chips(self):
        if not hasattr(self, "_filter_bar") or not hasattr(self, "_filter_popover"):
            return
        while self._filter_bar_lay.count():
            item = self._filter_bar_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        st = self._filter_popover.state()
        # Each tuple: (group_key, chip_text, is_exclude, tooltip)
        chips: list[tuple[str, str, bool, str]] = []
        if self._dup_group_resis:
            chips.append(("dup_group", f"Duplikat: {self._dup_group_label}  ×", False, ""))

        if st["date_preset"]:
            dp = st["date_preset"]
            d_from, d_to = st["date_from"], st["date_to"]
            if dp == "custom" and d_from is not None and d_to is not None:
                dlbl = f"{format_date_short(d_from)} – {format_date_short(d_to)}  ×"
            else:
                dlbl = f"{_DATE_PRESET_LABELS.get(dp, dp)}  ×"
            dtip = (
                f"Tanggal: {format_date_short(d_from)} s/d {format_date_short(d_to)}"
                if (d_from is not None and d_to is not None)
                else ""
            )
            chips.append(("date", dlbl, False, dtip))

        lc_names = {CAT_ACTIVE: "Berjalan", CAT_FINISHED: "Selesai", CAT_OTHER: "Lainnya"}
        for key_name, prefix, is_exc in (
            ("lifecycles_inc", "✓", False),
            ("lifecycles_exc", "✕", True),
        ):
            vals = st[key_name]
            if vals:
                lbls = ", ".join(lc_names.get(k, k) for k in (CAT_ACTIVE, CAT_FINISHED, CAT_OTHER) if k in vals)
                chips.append((key_name, f"{prefix} {lbls}  ×", is_exc, ""))

        for key_name, is_exc in (("statuses_inc", False), ("statuses_exc", True)):
            vals = st[key_name]
            if not vals:
                continue
            if is_exc and vals == {ST_CANCELED_BUYER, ST_CANCELED_SYSTEM}:
                chips.append((key_name, "✕ Tanpa Canceled  ×", True, "Kecuali Canceled - Pembeli, Canceled - Sistem"))
            else:
                ordered = [_FILTER_STATUS_LABELS.get(k, k) for k, _ in _FILTER_STATUS_ITEMS if k in vals]
                prefix = "✕" if is_exc else "✓"
                lbls = f"{len(ordered)} status" if len(ordered) > 2 else ", ".join(ordered)
                chips.append((key_name, f"{prefix} {lbls}  ×", is_exc, ", ".join(ordered)))

        for key_name, prefix, is_exc in (
            ("couriers_inc", "✓", False),
            ("couriers_exc", "✕", True),
        ):
            vals = st[key_name]
            if vals:
                lbls = ", ".join(c for c in COURIERS if c in vals)
                chips.append((key_name, f"{prefix} {lbls}  ×", is_exc, ""))

        for key_name, prefix, is_exc in (
            ("payments_inc", "✓", False),
            ("payments_exc", "✕", True),
        ):
            vals = st[key_name]
            if vals:
                lbls = ", ".join(p for p in ("COD", "QRIS", "ShopeePay", "Lainnya") if p in vals)
                chips.append((key_name, f"{prefix} {lbls}  ×", is_exc, ""))

        if st["dup_filter"] == "only":
            chips.append(("dup_filter", "Duplikat  ×", False, ""))
        elif st["dup_filter"] == "non_dup":
            chips.append(("dup_filter", "Non-Duplikat  ×", False, ""))

        if not chips:
            self._filter_bar.hide()
            return

        for grp, text, is_exc, tip in chips:
            b = QPushButton(text)
            b.setObjectName("FilterChipExclude" if is_exc else "FilterChip")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"{tip}\nKlik untuk menghapus filter ini".strip() if tip else "Klik untuk menghapus filter ini")
            if grp == "dup_group":
                b.clicked.connect(self._clear_dup_group_filter)
            else:
                b.clicked.connect(lambda _c=False, g=grp: self._filter_popover.clear_group(g))
            self._filter_bar_lay.addWidget(b)

        self._filter_bar_lay.addStretch(1)
        btn_clr = QPushButton("Reset semua")
        btn_clr.setObjectName("GhostBtn")
        btn_clr.setCursor(Qt.PointingHandCursor)
        btn_clr.clicked.connect(self._reset_all_checklist_and_group)
        self._filter_bar_lay.addWidget(btn_clr)
        self._filter_bar.show()

    def _reset_all_checklist_and_group(self):
        self._dup_group_resis.clear()
        self._dup_group_label = ""
        self._filter_popover.reset_all()

    def _filter(self):
        self.proxy.text = self.search.text().strip()
        is_dup_view = (not self.show_archived) and (self.active_status_filter == FILTER_DUPLICATE)
        self.proxy.duplicates_only = is_dup_view
        self.table.duplicate_view_mode = is_dup_view or bool(self._dup_group_resis)
        if is_dup_view:
            self.proxy.category = ""
            self.proxy.status = ""
        else:
            self.proxy.category = "" if self.show_archived else self.active_category_filter
            self.proxy.status = self.active_status_filter

        self.proxy.dup_group_resis = set(self._dup_group_resis)

        st = self._filter_popover.state()
        self.proxy.lifecycles_inc = st["lifecycles_inc"]
        self.proxy.lifecycles_exc = st["lifecycles_exc"]
        self.proxy.statuses_inc = st["statuses_inc"]
        self.proxy.statuses_exc = st["statuses_exc"]
        self.proxy.couriers_inc = st["couriers_inc"]
        self.proxy.couriers_exc = st["couriers_exc"]
        self.proxy.payments_inc = st["payments_inc"]
        self.proxy.payments_exc = st["payments_exc"]
        self.proxy.dup_filter = st["dup_filter"]
        self.proxy.date_from = st["date_from"]
        self.proxy.date_to = st["date_to"]

        self.proxy.invalidateFilter()
        self._refresh_filter_btn_state()
        self._rebuild_filter_chips()
        self._update_center_empty_state()

    def _update_center_empty_state(self):
        visible = self.proxy.rowCount()
        self._lbl_view_count.setText(str(visible))
        if visible > 0:
            self._center_stack.setCurrentIndex(0)
            return

        total_in_view = self.model.rowCount()
        has_filter = bool(
            self.proxy.text
            or self._filter_popover.active_group_count() > 0
            or self.proxy.dup_group_resis
            or self.proxy.category
            or self.proxy.status
            or self.proxy.duplicates_only
            or self.show_archived
        )
        if total_in_view == 0 and not has_filter:
            self._empty_table_title.setText("Belum ada pesanan tersimpan")
            self._empty_table_sub.setText("Impor file pesanan Shopee (.xlsx / .csv) atau klik Tambah untuk melacak resi.")
            self._btn_empty_import.show()
            self._btn_empty_reset.hide()
        else:
            self._empty_table_title.setText("Tidak ada pesanan yang cocok")
            self._empty_table_sub.setText("Coba ubah kata kunci pencarian atau filter status di sidebar kiri.")
            self._btn_empty_import.hide()
            self._btn_empty_reset.show()
        self._center_stack.setCurrentIndex(1)

    # ------------------------------------------------------------------ Data Load & Live Updates
    def _build_row_items(self, r_dict: dict) -> list[QStandardItem]:
        status = effective_status(r_dict)
        vals = [
            r_dict.get("buyer_username") or "",
            r_dict.get("resi") or "",
            status,
            r_dict.get("last_event") or r_dict.get("error") or "",
        ]
        items = []
        for idx, txt in enumerate(vals):
            it = QStandardItem(txt)
            it.setData(r_dict["resi"], Qt.UserRole)
            if idx == 0:
                it.setData(r_dict, ROW_DATA_ROLE)
            items.append(it)
        return items

    def reload(self, *_):
        selected = {r["resi"] for r in self.selected_rows()}
        if self.show_archived:
            rows = [dict(r) for r in self.db.all(include_archived=True) if r["archived"] == 1]
        else:
            rows = [dict(r) for r in self.db.all(include_archived=False)]

        row_dup_map = duplicate_resi_map(rows)
        for d in rows:
            d["_dup_count"] = len(row_dup_map.get(d["resi"], []))
            d["_dup_key"] = recipient_dup_key(d)

        if not self.show_archived and self.active_status_filter == FILTER_DUPLICATE:
            rows.sort(key=lambda d: (d.get("_dup_key") or "", d.get("created_at") or "", d.get("resi") or ""))

        self.model.setRowCount(0)
        for d in rows:
            self.model.appendRow(self._build_row_items(d))

        self._update_nav_counts()
        self._filter()

        if selected:
            sm = self.table.selectionModel()
            sm.blockSignals(True)
            sel = QItemSelection()
            last_col = self.proxy.columnCount() - 1
            for i in range(self.proxy.rowCount()):
                if self.proxy.index(i, 0).data(Qt.UserRole) in selected:
                    sel.select(self.proxy.index(i, 0), self.proxy.index(i, last_col))
            if not sel.isEmpty():
                sm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            sm.blockSignals(False)
        self._refresh_inspector()

    def _update_nav_counts(self):
        rows = self.db.all(include_archived=False)
        all_dicts = [dict(r) for r in rows]
        self._dup_map = duplicate_resi_map(all_dicts)
        self.proxy.duplicate_resis = set(self._dup_map.keys())

        pair_counts: Counter[tuple[str, str]] = Counter()
        cat_counts: Counter[str] = Counter()
        for d in all_dicts:
            cat = row_lifecycle(d)
            st = effective_status(d)
            cat_counts[cat] += 1
            pair_counts[(cat, st)] += 1

        total_all = len(all_dicts)
        n_active = cat_counts.get(CAT_ACTIVE, 0)
        n_done = cat_counts.get(CAT_FINISHED, 0)
        n_other = cat_counts.get(CAT_OTHER, 0)

        for key, btn in self.nav_items.items():
            ck, sk = key
            if ck == "" and sk == "":
                btn.set_count(total_all)
            elif sk == "":
                btn.set_count(cat_counts.get(ck, 0))
            else:
                cnt = pair_counts.get((ck, sk), 0)
                btn.set_count(cnt)
                if (ck, sk) == (CAT_ACTIVE, ST_RTS_BALSIS) and isinstance(btn, NavItemButton):
                    btn.set_accent_badge(cnt > 0)

        if hasattr(self, "_err_nav_btn"):
            err_cnt = pair_counts.get((CAT_OTHER, ST_ERROR), 0)
            self._err_nav_btn.set_accent_badge(err_cnt > 0)
        if hasattr(self, "_dup_nav_btn"):
            self._dup_nav_btn.set_count(len(self._dup_map))
        if hasattr(self, "_unk_nav_btn"):
            unk_cnt = pair_counts.get((CAT_OTHER, ST_UNKNOWN), 0)
            is_unk_active = (self.active_category_filter, self.active_status_filter) == (CAT_OTHER, ST_UNKNOWN)
            self._unk_nav_btn.setVisible(unk_cnt > 0 or is_unk_active)

        if hasattr(self, "_seg_all"):
            self._seg_all.setText(f"Semua · {total_all}")
            self._seg_active.setText(f"Berjalan · {n_active}")
            self._seg_done.setText(f"Selesai · {n_done}")
            self._seg_other.setText(f"Lainnya · {n_other}")

        self.btn_arch.set_count(self.db.archived_count())

    def refresh_one(self, resi: str):
        """Update row, sidebar counts, and Inspector instantly when a single tracking finishes."""
        r = self.db.get(resi)
        if r is None:
            return
        d = dict(r)
        d["_dup_count"] = len(self._dup_map.get(resi, []))
        d["_dup_key"] = recipient_dup_key(d)
        status = effective_status(d)

        for i in range(self.model.rowCount()):
            if self.model.item(i, 0).data(Qt.UserRole) == resi:
                self.model.item(i, 0).setData(d, ROW_DATA_ROLE)
                self.model.item(i, COL["buyer"]).setText(d.get("buyer_username") or "")
                self.model.item(i, COL["resi"]).setText(d.get("resi") or "")
                self.model.item(i, COL["status"]).setText(status)
                self.model.item(i, COL["event"]).setText(d.get("last_event") or d.get("error") or "")
                break

        self.proxy.invalidateFilter()
        self._update_nav_counts()
        self._update_center_empty_state()

        if any(x["resi"] == resi for x in self.selected_rows()):
            self._refresh_inspector()

    def selected_rows(self) -> list[dict]:
        if not hasattr(self, "table"):
            return []
        idxs = sorted(self.table.selectionModel().selectedRows(), key=lambda i: i.row())
        resis: list[str] = []
        seen: set[str] = set()
        for idx in idxs:
            val = self.proxy.index(idx.row(), 0).data(Qt.UserRole)
            if val and val not in seen:
                seen.add(val)
                resis.append(val)
        return [dict(r) for r in (self.db.get(x) for x in resis) if r is not None]

    # ------------------------------------------------------------------ Collapsible Inspector & Single-Click UX
    def _refresh_inspector_toggle_btn(self):
        if not hasattr(self, "btn_toggle_inspector"):
            return
        t = get_tokens(self.current_theme == "dark")
        is_open = self.inspector.isVisible() if hasattr(self, "inspector") else True
        color = t["accent"] if is_open else t["text_secondary"]
        self.btn_toggle_inspector.setIcon(svg_icon("panel-right", color, 15))
        self.btn_toggle_inspector.setToolTip(
            "Tutup panel detail kanan (Esc)" if is_open else "Tampilkan panel detail kanan"
        )

    def close_inspector(self, save: bool = True):
        if hasattr(self, "inspector") and not self.inspector.isHidden():
            if self.inspector.width() >= 280:
                self._saved_inspector_width = self.inspector.width()
            self.inspector.hide()
            self._split.setHandleWidth(0)
        if save:
            self.settings["inspector_open"] = False
            save_settings(self.settings)
        self._refresh_inspector_toggle_btn()

    def open_inspector(self, save: bool = True):
        if hasattr(self, "inspector") and self.inspector.isHidden():
            self._split.setHandleWidth(1)
            self.inspector.show()
            total_w = max(800, self._split.width())
            insp_w = min(max(340, self._saved_inspector_width), max(340, int(total_w * 0.45)))
            self._split.setSizes([max(400, total_w - insp_w), insp_w])
        if save:
            self.settings["inspector_open"] = True
            save_settings(self.settings)
        self._refresh_inspector_toggle_btn()

    def toggle_inspector(self):
        if self.inspector.isHidden():
            self.open_inspector()
        else:
            self.close_inspector()

    def _maybe_auto_track(self, row: dict):
        resi = row.get("resi") or ""
        if not resi or resi.startswith("ORDER-"):
            return
        if row.get("last_checked") or not row.get("courier"):
            return
        if self._batch_worker is not None and any(r["resi"] == resi for r in self._batch_worker.rows):
            return
        if self._single_worker is not None and self._single_worker.rows and self._single_worker.rows[0]["resi"] == resi:
            return
        self.track_single_resi(resi)

    def _on_table_clicked(self, _index=None):
        """Opening/clicking any order row opens the inspector & auto-tracks if untracked."""
        self.open_inspector()
        self._refresh_inspector()
        rows = self.selected_rows()
        if len(rows) == 1:
            self._maybe_auto_track(rows[0])

    def _on_table_selection_changed(self, *_):
        self._refresh_inspector()
        if hasattr(self, "inspector") and self.inspector.isVisible():
            rows = self.selected_rows()
            if len(rows) == 1:
                self._maybe_auto_track(rows[0])

    def _refresh_inspector(self):
        self.inspector.set_rows(self.selected_rows(), dup_map=self._dup_map)

    def _select_and_focus_resi(self, resi: str):
        if not resi:
            return

        def _find_proxy_row() -> int:
            for i in range(self.proxy.rowCount()):
                if self.proxy.index(i, 0).data(Qt.UserRole) == resi:
                    return i
            return -1

        target_row = _find_proxy_row()
        if target_row == -1:
            self._on_nav_clicked("", "")
            target_row = _find_proxy_row()
        if target_row == -1:
            self.search.clear()
            self._filter_popover.reset_all()
            target_row = _find_proxy_row()
        if target_row >= 0:
            self.table.selectRow(target_row)
            self.table.scrollTo(self.proxy.index(target_row, 0))
            self.open_inspector()
            self._refresh_inspector()

    def _auto_sync_stale_on_startup(self):
        if self._batch_worker is not None:
            return
        max_age = int(self.settings.get("auto_refresh_minutes", 60) or 60)
        stale = self.db.stale_active_resis(max_age_minutes=max_age)
        if stale:
            self.track_rows(stale, auto=True)

    def track_single_resi(self, resi: str):
        """Non-blocking single-resi refresh decoupled from batch worker."""
        if not resi or resi.startswith("ORDER-"):
            self.statusBar().showMessage("Pesanan ini belum memiliki nomor resi dari ekspedisi.")
            return
        if self._single_worker is not None and self._single_worker.rows and self._single_worker.rows[0]["resi"] == resi:
            return
        row = self.db.get(resi)
        if not row:
            return
        if self._single_worker is not None:
            self._single_worker.cancelled = True

        self.inspector.set_refreshing(resi)
        self.statusBar().showMessage(f"Melacak {resi} ({row['courier'] or '?'})...")

        w = TrackWorker(self.db, [row], self.settings, self._shopee())
        self._single_worker = w
        self._active_workers.add(w)
        w.signals.item_done.connect(self.refresh_one)

        def finish_single(msg):
            self._active_workers.discard(w)
            if self._single_worker is w:
                self._single_worker = None
            self.inspector.set_refreshing("")
            self.statusBar().showMessage(f"Resi {resi} diperbarui.")

        def err_single(msg):
            self._active_workers.discard(w)
            if self._single_worker is w:
                self._single_worker = None
            self.inspector.set_refreshing("")
            self.statusBar().showMessage(msg)

        w.signals.finished.connect(finish_single)
        w.signals.error.connect(err_single)
        self.pool.start(w)

    def _on_batch_btn_clicked(self):
        if self._batch_worker is not None:
            self.stop_batch()
        else:
            self.track_rows(self.db.active_resis(), auto=False)

    def _refresh_batch_button_state(self):
        is_dark = self.current_theme == "dark"
        t = get_tokens(is_dark)
        if self._batch_worker is not None:
            self.btn_batch_track.setText("Hentikan")
            self.btn_batch_track.setIcon(svg_icon("stop", t["danger_fg"], 14))
            self.btn_batch_track.setObjectName("DangerBtn")
        else:
            self.btn_batch_track.setText("Lacak")
            self.btn_batch_track.setIcon(svg_icon("refresh", t["text_primary"], 14))
            self.btn_batch_track.setObjectName("SecondaryBtn")
        # Re-polish button so objectName style switches cleanly
        self.btn_batch_track.style().unpolish(self.btn_batch_track)
        self.btn_batch_track.style().polish(self.btn_batch_track)

    def track_rows(self, rows, auto: bool = False):
        rows = [r for r in rows if r is not None]
        if self._batch_worker is not None:
            if not auto:
                QMessageBox.information(self, "Sedang Berjalan", "Proses pelacakan massal masih berjalan.")
            return
        if not rows:
            if not auto:
                self.statusBar().showMessage("Tidak ada resi aktif yang perlu dilacak.")
            return

        total = len(rows)
        w = TrackWorker(self.db, rows, self.settings, self._shopee())
        self._batch_worker = w
        self._active_workers.add(w)
        self._refresh_batch_button_state()

        self._sync_prog.setRange(0, total)
        self._sync_prog.setValue(0)
        self._sync_lbl.setText(f"Menyinkronkan 0/{total} pesanan aktif...")
        self._sync_banner.show()

        def on_prog_count(done_cnt: int, tot: int, cur_resi: str):
            self._sync_prog.setValue(done_cnt)
            self._sync_lbl.setText(f"Menyinkronkan {done_cnt}/{tot} · {cur_resi}")

        def on_done(msg: str):
            self._active_workers.discard(w)
            if self._batch_worker is w:
                self._batch_worker = None
            self._sync_banner.hide()
            self._refresh_batch_button_state()
            self._update_nav_counts()
            if not w.cancelled:
                self._record_last_sync(shopee_synced=False)
            self.statusBar().showMessage(msg)

        def on_err(msg: str):
            self._active_workers.discard(w)
            if self._batch_worker is w:
                self._batch_worker = None
            self._sync_banner.hide()
            self._refresh_batch_button_state()
            self.statusBar().showMessage(msg)

        w.signals.item_done.connect(self.refresh_one)
        w.signals.progress_count.connect(on_prog_count)
        w.signals.finished.connect(on_done)
        w.signals.error.connect(on_err)
        self.pool.start(w)

    def stop_batch(self):
        if self._batch_worker is not None:
            self._batch_worker.cancelled = True
            self._sync_lbl.setText("Menghentikan pelacakan...")
        if self._sync_worker is not None:
            self._sync_worker.cancelled = True
            self._sync_lbl.setText("Membatalkan sinkronisasi Shopee...")

    # ------------------------------------------------------------------ Drag & Drop
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith((".xlsx", ".xls", ".csv", ".txt")):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths: list[str] = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = url.toLocalFile()
                if p.lower().endswith((".xlsx", ".xls", ".csv", ".txt")):
                    paths.append(p)
        if paths:
            self._import_paths(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    # ------------------------------------------------------------------ Actions & Import
    def add_resi(self):
        d = AddDialog(self)
        if d.exec():
            recs = d.records()
            if not recs:
                return
            for rec in recs:
                self.db.upsert(rec)
            self.reload()
            rows = [self.db.get(r["resi"]) for r in recs if self.db.get(r["resi"]) is not None]
            self.statusBar().showMessage(f"{len(recs)} resi disimpan — menyinkronkan ke kurir...")
            if len(rows) == 1:
                self.track_single_resi(rows[0]["resi"])
            else:
                self.track_rows(rows, auto=True)

    def import_file(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Pilih File Export Shopee (Bisa Lebih dari Satu)",
            "",
            "Semua Format Pesanan (*.xlsx *.xls *.csv *.txt);;Excel Spreadsheet (*.xlsx *.xls);;CSV Text (*.csv *.txt);;Semua File (*.*)",
        )
        if paths:
            self._import_paths(paths)

    def _import_path(self, path: str):
        self._import_paths([path])

    def _import_paths(self, paths: list[str]):
        clean_paths = [str(p) for p in paths if str(p).strip()]
        if not clean_paths:
            return

        total_new = total_upd = total_dup = 0
        files_ok = 0
        changed_resis: set[str] = set()
        all_warns: list[str] = []
        file_errors: list[str] = []

        for path in clean_paths:
            fname = Path(path).name
            try:
                recs, warns = importer.parse_file(path)
            except Exception as e:
                file_errors.append(f"• {fname}: {e}")
                continue
            files_ok += 1
            for w in warns:
                msg = f"[{fname}] {w}" if len(clean_paths) > 1 else w
                if msg not in all_warns:
                    all_warns.append(msg)
            for rec in recs:
                rec["order_source"] = "csv"
                res = self.db.upsert(rec)
                if res == "new":
                    total_new += 1
                    changed_resis.add(rec["resi"])
                elif res == "updated":
                    total_upd += 1
                    changed_resis.add(rec["resi"])
                else:
                    total_dup += 1

        if files_ok == 0 and file_errors:
            QMessageBox.critical(self, "Gagal Membaca File", "File tidak dapat dibaca:\n\n" + "\n".join(file_errors))
            return

        self.reload()
        prefix = f"Impor {files_ok} file selesai" if len(clean_paths) > 1 else "Impor selesai"
        if total_new == 0 and total_upd == 0 and total_dup > 0 and not file_errors:
            summary = f"Semua {total_dup} pesanan sudah ada di daftar (dilewati otomatis agar tidak dobel)."
            self.statusBar().showMessage(summary)
            QMessageBox.information(self, "Data Sudah Ada", summary)
        else:
            parts = []
            if total_new:
                parts.append(f"{total_new} baru")
            if total_upd:
                parts.append(f"{total_upd} diperbarui")
            if total_dup:
                parts.append(f"{total_dup} sudah ada (dilewati)")
            summary = f"{prefix}: {', '.join(parts)}." if parts else f"{prefix}."
            self.statusBar().showMessage(summary)
            notes = file_errors + all_warns
            if notes:
                QMessageBox.warning(self, "Catatan Impor", summary + "\n\n" + "\n".join(notes))

        # Auto-sync active untracked orders from changed imports seamlessly
        if changed_resis and self._batch_worker is None:
            to_track = [r for r in self.db.stale_active_resis(max_age_minutes=60) if r["resi"] in changed_resis]
            if to_track:
                self.track_rows(to_track, auto=True)

    def _shopee(self, settings=None):
        def persist(s):
            self.settings.update({k: v for k, v in s.items() if k.startswith("shopee_")})
            save_settings(self.settings)

        return ShopeeClient(settings if settings is not None else self.settings, on_tokens_changed=persist)

    def _record_last_sync(self, shopee_synced: bool = False) -> None:
        now = datetime.now()
        self.settings["shopee_last_sync_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        if shopee_synced:
            self.settings["shopee_last_sync_ts"] = int(time.time())
        save_settings(self.settings)
        self._refresh_last_sync_label()

    def _refresh_last_sync_label(self) -> None:
        if not hasattr(self, "_lbl_last_sync"):
            return
        raw = str(self.settings.get("shopee_last_sync_at") or "").strip()
        if not raw:
            self._lbl_last_sync.hide()
            return
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw[:len(fmt) + 2], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            self._lbl_last_sync.hide()
            return
        if dt.date() == date.today():
            short_txt = f"Diperbarui {dt.strftime('%H:%M')}"
        else:
            short_txt = f"Diperbarui {format_date_short(dt.date())[:6]} {dt.strftime('%H:%M')}"
        self._lbl_last_sync.setText(short_txt)
        mins = max(1, int(self.settings.get("shopee_auto_sync_minutes", 2) or 2))
        auto_on = bool(self.settings.get("shopee_auto_sync_enabled", True)) and self._shopee().authorised()
        auto_note = f"Auto Shopee tiap {mins} menit" if auto_on else "Auto Shopee nonaktif"
        self._lbl_last_sync.setToolTip(
            f"Terakhir diperbarui: {format_date_short(dt.date())} {dt.strftime('%H:%M')}\n{auto_note}"
        )
        self._lbl_last_sync.show()

    def _auto_sync_shopee_realtime(self):
        if self._sync_worker is not None or self._batch_worker is not None:
            return
        client = self._shopee()
        if not client.authorised() or not self.settings.get("shopee_auto_sync_enabled", True):
            return

        last_ts = int(self.settings.get("shopee_last_sync_ts") or 0)
        since_ts = max(0, last_ts - 300) if last_ts > 0 else max(0, int(time.time()) - 86400)
        w = ShopeeSyncWorker(self.db, client, days=1, since_ts=since_ts, time_range_field="update_time")
        self._sync_worker = w
        self._active_workers.add(w)
        sync_res = {"new": 0, "upd": 0, "resis": []}

        def on_sync_completed(new_cnt: int, upd_cnt: int, resis: list):
            sync_res["new"] = new_cnt
            sync_res["upd"] = upd_cnt
            sync_res["resis"] = list(resis)

        def on_done(msg):
            self._active_workers.discard(w)
            if self._sync_worker is w:
                self._sync_worker = None
            if not w.cancelled:
                self._record_last_sync(shopee_synced=True)
            if sync_res["new"] > 0 or sync_res["upd"] > 0:
                self.reload()
                self.statusBar().showMessage(
                    f"Shopee realtime: {sync_res['new']} pesanan baru, {sync_res['upd']} diperbarui."
                )
                rows_to_track = [
                    r for r in (self.db.get(resi) for resi in sync_res["resis"])
                    if r is not None and not str(r["resi"] or "").startswith("ORDER-")
                ]
                if rows_to_track:
                    self.track_rows(rows_to_track, auto=True)

        def on_err(msg):
            self._active_workers.discard(w)
            if self._sync_worker is w:
                self._sync_worker = None
            self.statusBar().showMessage(f"Auto-sync Shopee tertunda: {msg}")

        w.signals.sync_completed.connect(on_sync_completed)
        w.signals.finished.connect(on_done)
        w.signals.error.connect(on_err)
        self.pool.start(w)

    def sync_shopee(self):
        if self._sync_worker is not None or self._batch_worker is not None:
            QMessageBox.information(self, "Sedang Berjalan", "Proses sinkronisasi atau pelacakan masih berjalan.")
            return
        client = self._shopee()
        if not client.authorised():
            QMessageBox.information(
                self,
                "Belum Terhubung ke Shopee",
                "Akun Shopee Open Platform belum terotorisasi.\nBuka Pengaturan > Shopee API untuk menghubungkan tokomu.",
            )
            self.open_settings()
            return
        dlg = ShopeeSyncDialog(self)
        if not dlg.exec():
            return
        d_from, d_to = dlg.date_range()
        since_ts, until_ts = dlg.timestamp_range()
        days_span = max(1, (d_to - d_from).days + 1)
        range_label = (
            format_date_short(d_from)
            if d_from == d_to
            else f"{format_date_short(d_from)} – {format_date_short(d_to)}"
        )
        w = ShopeeSyncWorker(
            self.db,
            client,
            days=days_span,
            since_ts=since_ts,
            until_ts=until_ts,
            time_range_field="create_time",
        )
        self._sync_worker = w
        self._active_workers.add(w)
        self._sync_prog.setRange(0, 0)
        self._sync_lbl.setText(f"Menarik pesanan Shopee ({range_label})...")
        self._sync_banner.show()

        def on_done(msg):
            self._active_workers.discard(w)
            self._sync_worker = None
            self._sync_banner.hide()
            if not w.cancelled:
                self._record_last_sync(shopee_synced=(d_to >= date.today()))
            self.reload()
            self.statusBar().showMessage(msg)
            self.track_rows(self.db.active_resis(), auto=True)

        def on_err(msg):
            self._active_workers.discard(w)
            self._sync_worker = None
            self._sync_banner.hide()
            self.statusBar().showMessage(msg)
            QMessageBox.critical(self, "Gagal Sinkron Shopee", msg)

        w.signals.progress.connect(self._sync_lbl.setText)
        w.signals.finished.connect(on_done)
        w.signals.error.connect(on_err)
        self.pool.start(w)

    def export(self):
        rows = self.db.all(include_archived=self.show_archived)
        if not rows:
            QMessageBox.information(self, "Data Kosong", "Belum ada pesanan untuk diekspor.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Simpan File CSV", "resi_pesanan_shopee.csv", "CSV File (*.csv)")
        if not path:
            return
        try:
            importer.export_csv(path, rows)
            self.statusBar().showMessage(f"Data {len(rows)} pesanan diekspor ke {path}")
        except OSError as e:
            QMessageBox.critical(self, "Gagal Ekspor CSV", f"File tidak dapat disimpan:\n\n{e}")

    def save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Simpan Template CSV", "template_resi_shopee.csv", "CSV File (*.csv)")
        if not path:
            return
        try:
            importer.write_template(path)
            self.statusBar().showMessage(f"Template disimpan di {path}")
        except OSError as e:
            QMessageBox.critical(self, "Gagal Menyimpan Template", f"File tidak dapat disimpan:\n\n{e}")

    def open_settings(self):
        d = SettingsDialog(self.settings, lambda s: self._shopee(s), self)
        if d.exec():
            self.settings = d.result_settings()
            save_settings(self.settings)
            new_theme = self.settings.get("theme", "light")
            if new_theme != self.current_theme:
                self.apply_theme(new_theme)
            self._apply_timer()

    def _apply_timer(self):
        mins = int(self.settings.get("auto_refresh_minutes") or 0)
        if mins > 0:
            self.timer.start(mins * 60 * 1000)
        else:
            self.timer.stop()
        if hasattr(self, "_shopee_sync_timer") and self._shopee_sync_timer is not None:
            shp_mins = int(self.settings.get("shopee_auto_sync_minutes") or 0)
            if (
                self._auto_sync_enabled
                and self.settings.get("shopee_auto_sync_enabled", True)
                and shp_mins > 0
                and self._shopee().authorised()
            ):
                self._shopee_sync_timer.start(max(1, shp_mins) * 60 * 1000)
            else:
                self._shopee_sync_timer.stop()
        self._refresh_last_sync_label()

    def _open_courier_web_row(self, r: dict):
        resi = r.get("resi") or ""
        courier = (r.get("courier") or "").upper()
        if courier == "SPX":
            url = f"https://spx.co.id/track?spx_tn={resi}"
        elif courier == "ANTERAJA":
            url = "https://anteraja.id/id/tracking"
        elif courier == "JNE":
            url = "https://www.jne.co.id/tracking-package"
        elif courier == "POS":
            url = "https://www.posindonesia.co.id/id/tracking"
        else:
            url = f"https://cekresi.com/?noresi={resi}"
        webbrowser.open(url)

    # ------------------------------------------------------------------ Context & Row Action Menus
    def _build_row_menu(self, rows: list[dict], from_inspector: bool = False) -> QMenu:
        t = get_tokens(self.current_theme == "dark")
        m = QMenu(self)
        if not from_inspector:
            m.addAction(svg_icon("panel-right", t["text_secondary"], 14), "Lihat Detail & Lacak", lambda: self._on_table_clicked())
        m.addAction(svg_icon("refresh", t["text_secondary"], 14), "Lacak Sekarang", lambda: self.track_rows(rows))
        m.addAction(svg_icon("copy", t["text_secondary"], 14), "Salin Nomor Resi",
                    lambda: (QGuiApplication.clipboard().setText("\n".join(r["resi"] for r in rows)),
                             self.statusBar().showMessage("Nomor resi disalin.")))
        m.addAction(svg_icon("user", t["text_secondary"], 14), "Salin Username Buyer",
                    lambda: (QGuiApplication.clipboard().setText("\n".join(r["buyer_username"] for r in rows if r.get("buyer_username"))),
                             self.statusBar().showMessage("Username buyer disalin.")))
        if len(rows) == 1:
            m.addAction(svg_icon("external", t["text_secondary"], 14), "Buka Web Resmi Kurir",
                        lambda: self._open_courier_web_row(rows[0]))
        m.addSeparator()

        if len(rows) == 1:
            m.addAction(svg_icon("edit", t["text_secondary"], 14), "Ubah Username Buyer",
                        lambda: self._edit(rows[0], "buyer_username", "Username Buyer"))
            m.addAction("Ubah No. Pesanan", lambda: self._edit(rows[0], "order_sn", "No. Pesanan Shopee"))
            m.addAction("Ubah Status Shopee", lambda: self._edit(rows[0], "order_status", "Status di Shopee (Selesai/Batal/Dikirim)"))

        cm = m.addMenu("Ganti Ekspedisi")
        for c in COURIERS:
            cm.addAction(c, lambda c=c: self._set_field(rows, "courier", c))

        sm = m.addMenu("Atur Status Manual")
        for s in ALL_STATUSES:
            if s == ST_ERROR:
                continue
            sm.addAction(s, lambda s=s: ([self.db.set_status(r["resi"], s) for r in rows], self.reload()))

        m.addSeparator()
        if self.show_archived:
            m.addAction(svg_icon("archive", t["text_secondary"], 14), "Keluarkan dari Arsip",
                        lambda: self._archive_rows(rows, archived=False))
        else:
            m.addAction(svg_icon("archive", t["text_secondary"], 14), "Masukkan ke Arsip",
                        lambda: self._archive_rows(rows, archived=True))
        del_label = "Hapus Pesanan" if len(rows) == 1 else f"Hapus {len(rows)} Pesanan"
        m.addAction(svg_icon("trash", t["danger_fg"], 14), del_label, lambda: self._delete(rows))
        return m

    def _context_menu(self, pos):
        rows = self.selected_rows()
        if not rows:
            return
        m = self._build_row_menu(rows)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _open_row_menu_at(self, global_pos, row_dict: dict):
        m = self._build_row_menu([row_dict], from_inspector=True)
        m.exec(global_pos)

    def _edit(self, row: dict, field: str, label: str):
        val, ok = QInputDialog.getText(self, "Ubah Data", f"{label}:", text=row.get(field, ""))
        if ok:
            self.db.update_field(row["resi"], field, val.strip())
            self.refresh_one(row["resi"])

    def _set_field(self, rows: list[dict], field: str, value: str):
        for r in rows:
            self.db.update_field(r["resi"], field, value)
        self.reload()

    def _copy_resi_inline(self, resi: str):
        if not resi or resi == "-":
            return
        QGuiApplication.clipboard().setText(resi)
        self.table.flash_copied_resi(resi)
        self.statusBar().showMessage(f"Nomor resi {resi} disalin ke clipboard.")

    def _archive_rows(self, rows: list[dict], archived: bool = True):
        self.db.set_archived([r["resi"] for r in rows], archived)
        self.reload()

    def _delete(self, rows: list[dict]):
        if not rows:
            return
        if QMessageBox.question(self, "Hapus Pesanan", f"Hapus {len(rows)} pesanan ini dari daftar?") == QMessageBox.Yes:
            self.db.delete([r["resi"] for r in rows])
            self.reload()

    def clear_all_data(self):
        """Confirm and delete all shipment records (including archived) so the user can re-import from scratch."""
        total = len(self.db.all(include_archived=True))
        if total == 0:
            QMessageBox.information(self, "Belum Ada Data", "Daftar resi sudah kosong.")
            return
        reply = QMessageBox.warning(
            self,
            "Hapus Semua Data Resi",
            f"Hapus seluruh {total} data resi (termasuk arsip)?\n\n"
            "Tindakan ini tidak dapat dibatalkan, setelah itu Anda bisa mengimpor ulang file dari awal.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.stop_batch()
        if self._single_worker is not None:
            self._single_worker.cancelled = True
        deleted = self.db.clear_all(include_archived=True)
        self._reset_filters()
        self.inspector.set_rows([])
        self.statusBar().showMessage(f"Semua {deleted} data resi berhasil dihapus. Siap impor ulang.")

    def closeEvent(self, event):
        if hasattr(self, "timer") and self.timer is not None:
            self.timer.stop()
        if hasattr(self, "_shopee_sync_timer") and self._shopee_sync_timer is not None:
            self._shopee_sync_timer.stop()
        if self._batch_worker is not None:
            self._batch_worker.cancelled = True
        if self._single_worker is not None:
            self._single_worker.cancelled = True
        if self._sync_worker is not None:
            self._sync_worker.cancelled = True
        for w in list(self._active_workers):
            if hasattr(w, "cancelled"):
                w.cancelled = True
        self.pool.waitForDone(1500)
        self.db.close()
        super().closeEvent(event)
