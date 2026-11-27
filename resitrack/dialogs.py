"""Clean minimalist dialogs: Add Waybills & Settings."""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QIcon, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import assets_dir
from .couriers import detect_courier, normalize_resi
from .icons import svg_icon
from .models import COURIERS
from .theme import apply_windows_titlebar_theme, get_tokens

ICON_PATH = assets_dir() / "icon.png"


def _parent_is_dark(parent) -> bool:
    return getattr(parent, "current_theme", "light") == "dark"


def _style_form(form: QFormLayout) -> None:
    """Apply consistent DKV spacing & full-width growth to every QFormLayout."""
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignTop)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(12)


class AddDialog(QDialog):
    """Input resi baru (satuan atau banyak baris sekaligus)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tambah Nomor Resi")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(540, 460)
        self.setMinimumSize(460, 400)
        is_dark = _parent_is_dark(parent)
        apply_windows_titlebar_theme(self, is_dark)
        t = get_tokens(is_dark)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)

        info = QLabel(
            "Masukkan 1 resi per baris. Bisa langsung sertakan username &amp; no. pesanan "
            "dengan format: <code>resi, username, no_pesanan</code><br>"
            f"<span style='color:{t['text_muted']}; font-size:11.5px;'>"
            "Contoh: <code>SPXID041234567890, toko_budi, 251120ABCDEF12</code></span>"
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        self.text = QPlainTextEdit()
        self.text.setCursor(Qt.IBeamCursor)
        self.text.setPlaceholderText(
            "SPXID041234567890\n"
            "CM12345678901234, buyer_sari\n"
            "10001234567890, pembeli_setia, 251120XXXX"
        )
        lay.addWidget(self.text, 1)

        form = QFormLayout()
        _style_form(form)
        self.courier = QComboBox()
        self.courier.setCursor(Qt.PointingHandCursor)
        self.courier.setMinimumHeight(36)
        self.courier.addItems(["(Deteksi Otomatis)"] + COURIERS)
        form.addRow("Ekspedisi:", self.courier)

        self.username = QLineEdit()
        self.username.setCursor(Qt.IBeamCursor)
        self.username.setMinimumHeight(36)
        self.username.setPlaceholderText("Dipakai jika baris di atas tidak berisi username")
        form.addRow("Username Buyer:", self.username)
        lay.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = bb.button(QDialogButtonBox.Ok)
        cancel_btn = bb.button(QDialogButtonBox.Cancel)
        ok_btn.setText("Simpan Resi")
        cancel_btn.setText("Batal")
        ok_btn.setObjectName("PrimaryBtn")
        cancel_btn.setObjectName("SecondaryBtn")
        ok_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _on_accept(self):
        if not self.records():
            QMessageBox.warning(self, "Input Kosong", "Masukkan minimal satu nomor resi yang valid.")
            return
        self.accept()

    def records(self) -> list[dict]:
        forced = self.courier.currentText() if self.courier.currentIndex() > 0 else ""
        out = []
        for line in self.text.toPlainText().splitlines():
            parts = [p.strip() for p in line.replace(";", ",").replace("\t", ",").split(",")]
            if not parts or not parts[0]:
                continue
            resi = normalize_resi(parts[0])
            if not resi:
                continue
            out.append({
                "resi": resi,
                "courier": forced or detect_courier(resi),
                "buyer_username": (parts[1] if len(parts) > 1 else "") or self.username.text().strip(),
                "order_sn": parts[2] if len(parts) > 2 else "",
                "order_source": "manual",
            })
        return out


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, shopee_client_factory, parent=None):
        super().__init__(parent)
        self.s = dict(settings)
        self.factory = shopee_client_factory
        self._is_dark = _parent_is_dark(parent)
        self.setWindowTitle("Pengaturan ResiTrack")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(680, 680)
        self.setMinimumSize(620, 580)

        apply_windows_titlebar_theme(self, self._is_dark)
        t = get_tokens(self._is_dark)

        tabs = QTabWidget()
        tabs.tabBar().setCursor(Qt.PointingHandCursor)

        # ---- Tab 1: Tampilan & Umum
        w_gen = QWidget()
        f_gen = QFormLayout(w_gen)
        f_gen.setContentsMargins(18, 20, 18, 18)
        _style_form(f_gen)

        self.theme_combo = QComboBox()
        self.theme_combo.setCursor(Qt.PointingHandCursor)
        self.theme_combo.setMinimumHeight(36)
        self.theme_combo.addItem("Tema Terang (Light Mode)", "light")
        self.theme_combo.addItem("Tema Gelap (Dark Mode)", "dark")
        current_theme = self.s.get("theme", "light")
        self.theme_combo.setCurrentIndex(1 if current_theme == "dark" else 0)
        f_gen.addRow("Pilihan Tema:", self.theme_combo)

        self.auto = QSpinBox()
        self.auto.setCursor(Qt.IBeamCursor)
        self.auto.setMinimumHeight(36)
        self.auto.setRange(0, 1440)
        self.auto.setSuffix(" menit (0 = mati)")
        self.auto.setValue(int(self.s.get("auto_refresh_minutes", 60)))
        auto_wrap = QVBoxLayout()
        auto_wrap.setSpacing(4)
        auto_wrap.addWidget(self.auto)
        auto_hint = QLabel("Interval sinkronisasi otomatis status kurir di latar belakang.")
        auto_hint.setObjectName("MutedText")
        auto_wrap.addWidget(auto_hint)
        f_gen.addRow("Auto lacak ulang:", auto_wrap)

        self.delay = QDoubleSpinBox()
        self.delay.setCursor(Qt.IBeamCursor)
        self.delay.setMinimumHeight(36)
        self.delay.setRange(0, 10)
        self.delay.setSingleStep(0.5)
        self.delay.setSuffix(" detik")
        self.delay.setValue(float(self.s.get("request_delay_seconds", 1.0)))
        delay_wrap = QVBoxLayout()
        delay_wrap.setSpacing(4)
        delay_wrap.addWidget(self.delay)
        delay_hint = QLabel("Jeda sopan antar permintaan kurir saat melacak banyak paket.")
        delay_hint.setObjectName("MutedText")
        delay_wrap.addWidget(delay_hint)
        f_gen.addRow("Jeda antar cek:", delay_wrap)

        tabs.addTab(w_gen, svg_icon("sliders", t["text_secondary"], 15), "Tampilan && Umum")

        # ---- Tab 2: Ekspedisi / Gateway
        w_cour = QWidget()
        v_cour = QVBoxLayout(w_cour)
        v_cour.setContentsMargins(18, 20, 18, 18)
        v_cour.setSpacing(16)

        cour_card = QFrame()
        cour_card.setObjectName("InfoCard")
        cc_lay = QVBoxLayout(cour_card)
        cc_lay.setContentsMargins(14, 12, 14, 12)
        info_cour = QLabel(
            "<b>Status Koneksi Ekspedisi:</b><br>"
            "• <b>SPX &amp; Anteraja:</b> Terhubung langsung ke endpoint publik kurir (gratis &amp; instan).<br>"
            "• <b>JNE &amp; POS:</b> Menggunakan gateway komunitas ZPI secara otomatis.<br>"
            "• <b>BinderByte (Opsional):</b> Isi API key pribadi sebagai cadangan untuk JNE/POS."
        )
        info_cour.setWordWrap(True)
        cc_lay.addWidget(info_cour)
        v_cour.addWidget(cour_card)

        f_cour = QFormLayout()
        _style_form(f_cour)

        self.bb_key = QLineEdit(self.s.get("binderbyte_api_key", ""))
        self.bb_key.setCursor(Qt.IBeamCursor)
        self.bb_key.setMinimumHeight(36)
        self.bb_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.bb_key.setPlaceholderText("Masukkan API key BinderByte (opsional)")
        f_cour.addRow("API Key BinderByte:", self.bb_key)

        self.bb_fb = QCheckBox("Gunakan BinderByte sebagai cadangan saat endpoint utama gagal")
        self.bb_fb.setCursor(Qt.PointingHandCursor)
        self.bb_fb.setChecked(bool(self.s.get("use_binderbyte_fallback", True)))
        f_cour.addRow("", self.bb_fb)

        v_cour.addLayout(f_cour)
        v_cour.addStretch(1)
        tabs.addTab(w_cour, svg_icon("truck", t["text_secondary"], 15), "Ekspedisi")

        # ---- Tab 3: Shopee Open Platform (Scroll-protected, crisp DKV cards)
        shp_scroll = QScrollArea()
        shp_scroll.setWidgetResizable(True)
        shp_scroll.setFrameShape(QFrame.NoFrame)
        shp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        w_shp = QWidget()
        v_shp = QVBoxLayout(w_shp)
        v_shp.setSizeConstraint(QLayout.SetMinimumSize)
        v_shp.setContentsMargins(18, 18, 18, 18)
        v_shp.setSpacing(14)

        info_shp = QLabel(
            "Sambungkan dengan <b>Shopee Open Platform</b> agar pesanan dan <b>username buyer</b> "
            "ditarik otomatis.<br>"
            f"Daftar di <a href='https://open.shopee.com' style='color:{t['accent']}; text-decoration:none;'>"
            "open.shopee.com</a> (Seller In-House System), lalu isi kredensial aplikasi di bawah:"
        )
        info_shp.setWordWrap(True)
        info_shp.setOpenExternalLinks(True)
        v_shp.addWidget(info_shp)

        lbl_cred = QLabel("KREDENSIAL APLIKASI")
        lbl_cred.setObjectName("SectionOverline")
        v_shp.addWidget(lbl_cred)

        f_shp = QFormLayout()
        _style_form(f_shp)

        self.pid = QLineEdit(str(self.s.get("shopee_partner_id", "")))
        self.pid.setCursor(Qt.IBeamCursor)
        self.pid.setMinimumHeight(36)
        self.pid.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,18}"), self.pid))
        self.pid.setPlaceholderText("Contoh: 1234567")

        self.pkey = QLineEdit(self.s.get("shopee_partner_key", ""))
        self.pkey.setCursor(Qt.IBeamCursor)
        self.pkey.setMinimumHeight(36)
        self.pkey.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.pkey.setPlaceholderText("Kunci rahasia dari Shopee Console")

        self.redirect = QLineEdit(self.s.get("shopee_redirect_url", "https://www.google.com"))
        self.redirect.setCursor(Qt.IBeamCursor)
        self.redirect.setMinimumHeight(36)

        self.sandbox = QCheckBox("Gunakan server pengujian (Sandbox / test-stable)")
        self.sandbox.setCursor(Qt.PointingHandCursor)
        self.sandbox.setChecked(bool(self.s.get("shopee_sandbox", False)))

        self.use_trk = QCheckBox("Prioritaskan lacak via Shopee API jika pesanan memiliki No. Pesanan")
        self.use_trk.setCursor(Qt.PointingHandCursor)
        self.use_trk.setChecked(bool(self.s.get("shopee_use_tracking_api", True)))

        f_shp.addRow("Partner ID:", self.pid)
        f_shp.addRow("Partner Key:", self.pkey)
        f_shp.addRow("Redirect URL:", self.redirect)
        f_shp.addRow("", self.sandbox)
        f_shp.addRow("", self.use_trk)
        v_shp.addLayout(f_shp)

        auth_card = QFrame()
        auth_card.setObjectName("InfoCard")
        ac_lay = QVBoxLayout(auth_card)
        ac_lay.setContentsMargins(16, 14, 16, 14)
        ac_lay.setSpacing(10)

        auth_hdr = QHBoxLayout()
        auth_hdr.setSpacing(8)
        lbl_auth = QLabel("OTORISASI TOKO")
        lbl_auth.setObjectName("SectionOverline")
        self.shopee_state = QLabel()
        self.btn_disconnect = QPushButton("Putuskan")
        self.btn_disconnect.setObjectName("DangerBtn")
        self.btn_disconnect.setCursor(Qt.PointingHandCursor)
        self.btn_disconnect.setMinimumHeight(28)
        self.btn_disconnect.clicked.connect(self.disconnect_shop)

        auth_hdr.addWidget(lbl_auth)
        auth_hdr.addWidget(self.shopee_state)
        auth_hdr.addStretch(1)
        auth_hdr.addWidget(self.btn_disconnect)
        ac_lay.addLayout(auth_hdr)

        b_auth = QPushButton("1. Buka Link Otorisasi Toko")
        b_auth.setObjectName("SecondaryBtn")
        b_auth.setCursor(Qt.PointingHandCursor)
        b_auth.setMinimumHeight(36)
        b_auth.clicked.connect(self.open_auth)
        ac_lay.addWidget(b_auth)

        self.redirect_paste = QLineEdit()
        self.redirect_paste.setCursor(Qt.IBeamCursor)
        self.redirect_paste.setMinimumHeight(36)
        self.redirect_paste.setPlaceholderText("2. Tempel URL hasil redirect browser (?code=...&shop_id=...)")
        ac_lay.addWidget(self.redirect_paste)

        b_conn = QPushButton("3. Hubungkan Toko Sekarang")
        b_conn.setObjectName("PrimaryBtn")
        b_conn.setCursor(Qt.PointingHandCursor)
        b_conn.setMinimumHeight(36)
        b_conn.clicked.connect(self.connect_shop)
        ac_lay.addWidget(b_conn)

        v_shp.addWidget(auth_card)
        v_shp.addStretch(1)

        self._update_state()
        shp_scroll.setWidget(w_shp)
        tabs.addTab(shp_scroll, svg_icon("shopping-bag", t["text_secondary"], 15), "Shopee API")

        # Bottom Buttons
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)
        lay.addWidget(tabs, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save_btn = bb.button(QDialogButtonBox.Save)
        cancel_btn = bb.button(QDialogButtonBox.Cancel)
        save_btn.setText("Simpan Pengaturan")
        cancel_btn.setText("Tutup")
        save_btn.setObjectName("PrimaryBtn")
        cancel_btn.setObjectName("SecondaryBtn")
        save_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _collect(self):
        self.s["theme"] = self.theme_combo.currentData()
        self.s["auto_refresh_minutes"] = self.auto.value()
        self.s["request_delay_seconds"] = self.delay.value()
        self.s["binderbyte_api_key"] = self.bb_key.text().strip()
        self.s["use_binderbyte_fallback"] = self.bb_fb.isChecked()

        new_pid = self.pid.text().strip()
        new_pkey = self.pkey.text().strip()
        new_sandbox = self.sandbox.isChecked()
        cred_changed = (
            new_pid != str(self.s.get("shopee_partner_id", ""))
            or new_pkey != str(self.s.get("shopee_partner_key", ""))
            or new_sandbox != bool(self.s.get("shopee_sandbox", False))
        )
        if cred_changed:
            self.s["shopee_access_token"] = ""
            self.s["shopee_refresh_token"] = ""
            self.s["shopee_shop_id"] = ""
            self.s["shopee_token_expire_at"] = 0

        self.s["shopee_partner_id"] = new_pid
        self.s["shopee_partner_key"] = new_pkey
        self.s["shopee_redirect_url"] = self.redirect.text().strip() or "https://www.google.com"
        self.s["shopee_sandbox"] = new_sandbox
        self.s["shopee_use_tracking_api"] = self.use_trk.isChecked()

    def _update_state(self):
        t = get_tokens(self._is_dark)
        connected = bool(self.s.get("shopee_refresh_token") and self.s.get("shopee_shop_id"))
        if connected:
            bg = "#0E2D1C" if self._is_dark else "#F0FDF4"
            fg = "#86EFAC" if self._is_dark else "#15803D"
            self.shopee_state.setText(f"Terhubung · shop_id {self.s['shopee_shop_id']}")
            self.shopee_state.setStyleSheet(
                f"background-color: {bg}; color: {fg}; border-radius: 8px; "
                "padding: 3px 9px; font-size: 11px; font-weight: 600;"
            )
            self.btn_disconnect.show()
        else:
            self.shopee_state.setText("Belum tersambung")
            self.shopee_state.setStyleSheet(
                f"background-color: {t['bg_surface']}; color: {t['text_muted']}; "
                f"border: 1px solid {t['border_hairline']}; border-radius: 8px; "
                "padding: 3px 9px; font-size: 11px; font-weight: 600;"
            )
            self.btn_disconnect.hide()

    def open_auth(self):
        self._collect()
        c = self.factory(self.s)
        if c is None or not c.configured():
            QMessageBox.warning(self, "Shopee API", "Isi Partner ID dan Partner Key dulu ya sebelum buka link otorisasi.")
            return
        try:
            url = c.auth_url()
        except Exception as e:
            QMessageBox.warning(self, "Shopee API", f"Kredensial tidak valid: {e}")
            return
        webbrowser.open(url)

    def connect_shop(self):
        self._collect()
        c = self.factory(self.s)
        if c is None or not c.configured():
            QMessageBox.warning(self, "Shopee API", "Isi Partner ID dan Partner Key terlebih dahulu.")
            return
        try:
            code, shop_id = c.parse_redirect(self.redirect_paste.text())
            c.exchange_code(code, shop_id)
            self.s.update(c.s)
            self.redirect_paste.clear()
            QMessageBox.information(
                self,
                "Shopee Berhasil Terhubung",
                f"Mantap! Toko dengan shop_id {shop_id} sudah berhasil tersambung.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Koneksi Gagal", f"Gagal menghubungkan toko: {e}")
        self._update_state()

    def disconnect_shop(self):
        if QMessageBox.question(
            self,
            "Putuskan Koneksi Shopee",
            "Putuskan otorisasi toko Shopee dari aplikasi ini?\nAnda bisa menghubungkannya kembali kapan saja.",
        ) != QMessageBox.Yes:
            return
        self.s["shopee_access_token"] = ""
        self.s["shopee_refresh_token"] = ""
        self.s["shopee_shop_id"] = ""
        self.s["shopee_token_expire_at"] = 0
        self._update_state()

    def _save(self):
        self._collect()
        self.accept()

    def result_settings(self) -> dict:
        return self.s
