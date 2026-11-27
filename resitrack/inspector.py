"""Native right-hand Inspector panel & vertical courier timeline (zero QTextBrowser HTML hacks)."""
from __future__ import annotations

import json
from collections import Counter

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .icons import svg_icon, svg_pixmap
from .models import (
    CAT_ACTIVE,
    CAT_FINISHED,
    CAT_OTHER,
    ST_CANCELED_BUYER,
    ST_CANCELED_SYSTEM,
    ST_COMPLETED,
    ST_DELIVERED,
    ST_ERROR,
    ST_OUT,
    ST_RTS_BALSIS,
    ST_RTS_REJECTED,
    ST_RTS_RETURN,
    ST_UNKNOWN,
    effective_status,
    format_idr,
    payment_details_dict,
    payment_short_label,
    row_lifecycle,
)
from .theme import courier_style, duplicate_style, get_tokens, payment_style, status_style


class _StatusPill(QFrame):
    """Minimal soft pill with 6px colored dot and crisp status text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setFixedHeight(26)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 11, 0)
        lay.setSpacing(7)

        self.dot = QLabel()
        self.dot.setFixedSize(7, 7)
        self.lbl = QLabel()
        self.lbl.setStyleSheet("font-size: 12px; font-weight: 600;")

        lay.addWidget(self.dot, 0, Qt.AlignVCenter)
        lay.addWidget(self.lbl, 0, Qt.AlignVCenter)

    def set_status(self, status: str, is_dark: bool) -> None:
        bg, fg, dot_col, short_lbl = status_style(status, is_dark)
        self.setStyleSheet(f"_StatusPill {{ background-color: {bg}; border-radius: 12px; border: none; }}")
        self.dot.setStyleSheet(f"background-color: {dot_col}; border-radius: 3px;")
        self.lbl.setText(short_lbl or ST_UNKNOWN)
        self.lbl.setStyleSheet(f"color: {fg}; font-size: 12px; font-weight: 600; background: transparent;")
        self.setToolTip(status or ST_UNKNOWN)


class _TimelineRow(QWidget):
    """Single checkpoint row painting its own crisp vertical rail & dot."""

    def __init__(self, time_str: str, desc: str, loc: str, is_first: bool, is_last: bool, is_dark: bool, parent=None):
        super().__init__(parent)
        self._is_first = is_first
        self._is_last = is_last
        self._is_dark = is_dark
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        lay.setContentsMargins(26, 2, 0, 14 if not is_last else 4)
        lay.setSpacing(3)

        t = get_tokens(is_dark)
        lbl_time = QLabel(time_str or "-")
        lbl_time.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl_time.setCursor(Qt.IBeamCursor)
        lbl_time.setStyleSheet(
            f"font-size: 11px; font-weight: {'700' if is_first else '600'}; "
            f"color: {t['accent'] if is_first else t['text_muted']};"
        )
        lay.addWidget(lbl_time)

        lbl_desc = QLabel(desc or "-")
        lbl_desc.setWordWrap(True)
        lbl_desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl_desc.setCursor(Qt.IBeamCursor)
        lbl_desc.setStyleSheet(
            f"font-size: 12.5px; font-weight: {'500' if is_first else '400'}; "
            f"color: {t['text_primary'] if is_first else t['text_secondary']};"
        )
        lay.addWidget(lbl_desc)

        if loc:
            row_loc = QHBoxLayout()
            row_loc.setContentsMargins(0, 1, 0, 0)
            row_loc.setSpacing(5)
            pin = QLabel()
            pin.setPixmap(svg_pixmap("map-pin", t["text_muted"], 11))
            pin.setFixedSize(12, 12)
            lbl_loc = QLabel(loc)
            lbl_loc.setWordWrap(True)
            lbl_loc.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lbl_loc.setCursor(Qt.IBeamCursor)
            lbl_loc.setStyleSheet(f"font-size: 11px; color: {t['text_muted']};")
            row_loc.addWidget(pin, 0, Qt.AlignTop)
            row_loc.addWidget(lbl_loc, 1)
            lay.addLayout(row_loc)

    def paintEvent(self, event):
        super().paintEvent(event)
        t = get_tokens(self._is_dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        cx = 9.0
        cy = 9.0

        # Vertical rail line
        rail_pen = QPen(QColor(t["border_control"]), 1.5)
        p.setPen(rail_pen)
        if not self._is_first:
            p.drawLine(int(cx), 0, int(cx), int(cy))
        if not self._is_last:
            p.drawLine(int(cx), int(cy), int(cx), self.height())

        # Node dot
        if self._is_first:
            halo = QColor(t["accent"])
            halo.setAlpha(45)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(int(cx - 8), int(cy - 8), 16, 16)
            p.setBrush(QColor(t["accent"]))
            p.drawEllipse(int(cx - 4), int(cy - 4), 8, 8)
        else:
            p.setPen(QPen(QColor(t["bg_surface"]), 1.5))
            p.setBrush(QColor(t["border_hover"]))
            p.drawEllipse(int(cx - 3.5), int(cy - 3.5), 7, 7)

        p.end()


class TimelineWidget(QWidget):
    """Vertical list of native tracking checkpoints."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._lay = QVBoxLayout(self)
        self._lay.setSizeConstraint(QLayout.SetMinimumSize)
        self._lay.setContentsMargins(0, 4, 0, 0)
        self._lay.setSpacing(0)

    def set_events(self, events: list[dict], is_dark: bool, is_placeholder: bool = False) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        t = get_tokens(is_dark)
        if not events:
            msg = (
                "Belum ada nomor resi."
                if is_placeholder
                else "Belum ada riwayat dari kurir."
            )
            empty = QLabel(msg)
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color: {t['text_muted']}; font-size: 12px; padding: 14px; "
                f"background-color: {t['bg_subtle']}; border-radius: 8px;"
            )
            self._lay.addWidget(empty)
            return

        total = len(events)
        for idx, ev in enumerate(events):
            row = _TimelineRow(
                time_str=ev.get("time", ""),
                desc=ev.get("description", ""),
                loc=ev.get("location", ""),
                is_first=(idx == 0),
                is_last=(idx == total - 1),
                is_dark=is_dark,
            )
            self._lay.addWidget(row)


class InspectorPanel(QWidget):
    """Clean native detail inspector with Empty / Single Order / Multi-Select views."""

    refresh_requested = Signal(str)
    track_many_requested = Signal(list)
    copy_toast = Signal(str)
    open_web_requested = Signal(dict)
    action_menu_requested = Signal(QPoint, dict)
    archive_requested = Signal(list, bool)
    delete_requested = Signal(list)
    select_resi_requested = Signal(str)
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InspectorRoot")
        self._is_dark = False
        self._current_rows: list[dict] = []
        self._dup_map: dict[str, list[dict]] = {}
        self._refreshing_resi: str = ""
        self._multi_archive_target: bool = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        self._build_empty_page()
        self._build_single_page()
        self._build_multi_page()
        self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------------ Page Builders
    def _make_close_btn(self) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("GhostIconBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip("Tutup panel detail (Esc)")
        btn.clicked.connect(self.close_requested.emit)
        return btn

    def _build_empty_page(self):
        page = QWidget()
        v_pg = QVBoxLayout(page)
        v_pg.setContentsMargins(0, 0, 0, 0)
        v_pg.setSpacing(0)

        top_bar = QWidget()
        top_bar.setFixedHeight(52)
        tb_lay = QHBoxLayout(top_bar)
        tb_lay.setContentsMargins(16, 0, 14, 0)
        tb_lay.addStretch(1)
        self._btn_close_empty = self._make_close_btn()
        tb_lay.addWidget(self._btn_close_empty)
        v_pg.addWidget(top_bar)

        lay = QVBoxLayout()
        lay.setContentsMargins(36, 12, 36, 48)
        lay.setAlignment(Qt.AlignCenter)

        circle = QFrame()
        circle.setObjectName("EmptyCircle")
        circle.setFixedSize(56, 56)
        c_lay = QVBoxLayout(circle)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setAlignment(Qt.AlignCenter)
        self._empty_icon = QLabel()
        self._empty_icon.setFixedSize(24, 24)
        c_lay.addWidget(self._empty_icon)

        self._empty_title = QLabel("Pilih pesanan")
        self._empty_title.setAlignment(Qt.AlignCenter)
        self._empty_title.setStyleSheet("font-size: 15px; font-weight: 600;")

        self._empty_sub = QLabel("Klik baris pesanan untuk melihat detail & riwayat resi.")
        self._empty_sub.setWordWrap(True)
        self._empty_sub.setAlignment(Qt.AlignCenter)
        self._empty_sub.setObjectName("MutedText")

        lay.addStretch(1)
        lay.addWidget(circle, 0, Qt.AlignCenter)
        lay.addSpacing(14)
        lay.addWidget(self._empty_title)
        lay.addSpacing(6)
        lay.addWidget(self._empty_sub)
        lay.addStretch(1)
        v_pg.addLayout(lay, 1)
        self._stack.addWidget(page)

    def _build_single_page(self):
        page = QWidget()
        v_page = QVBoxLayout(page)
        v_page.setContentsMargins(0, 0, 0, 0)
        v_page.setSpacing(0)

        # Header Bar (64px)
        header = QWidget()
        header.setObjectName("InspectorHeader")
        header.setFixedHeight(64)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 14, 0)
        h_lay.setSpacing(4)

        self._status_pill = _StatusPill()
        h_lay.addWidget(self._status_pill)
        h_lay.addStretch(1)

        self._btn_refresh = QPushButton()
        self._btn_refresh.setObjectName("GhostIconBtn")
        self._btn_refresh.setCursor(Qt.PointingHandCursor)
        self._btn_refresh.setToolTip("Segarkan lacak resi ini sekarang")
        self._btn_refresh.clicked.connect(self._on_refresh_click)

        self._btn_web = QPushButton()
        self._btn_web.setObjectName("GhostIconBtn")
        self._btn_web.setCursor(Qt.PointingHandCursor)
        self._btn_web.setToolTip("Buka halaman web resmi ekspedisi")
        self._btn_web.clicked.connect(self._on_web_click)

        self._btn_more = QPushButton()
        self._btn_more.setObjectName("GhostIconBtn")
        self._btn_more.setCursor(Qt.PointingHandCursor)
        self._btn_more.setToolTip("Opsi lainnya (ubah, arsip, hapus)")
        self._btn_more.clicked.connect(self._on_more_click)

        self._btn_close_single = self._make_close_btn()

        h_lay.addWidget(self._btn_refresh)
        h_lay.addWidget(self._btn_web)
        h_lay.addWidget(self._btn_more)
        h_lay.addWidget(self._btn_close_single)
        v_page.addWidget(header)

        # Subtle 2px progress bar while refreshing single resi
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(2)
        self._progress.hide()
        v_page.addWidget(self._progress)

        # Scrollable Body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        body = QWidget()
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        b_lay = QVBoxLayout(body)
        b_lay.setSizeConstraint(QLayout.SetMinAndMaxSize)
        b_lay.setContentsMargins(22, 20, 22, 24)
        b_lay.setSpacing(18)

        # 1. Waybill Header Block
        w_block = QVBoxLayout()
        w_block.setSpacing(6)

        w_top = QHBoxLayout()
        w_top.setSpacing(6)
        w_over = QLabel("NOMOR RESI")
        w_over.setObjectName("SectionOverline")
        self._lifecycle_badge = QLabel()
        self._lifecycle_badge.setFixedHeight(21)
        self._lifecycle_badge.setAlignment(Qt.AlignCenter)
        self._courier_tag = QLabel()
        w_top.addWidget(w_over)
        w_top.addStretch(1)
        w_top.addWidget(self._lifecycle_badge)
        w_top.addWidget(self._courier_tag)
        w_block.addLayout(w_top)

        w_num_row = QHBoxLayout()
        w_num_row.setSpacing(8)
        self._lbl_resi = QLabel()
        self._lbl_resi.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl_resi.setCursor(Qt.IBeamCursor)
        self._lbl_resi.setFont(QFont("Consolas", 12, QFont.Bold))
        self._lbl_resi.setWordWrap(True)
        self._btn_copy_resi = QPushButton()
        self._btn_copy_resi.setObjectName("GhostIconBtn")
        self._btn_copy_resi.setCursor(Qt.PointingHandCursor)
        self._btn_copy_resi.setToolTip("Salin nomor resi")
        self._btn_copy_resi.clicked.connect(self._copy_resi)
        w_num_row.addWidget(self._lbl_resi)
        w_num_row.addWidget(self._btn_copy_resi)
        w_num_row.addStretch(1)
        w_block.addLayout(w_num_row)

        self._lbl_checked = QLabel()
        self._lbl_checked.setObjectName("MutedText")
        w_block.addWidget(self._lbl_checked)
        b_lay.addLayout(w_block)

        # 2. Diagnosis Callout Card (contextual)
        self._callout = QFrame()
        self._callout.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        c_lay = QHBoxLayout(self._callout)
        c_lay.setSizeConstraint(QLayout.SetMinimumSize)
        c_lay.setContentsMargins(12, 11, 12, 12)
        c_lay.setSpacing(10)
        self._callout_icon = QLabel()
        self._callout_icon.setFixedSize(18, 18)
        c_text = QVBoxLayout()
        c_text.setSizeConstraint(QLayout.SetMinimumSize)
        c_text.setSpacing(4)
        self._callout_title = QLabel()
        self._callout_title.setWordWrap(True)
        self._callout_desc = QLabel()
        self._callout_desc.setWordWrap(True)
        self._callout_desc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        c_text.addWidget(self._callout_title)
        c_text.addWidget(self._callout_desc)
        c_lay.addWidget(self._callout_icon, 0, Qt.AlignTop)
        c_lay.addLayout(c_text, 1)
        self._callout.hide()
        b_lay.addWidget(self._callout)

        # 3. Buyer & Order Card
        self._order_card = QFrame()
        self._order_card.setObjectName("InfoCard")
        oc_lay = QVBoxLayout(self._order_card)
        oc_lay.setContentsMargins(16, 14, 16, 14)
        oc_lay.setSpacing(12)

        # Buyer top row
        b_row = QHBoxLayout()
        b_row.setSpacing(10)
        self._avatar = QLabel("B")
        self._avatar.setFixedSize(32, 32)
        self._avatar.setAlignment(Qt.AlignCenter)

        b_info = QVBoxLayout()
        b_info.setSpacing(1)
        self._lbl_buyer = QLabel()
        self._lbl_buyer.setWordWrap(True)
        self._lbl_buyer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl_buyer.setCursor(Qt.IBeamCursor)
        self._lbl_buyer.setStyleSheet("font-size: 13.5px; font-weight: 600;")
        self._lbl_buyer_sub = QLabel("Pembeli Shopee")
        self._lbl_buyer_sub.setObjectName("MutedText")
        b_info.addWidget(self._lbl_buyer)
        b_info.addWidget(self._lbl_buyer_sub)

        self._btn_copy_buyer = QPushButton()
        self._btn_copy_buyer.setObjectName("GhostIconBtn")
        self._btn_copy_buyer.setCursor(Qt.PointingHandCursor)
        self._btn_copy_buyer.setToolTip("Salin username pembeli")
        self._btn_copy_buyer.clicked.connect(self._copy_buyer)

        b_row.addWidget(self._avatar)
        b_row.addLayout(b_info, 1)
        b_row.addWidget(self._btn_copy_buyer)
        oc_lay.addLayout(b_row)

        div = QFrame()
        div.setObjectName("HairlineH")
        oc_lay.addWidget(div)

        # Key-value order grid
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(1, 1)

        def kv_row(r_idx: int, label: str, selectable: bool = True):
            k_lbl = QLabel(label)
            k_lbl.setObjectName("MutedText")
            k_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            v_lbl = QLabel("-")
            v_lbl.setWordWrap(True)
            v_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            if selectable:
                v_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                v_lbl.setCursor(Qt.IBeamCursor)
            grid.addWidget(k_lbl, r_idx, 0)
            grid.addWidget(v_lbl, r_idx, 1)
            return k_lbl, v_lbl

        # Row 0: No. Pesanan + inline copy
        k_sn = QLabel("No. Pesanan")
        k_sn.setObjectName("MutedText")
        sn_wrap = QHBoxLayout()
        sn_wrap.setSpacing(6)
        self._lbl_ordersn = QLabel("-")
        self._lbl_ordersn.setWordWrap(True)
        self._lbl_ordersn.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl_ordersn.setCursor(Qt.IBeamCursor)
        self._lbl_ordersn.setFont(QFont("Consolas", 10))
        self._btn_copy_sn = QPushButton()
        self._btn_copy_sn.setObjectName("GhostIconBtn")
        self._btn_copy_sn.setFixedSize(22, 22)
        self._btn_copy_sn.setCursor(Qt.PointingHandCursor)
        self._btn_copy_sn.setToolTip("Salin no. pesanan")
        self._btn_copy_sn.clicked.connect(self._copy_ordersn)
        sn_wrap.addWidget(self._lbl_ordersn)
        sn_wrap.addWidget(self._btn_copy_sn)
        sn_wrap.addStretch(1)
        grid.addWidget(k_sn, 0, 0)
        grid.addLayout(sn_wrap, 0, 1)

        _, self._lbl_shop_status = kv_row(1, "Status Shopee")
        _, self._lbl_recipient = kv_row(2, "Penerima")
        _, self._lbl_city = kv_row(3, "Tujuan")
        _, self._lbl_product = kv_row(4, "Produk")
        self._k_cancel, self._lbl_cancel = kv_row(5, "Alasan Batal")
        self._k_note, self._lbl_note = kv_row(6, "Catatan")

        oc_lay.addLayout(grid)
        b_lay.addWidget(self._order_card)

        # 3b. Payment & Fee Breakdown Card (shown when payment/fee data is available)
        self._payment_card = QFrame()
        self._payment_card.setObjectName("InfoCard")
        pc_lay = QVBoxLayout(self._payment_card)
        pc_lay.setContentsMargins(16, 14, 16, 14)
        pc_lay.setSpacing(8)

        p_hdr = QHBoxLayout()
        p_hdr.setSpacing(8)
        p_over = QLabel("PEMBAYARAN")
        p_over.setObjectName("SectionOverline")
        self._pay_method_badge = QLabel()
        self._lbl_pay_total = QLabel("-")
        self._lbl_pay_total.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl_pay_total.setCursor(Qt.IBeamCursor)
        p_hdr.addWidget(p_over)
        p_hdr.addWidget(self._pay_method_badge)
        p_hdr.addStretch(1)
        p_hdr.addWidget(self._lbl_pay_total)
        pc_lay.addLayout(p_hdr)

        self._lbl_pay_method_full = QLabel()
        self._lbl_pay_method_full.setWordWrap(True)
        self._lbl_pay_method_full.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl_pay_method_full.setCursor(Qt.IBeamCursor)
        self._lbl_pay_method_full.setObjectName("MutedText")
        pc_lay.addWidget(self._lbl_pay_method_full)

        self._pay_div = QFrame()
        self._pay_div.setObjectName("HairlineH")
        pc_lay.addWidget(self._pay_div)

        self._pay_grid_host = QWidget()
        self._pay_grid = QGridLayout(self._pay_grid_host)
        self._pay_grid.setContentsMargins(0, 0, 0, 0)
        self._pay_grid.setHorizontalSpacing(12)
        self._pay_grid.setVerticalSpacing(5)
        self._pay_grid.setColumnStretch(0, 1)
        pc_lay.addWidget(self._pay_grid_host)

        self._lbl_pay_ship_note = QLabel()
        self._lbl_pay_ship_note.setWordWrap(True)
        self._lbl_pay_ship_note.setObjectName("MutedText")
        pc_lay.addWidget(self._lbl_pay_ship_note)

        self._payment_card.hide()
        b_lay.addWidget(self._payment_card)

        # 3c. Related Duplicate Recipient Orders Card (shown when same recipient+phone exists)
        self._dup_card = QFrame()
        self._dup_card.setObjectName("InfoCard")
        dc_lay = QVBoxLayout(self._dup_card)
        dc_lay.setContentsMargins(16, 14, 16, 14)
        dc_lay.setSpacing(8)

        d_hdr = QHBoxLayout()
        d_hdr.setSpacing(8)
        d_over = QLabel("DUPLIKAT PENERIMA")
        d_over.setObjectName("SectionOverline")
        self._dup_count_badge = QLabel()
        d_hdr.addWidget(d_over)
        d_hdr.addStretch(1)
        d_hdr.addWidget(self._dup_count_badge)
        dc_lay.addLayout(d_hdr)

        self._dup_list_host = QWidget()
        self._dup_list_lay = QVBoxLayout(self._dup_list_host)
        self._dup_list_lay.setContentsMargins(0, 0, 0, 0)
        self._dup_list_lay.setSpacing(6)
        dc_lay.addWidget(self._dup_list_host)

        self._dup_card.hide()
        b_lay.addWidget(self._dup_card)

        # 4. Timeline Section
        tl_hdr = QHBoxLayout()
        tl_over = QLabel("RIWAYAT PERJALANAN")
        tl_over.setObjectName("SectionOverline")
        self._lbl_ev_count = QLabel("0")
        self._lbl_ev_count.setObjectName("CountPill")
        tl_hdr.addWidget(tl_over)
        tl_hdr.addStretch(1)
        tl_hdr.addWidget(self._lbl_ev_count)
        b_lay.addLayout(tl_hdr)

        self._timeline = TimelineWidget()
        b_lay.addWidget(self._timeline)
        b_lay.addStretch(1)

        scroll.setWidget(body)
        v_page.addWidget(scroll, 1)
        self._stack.addWidget(page)

    def _build_multi_page(self):
        page = QWidget()
        v_pg = QVBoxLayout(page)
        v_pg.setContentsMargins(0, 0, 0, 0)
        v_pg.setSpacing(0)

        top_bar = QWidget()
        top_bar.setFixedHeight(52)
        tb_lay = QHBoxLayout(top_bar)
        tb_lay.setContentsMargins(16, 0, 14, 0)
        tb_lay.addStretch(1)
        self._btn_close_multi = self._make_close_btn()
        tb_lay.addWidget(self._btn_close_multi)
        v_pg.addWidget(top_bar)

        lay = QVBoxLayout()
        lay.setContentsMargins(28, 12, 28, 48)
        lay.setAlignment(Qt.AlignCenter)

        card = QFrame()
        card.setObjectName("InfoCard")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(20, 20, 20, 20)
        c_lay.setSpacing(12)

        self._multi_title = QLabel("0 pesanan dipilih")
        self._multi_title.setStyleSheet("font-size: 16px; font-weight: 700;")

        self._multi_sub = QLabel()
        self._multi_sub.setWordWrap(True)
        self._multi_sub.setObjectName("MutedText")

        c_lay.addWidget(self._multi_title)
        c_lay.addWidget(self._multi_sub)

        btn_grid = QGridLayout()
        btn_grid.setSpacing(8)

        self._m_track = QPushButton("Lacak Terpilih")
        self._m_track.setObjectName("PrimaryBtn")
        self._m_track.setCursor(Qt.PointingHandCursor)
        self._m_track.clicked.connect(lambda: self.track_many_requested.emit(list(self._current_rows)))

        self._m_copy = QPushButton("Salin Resi")
        self._m_copy.setObjectName("SecondaryBtn")
        self._m_copy.setCursor(Qt.PointingHandCursor)
        self._m_copy.clicked.connect(self._copy_all_resi)

        self._m_arch = QPushButton("Arsipkan")
        self._m_arch.setObjectName("SecondaryBtn")
        self._m_arch.setCursor(Qt.PointingHandCursor)
        self._m_arch.clicked.connect(self._on_multi_archive)

        self._m_del = QPushButton("Hapus")
        self._m_del.setObjectName("DangerBtn")
        self._m_del.setCursor(Qt.PointingHandCursor)
        self._m_del.clicked.connect(lambda: self.delete_requested.emit(list(self._current_rows)))

        btn_grid.addWidget(self._m_track, 0, 0)
        btn_grid.addWidget(self._m_copy, 0, 1)
        btn_grid.addWidget(self._m_arch, 1, 0)
        btn_grid.addWidget(self._m_del, 1, 1)
        c_lay.addSpacing(4)
        c_lay.addLayout(btn_grid)

        lay.addStretch(1)
        lay.addWidget(card)
        lay.addStretch(1)
        v_pg.addLayout(lay, 1)
        self._stack.addWidget(page)

    # ------------------------------------------------------------------ Public API
    def set_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self._refresh_icons()
        self.set_rows(self._current_rows)

    def set_refreshing(self, resi: str) -> None:
        self._refreshing_resi = resi or ""
        is_active = bool(self._current_rows and len(self._current_rows) == 1
                         and self._current_rows[0].get("resi") == self._refreshing_resi)
        self._progress.setVisible(is_active)
        self._btn_refresh.setEnabled(not is_active)
        if self._current_rows and len(self._current_rows) == 1:
            self._update_checked_label(self._current_rows[0])

    def set_rows(self, rows: list[dict], dup_map: dict[str, list[dict]] | None = None) -> None:
        self._current_rows = list(rows or [])
        if dup_map is not None:
            self._dup_map = dup_map
        t = get_tokens(self._is_dark)

        if not self._current_rows:
            self._stack.setCurrentIndex(0)
            return

        if len(self._current_rows) > 1:
            c_counts = Counter((r.get("courier") or "Lainnya") for r in self._current_rows)
            s_counts = Counter(effective_status(r) for r in self._current_rows)
            c_str = " · ".join(f"{k}: {v}" for k, v in c_counts.items())
            s_str = " · ".join(f"{k}: {v}" for k, v in s_counts.items())
            self._multi_title.setText(f"{len(self._current_rows)} pesanan dipilih")
            lines = [f"Kurir: {c_str}", f"Status: {s_str}"]
            p_counts = Counter(
                payment_short_label(r.get("payment_method") or "")
                for r in self._current_rows
                if (r.get("payment_method") or "").strip()
            )
            sum_tp = sum(int(r.get("total_payment") or 0) for r in self._current_rows)
            if p_counts or sum_tp > 0:
                p_parts = [f"{k}: {v}" for k, v in p_counts.items()]
                if sum_tp > 0:
                    p_parts.append(f"Total {format_idr(sum_tp)}")
                lines.append("Pembayaran: " + " · ".join(p_parts))
            self._multi_sub.setText("\n".join(lines))
            all_archived = all(bool(r.get("archived")) for r in self._current_rows)
            self._multi_archive_target = not all_archived
            self._m_arch.setText("Keluarkan Arsip" if all_archived else "Arsipkan")
            self._stack.setCurrentIndex(2)
            return

        r = self._current_rows[0]
        self._stack.setCurrentIndex(1)

        status = effective_status(r)
        lifecycle = row_lifecycle(r)
        self._status_pill.set_status(status, self._is_dark)
        if lifecycle == CAT_FINISHED:
            lc_bg = "#0E2D1C" if self._is_dark else "#F0FDF4"
            lc_fg = "#86EFAC" if self._is_dark else "#15803D"
            self._lifecycle_badge.setText("Selesai")
        elif lifecycle == CAT_OTHER:
            lc_bg = "#3A1C0B" if self._is_dark else "#FFF7ED"
            lc_fg = "#FDBA74" if self._is_dark else "#C2410C"
            self._lifecycle_badge.setText("Lainnya")
        else:
            lc_bg = "#172554" if self._is_dark else "#EFF6FF"
            lc_fg = "#93C5FD" if self._is_dark else "#1D4ED8"
            self._lifecycle_badge.setText("Berjalan")
        self._lifecycle_badge.setStyleSheet(
            f"background-color: {lc_bg}; color: {lc_fg}; border-radius: 7px; "
            "padding: 2px 8px; font-size: 10.5px; font-weight: 600;"
        )

        courier = (r.get("courier") or "").upper()
        c_bg, c_fg = courier_style(courier, self._is_dark)
        self._courier_tag.setText(courier or "KURIR ?")
        self._courier_tag.setStyleSheet(
            f"background-color: {c_bg}; color: {c_fg}; border-radius: 6px; "
            "padding: 3px 9px; font-size: 11px; font-weight: 700;"
        )

        self._lbl_resi.setText(r.get("resi") or "-")
        self._update_checked_label(r)

        # Active progress bar check
        is_busy_this = (r.get("resi") == self._refreshing_resi and bool(self._refreshing_resi))
        self._progress.setVisible(is_busy_this)
        self._btn_refresh.setEnabled(not is_busy_this)

        # Minimal error banner (shown only on tracking error)
        self._render_callout(r.get("error") or "")

        # Buyer Card
        buyer = r.get("buyer_username") or "Tanpa Username"
        initial = buyer[0].upper() if buyer and buyer[0].isalnum() else "B"
        self._avatar.setText(initial)
        self._avatar.setStyleSheet(
            f"background-color: {t['accent_soft']}; color: {t['accent']}; "
            "border-radius: 16px; font-size: 13px; font-weight: 700;"
        )
        self._lbl_buyer.setText(buyer)
        self._btn_copy_buyer.setVisible(bool(r.get("buyer_username")))

        self._lbl_ordersn.setText(r.get("order_sn") or "-")
        self._btn_copy_sn.setVisible(bool(r.get("order_sn")))
        self._lbl_shop_status.setText(r.get("order_status") or "-")
        rec_name = r.get("recipient_name") or "-"
        phone = r.get("recipient_phone") or ""
        self._lbl_recipient.setText(f"{rec_name} ({phone})" if phone else rec_name)
        self._lbl_city.setText(r.get("city") or "-")
        self._lbl_product.setText(r.get("product") or "-")

        c_by = (r.get("cancel_by") or "").strip()
        c_reason = (r.get("cancel_reason") or "").strip()
        actor_map = {"buyer": "Pembeli", "system": "Sistem", "seller": "Penjual"}
        c_by_disp = actor_map.get(c_by.lower(), c_by)
        if c_reason and c_by_disp and "dibatalkan" not in c_reason.lower():
            cancel_txt = f"{c_reason} ({c_by_disp})"
        else:
            cancel_txt = c_reason or c_by_disp
        self._k_cancel.setVisible(bool(cancel_txt))
        self._lbl_cancel.setVisible(bool(cancel_txt))
        self._lbl_cancel.setText(cancel_txt or "-")

        note = (r.get("note") or "").strip()
        show_note = bool(note and not note.startswith("pkg:"))
        self._k_note.setVisible(show_note)
        self._lbl_note.setVisible(show_note)
        self._lbl_note.setText(note)

        # Payment & Fee Breakdown Card
        self._render_payment_card(r)

        # Related Duplicate Orders Card
        self._render_dup_card(r)

        # Timeline
        try:
            events = json.loads(r.get("events_json") or "[]")
        except Exception:
            events = []
        self._lbl_ev_count.setText(str(len(events)))
        self._timeline.set_events(events, self._is_dark, is_placeholder=str(r.get("resi") or "").startswith("ORDER-"))

    def _clear_pay_grid(self) -> None:
        while self._pay_grid.count():
            item = self._pay_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _render_payment_card(self, r: dict) -> None:
        t = get_tokens(self._is_dark)
        pm = (r.get("payment_method") or "").strip()
        try:
            tp = int(r.get("total_payment") or 0)
        except (TypeError, ValueError):
            tp = 0
        details = payment_details_dict(r)
        if not pm and tp <= 0 and not any(v > 0 for v in details.values()):
            self._clear_pay_grid()
            self._payment_card.hide()
            return

        self._payment_card.show()
        short_pm = payment_short_label(pm)
        if short_pm:
            p_bg, p_fg = payment_style(pm, self._is_dark)
            self._pay_method_badge.setText(short_pm)
            self._pay_method_badge.setStyleSheet(
                f"background-color: {p_bg}; color: {p_fg}; border-radius: 5px; "
                "padding: 2px 7px; font-size: 10.5px; font-weight: 700;"
            )
            self._pay_method_badge.show()
        else:
            self._pay_method_badge.hide()

        if tp > 0:
            self._lbl_pay_total.setText(format_idr(tp))
            self._lbl_pay_total.setStyleSheet(f"color: {t['text_primary']}; font-size: 14.5px; font-weight: 700;")
        else:
            self._lbl_pay_total.setText("-")
            self._lbl_pay_total.setStyleSheet(f"color: {t['text_muted']}; font-size: 14px; font-weight: 600;")

        show_full_pm = bool(pm and pm != short_pm)
        self._lbl_pay_method_full.setVisible(show_full_pm)
        if show_full_pm:
            self._lbl_pay_method_full.setText(pm)

        sub = details.get("product_subtotal", 0)
        bship = details.get("buyer_shipping", 0)
        s_est = details.get("shipping_est", 0)
        s_disc = details.get("shipping_discount", 0)
        sv = details.get("seller_voucher", 0)
        shv = details.get("shopee_voucher", 0)
        coin = details.get("coins_discount", 0)
        cc = details.get("cc_discount", 0)
        svc = details.get("service_fee", 0)
        ret_s = details.get("return_shipping", 0)

        fee_rows: list[tuple[str, str, str]] = []
        if sub > 0:
            fee_rows.append(("Subtotal Produk", format_idr(sub), "normal"))
        if bship > 0:
            fee_rows.append(("Ongkir Pembeli", format_idr(bship), "normal"))
        elif s_est > 0 and tp > 0:
            fee_rows.append(("Ongkir Pembeli", "Gratis", "discount"))
        if sv > 0:
            fee_rows.append(("Voucher Penjual", f"-{format_idr(sv)}", "discount"))
        if shv > 0:
            fee_rows.append(("Voucher Shopee", f"-{format_idr(shv)}", "discount"))
        if coin > 0:
            fee_rows.append(("Potongan Koin", f"-{format_idr(coin)}", "discount"))
        if cc > 0:
            fee_rows.append(("Diskon Kartu Kredit", f"-{format_idr(cc)}", "discount"))
        if svc > 0:
            fee_rows.append(("Biaya Layanan", format_idr(svc), "normal"))
        if ret_s > 0:
            fee_rows.append(("Ongkir Retur", format_idr(ret_s), "warn"))
        if not fee_rows and s_est > 0:
            fee_rows.append(("Perkiraan Ongkir", format_idr(s_est), "normal"))

        self._clear_pay_grid()
        has_rows = bool(fee_rows)
        self._pay_div.setVisible(has_rows)
        self._pay_grid_host.setVisible(has_rows)

        disc_col = "#86EFAC" if self._is_dark else "#15803D"
        warn_col = "#FCD34D" if self._is_dark else "#B45309"
        for idx, (lbl_txt, val_txt, tone) in enumerate(fee_rows):
            k_lbl = QLabel(lbl_txt)
            k_lbl.setObjectName("MutedText")
            k_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 12px;")
            v_lbl = QLabel(val_txt)
            v_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            v_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v_lbl.setCursor(Qt.IBeamCursor)
            v_col = disc_col if tone == "discount" else (warn_col if tone == "warn" else t["text_primary"])
            v_lbl.setStyleSheet(f"color: {v_col}; font-size: 12px; font-weight: 600;")
            self._pay_grid.addWidget(k_lbl, idx, 0)
            self._pay_grid.addWidget(v_lbl, idx, 1)

        if s_est > 0 and s_disc > 0:
            self._lbl_pay_ship_note.setText(f"Ongkir kurir {format_idr(s_est)} · Subsidi ongkir {format_idr(s_disc)}")
            self._lbl_pay_ship_note.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px;")
            self._lbl_pay_ship_note.show()
        else:
            self._lbl_pay_ship_note.hide()

    def _clear_dup_list(self) -> None:
        while self._dup_list_lay.count():
            item = self._dup_list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _render_dup_card(self, r: dict) -> None:
        t = get_tokens(self._is_dark)
        cur_resi = r.get("resi") or ""
        peers = self._dup_map.get(cur_resi) or []
        other_peers = [p for p in peers if (p.get("resi") or "") != cur_resi]
        self._clear_dup_list()
        if len(peers) < 2 or not other_peers:
            self._dup_card.hide()
            return

        self._dup_card.show()
        d_bg, d_fg = duplicate_style(self._is_dark)
        self._dup_count_badge.setText(f"{len(peers)} pesanan")
        self._dup_count_badge.setStyleSheet(
            f"background-color: {d_bg}; color: {d_fg}; border-radius: 6px; "
            "padding: 2px 8px; font-size: 10.5px; font-weight: 700;"
        )

        for p in other_peers:
            p_resi = p.get("resi") or "-"
            buyer = (p.get("buyer_username") or "").strip() or "Tanpa Username"
            p_st = effective_status(p)
            _bg, _fg, _dot, short_st = status_style(p_st, self._is_dark)
            lc = row_lifecycle(p)
            lc_lbl = "Selesai" if lc == CAT_FINISHED else ("Lainnya" if lc == CAT_OTHER else "Berjalan")
            btn = QPushButton(f"{buyer} · {p_resi}\n{short_st} · {lc_lbl}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"Klik untuk melihat pesanan {p_resi}")
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {t['bg_surface']}; color: {t['text_primary']}; "
                f"border: 1px solid {t['border_hairline']}; border-radius: 8px; "
                "padding: 7px 10px; text-align: left; font-size: 11.5px; font-weight: 500; }} "
                f"QPushButton:hover {{ border-color: {d_fg}; background-color: {t['bg_hover']}; }}"
            )
            btn.clicked.connect(lambda _c=False, target_resi=p_resi: self.select_resi_requested.emit(target_resi))
            self._dup_list_lay.addWidget(btn)

    # ------------------------------------------------------------------ Helpers
    def _refresh_icons(self) -> None:
        t = get_tokens(self._is_dark)
        self._empty_icon.setPixmap(svg_pixmap("package", t["text_muted"], 24))
        self._btn_refresh.setIcon(svg_icon("refresh", t["text_secondary"], 15))
        self._btn_web.setIcon(svg_icon("external", t["text_secondary"], 15))
        self._btn_more.setIcon(svg_icon("more", t["text_secondary"], 15))
        for btn_c in (self._btn_close_empty, self._btn_close_single, self._btn_close_multi):
            btn_c.setIcon(svg_icon("x", t["text_secondary"], 15))
        self._btn_copy_resi.setIcon(svg_icon("copy", t["text_secondary"], 14))
        self._btn_copy_buyer.setIcon(svg_icon("copy", t["text_secondary"], 14))
        self._btn_copy_sn.setIcon(svg_icon("copy", t["text_muted"], 12))
        self._m_track.setIcon(svg_icon("refresh", t["accent_text"], 14))
        self._m_copy.setIcon(svg_icon("copy", t["text_primary"], 14))
        self._m_arch.setIcon(svg_icon("archive", t["text_primary"], 14))
        self._m_del.setIcon(svg_icon("trash", t["danger_fg"], 14))

    def _update_checked_label(self, r: dict) -> None:
        t = get_tokens(self._is_dark)
        if r.get("resi") == self._refreshing_resi and self._refreshing_resi:
            self._lbl_checked.setText("Sedang mengecek ke server kurir...")
            self._lbl_checked.setStyleSheet(f"color: {t['accent']}; font-size: 12px; font-weight: 500;")
            return
        lc = r.get("last_checked") or ""
        src = r.get("tracking_source") or ""
        if lc:
            txt = f"Diperbarui {lc}" + (f" · {src}" if src else "")
        else:
            txt = "Belum pernah dicek ke kurir"
        self._lbl_checked.setText(txt)
        self._lbl_checked.setStyleSheet(f"color: {t['text_muted']}; font-size: 12px;")

    def _render_callout(self, error_msg: str) -> None:
        if not error_msg:
            self._callout.hide()
            return

        bg, fg, _dot, _short = status_style(ST_ERROR, self._is_dark)
        self._callout.setStyleSheet(f"QFrame {{ background-color: {bg}; border-radius: 10px; border: none; }}")
        self._callout_icon.setPixmap(svg_pixmap("alert", fg, 16))
        self._callout_title.setText("Kendala Pelacakan")
        self._callout_title.setStyleSheet(f"color: {fg}; font-size: 12.5px; font-weight: 700; background: transparent;")
        self._callout_desc.setText(error_msg)
        self._callout_desc.setStyleSheet(f"color: {fg}; font-size: 11.5px; background: transparent;")
        self._callout.show()

    def _flash_copy(self, btn: QPushButton, text: str, label: str, size: int = 14) -> None:
        if not text or text == "-":
            return
        QGuiApplication.clipboard().setText(text)
        self.copy_toast.emit(f"{label} disalin ke clipboard")
        btn.setIcon(svg_icon("check", "#16A34A", size))

        def _restore_icon(b=btn, sz=size):
            try:
                cur_t = get_tokens(self._is_dark)
                col = cur_t["text_muted"] if sz <= 12 else cur_t["text_secondary"]
                b.setIcon(svg_icon("copy", col, sz))
            except RuntimeError:
                pass

        QTimer.singleShot(1200, _restore_icon)

    # ------------------------------------------------------------------ Slots
    def _on_multi_archive(self):
        if self._current_rows:
            self.archive_requested.emit(list(self._current_rows), self._multi_archive_target)
    def _on_refresh_click(self):
        if self._current_rows:
            self.refresh_requested.emit(self._current_rows[0]["resi"])

    def _on_web_click(self):
        if self._current_rows:
            self.open_web_requested.emit(self._current_rows[0])

    def _on_more_click(self):
        if self._current_rows:
            pos = self._btn_more.mapToGlobal(QPoint(0, self._btn_more.height() + 4))
            self.action_menu_requested.emit(pos, self._current_rows[0])

    def _copy_resi(self):
        if self._current_rows:
            self._flash_copy(self._btn_copy_resi, self._current_rows[0].get("resi", ""), "Nomor resi")

    def _copy_buyer(self):
        if self._current_rows:
            self._flash_copy(self._btn_copy_buyer, self._current_rows[0].get("buyer_username", ""), "Username buyer")

    def _copy_ordersn(self):
        if self._current_rows:
            self._flash_copy(self._btn_copy_sn, self._current_rows[0].get("order_sn", ""), "No. pesanan", size=12)

    def _copy_all_resi(self):
        if self._current_rows:
            text = "\n".join(r["resi"] for r in self._current_rows if r.get("resi"))
            QGuiApplication.clipboard().setText(text)
            self.copy_toast.emit(f"{len(self._current_rows)} nomor resi disalin")
