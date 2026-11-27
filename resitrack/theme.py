"""Unified DKV design system: color tokens, status taxonomy, QPalette & global QSS."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QWidget

from .config import assets_dir
from .models import (
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
)

LIGHT_TOKENS = {
    "bg_canvas": "#F7F8FA",
    "bg_surface": "#FFFFFF",
    "bg_subtle": "#F2F4F7",
    "bg_hover": "#F5F7FA",
    "bg_selected": "#FFF5F2",
    "border_hairline": "#E8EAEE",
    "border_control": "#DCDFE5",
    "border_hover": "#C5C9D2",
    "text_primary": "#111318",
    "text_secondary": "#5E6574",
    "text_muted": "#8C93A1",
    "accent": "#E84C2B",
    "accent_hover": "#D44020",
    "accent_soft": "#FEF2EF",
    "accent_text": "#FFFFFF",
    "danger": "#DC2626",
    "danger_soft": "#FEF2F2",
    "danger_fg": "#B91C1C",
    "scroll_thumb": "#D0D5DD",
    "scroll_thumb_hover": "#98A2B3",
    "tooltip_bg": "#111318",
    "tooltip_fg": "#FFFFFF",
}

DARK_TOKENS = {
    "bg_canvas": "#0D0F13",
    "bg_surface": "#14171E",
    "bg_subtle": "#1C2029",
    "bg_hover": "#191D26",
    "bg_selected": "#241918",
    "border_hairline": "#222630",
    "border_control": "#2D3240",
    "border_hover": "#3E4556",
    "text_primary": "#F1F3F6",
    "text_secondary": "#9AA1B0",
    "text_muted": "#636B7A",
    "accent": "#F05A39",
    "accent_hover": "#FF6D4D",
    "accent_soft": "#2A1815",
    "accent_text": "#FFFFFF",
    "danger": "#EF4444",
    "danger_soft": "#2B1517",
    "danger_fg": "#FCA5A5",
    "scroll_thumb": "#2E3442",
    "scroll_thumb_hover": "#434C5E",
    "tooltip_bg": "#F1F3F6",
    "tooltip_fg": "#111318",
}

# Semantic status colors (resttrained soft pills + crisp 6px indicator dot)
STATUS_META: dict[str, dict[str, str]] = {
    ST_PENDING: {
        "short": "Menunggu Pickup",
        "dot": "#71717A",
        "light_bg": "#F4F4F5",
        "light_fg": "#52525B",
        "dark_bg": "#23252A",
        "dark_fg": "#A1A1AA",
    },
    ST_TRANSIT: {
        "short": "Dalam Perjalanan",
        "dot": "#2563EB",
        "light_bg": "#EFF6FF",
        "light_fg": "#1D4ED8",
        "dark_bg": "#172554",
        "dark_fg": "#93C5FD",
    },
    ST_OUT: {
        "short": "Sedang Diantar",
        "dot": "#D97706",
        "light_bg": "#FFFBEB",
        "light_fg": "#B45309",
        "dark_bg": "#3A250A",
        "dark_fg": "#FCD34D",
    },
    ST_DELIVERED: {
        "short": "Terkirim",
        "dot": "#0D9488",
        "light_bg": "#F0FDFA",
        "light_fg": "#0F766E",
        "dark_bg": "#0F2E2B",
        "dark_fg": "#5EEAD4",
    },
    ST_COMPLETED: {
        "short": "Selesai",
        "dot": "#16A34A",
        "light_bg": "#F0FDF4",
        "light_fg": "#15803D",
        "dark_bg": "#0E2D1C",
        "dark_fg": "#86EFAC",
    },
    ST_RTS_BALSIS: {
        "short": "Gagal - Balsis",
        "dot": "#DC2626",
        "light_bg": "#FEF2F2",
        "light_fg": "#B91C1C",
        "dark_bg": "#381416",
        "dark_fg": "#FCA5A5",
    },
    ST_RTS_REJECTED: {
        "short": "Gagal - Ditolak",
        "dot": "#E11D48",
        "light_bg": "#FFF1F2",
        "light_fg": "#BE123C",
        "dark_bg": "#3B1320",
        "dark_fg": "#FDA4AF",
    },
    ST_RTS_RETURN: {
        "short": "Pengembalian",
        "dot": "#9333EA",
        "light_bg": "#FAF5FF",
        "light_fg": "#7E22CE",
        "dark_bg": "#2B1640",
        "dark_fg": "#D8B4FE",
    },
    ST_CANCELED_BUYER: {
        "short": "Canceled - Pembeli",
        "dot": "#78716C",
        "light_bg": "#F5F5F4",
        "light_fg": "#57534E",
        "dark_bg": "#262422",
        "dark_fg": "#D6D3D1",
    },
    ST_CANCELED_SYSTEM: {
        "short": "Canceled - Sistem",
        "dot": "#C2410C",
        "light_bg": "#FFF7ED",
        "light_fg": "#9A3412",
        "dark_bg": "#3A1C0B",
        "dark_fg": "#FDBA74",
    },
    ST_ERROR: {
        "short": "Kendala Lacak",
        "dot": "#EA580C",
        "light_bg": "#FFF7ED",
        "light_fg": "#C2410C",
        "dark_bg": "#3A1C0B",
        "dark_fg": "#FDBA74",
    },
    ST_UNKNOWN: {
        "short": "Tidak Diketahui",
        "dot": "#94A3B8",
        "light_bg": "#F8FAFC",
        "light_fg": "#64748B",
        "dark_bg": "#1E222B",
        "dark_fg": "#94A3B8",
    },
}

COURIER_META: dict[str, dict[str, str]] = {
    "SPX": {"light_bg": "#FEF2EF", "light_fg": "#C2410C", "dark_bg": "#2E1A15", "dark_fg": "#FDBA74"},
    "POS": {"light_bg": "#FEF9E7", "light_fg": "#A16207", "dark_bg": "#2F260E", "dark_fg": "#FDE047"},
    "JNE": {"light_bg": "#EFF6FF", "light_fg": "#1D4ED8", "dark_bg": "#172544", "dark_fg": "#93C5FD"},
    "ANTERAJA": {"light_bg": "#FAF5FF", "light_fg": "#7E22CE", "dark_bg": "#2B1640", "dark_fg": "#D8B4FE"},
}


def get_tokens(is_dark: bool) -> dict[str, str]:
    return DARK_TOKENS if is_dark else LIGHT_TOKENS


def status_style(status: str, is_dark: bool) -> tuple[str, str, str, str]:
    """Return (bg, fg, dot, short_label) for a given status & theme."""
    meta = STATUS_META.get(status, STATUS_META[ST_UNKNOWN])
    if is_dark:
        return meta["dark_bg"], meta["dark_fg"], meta["dot"], meta["short"]
    return meta["light_bg"], meta["light_fg"], meta["dot"], meta["short"]


def courier_style(courier: str, is_dark: bool) -> tuple[str, str]:
    """Return (bg, fg) for a courier micro-tag."""
    t = get_tokens(is_dark)
    meta = COURIER_META.get((courier or "").upper())
    if not meta:
        return t["bg_subtle"], t["text_secondary"]
    return (meta["dark_bg"], meta["dark_fg"]) if is_dark else (meta["light_bg"], meta["light_fg"])


def payment_style(method: str, is_dark: bool) -> tuple[str, str]:
    """Return (bg, fg) for a payment method micro-tag (COD vs Non-COD)."""
    m = (method or "").strip().lower()
    is_cod = any(k in m for k in ("cod", "bayar di tempat", "cash on delivery"))
    if is_cod:
        return ("#362409", "#FCD34D") if is_dark else ("#FEF3C7", "#92400E")
    return ("#0F2D33", "#67E8F9") if is_dark else ("#ECFEFF", "#0E7490")


def duplicate_style(is_dark: bool) -> tuple[str, str]:
    """Return (bg, fg) for duplicate-recipient micro-badges."""
    return ("#2B1640", "#C4B5FD") if is_dark else ("#F3E8FF", "#6D28D9")


def build_palette(is_dark: bool) -> QPalette:
    """Explicit QPalette so Windows OS dark mode never bleeds into Qt Fusion controls."""
    t = get_tokens(is_dark)
    pal = QPalette()

    bg_canvas = QColor(t["bg_canvas"])
    bg_surface = QColor(t["bg_surface"])
    bg_subtle = QColor(t["bg_subtle"])
    text_primary = QColor(t["text_primary"])
    text_secondary = QColor(t["text_secondary"])
    text_muted = QColor(t["text_muted"])
    border = QColor(t["border_control"])
    selected = QColor(t["bg_selected"])
    accent = QColor(t["accent"])

    for grp in (QPalette.Active, QPalette.Inactive):
        pal.setColor(grp, QPalette.Window, bg_canvas)
        pal.setColor(grp, QPalette.WindowText, text_primary)
        pal.setColor(grp, QPalette.Base, bg_surface)
        pal.setColor(grp, QPalette.AlternateBase, bg_surface)
        pal.setColor(grp, QPalette.ToolTipBase, QColor(t["tooltip_bg"]))
        pal.setColor(grp, QPalette.ToolTipText, QColor(t["tooltip_fg"]))
        pal.setColor(grp, QPalette.Text, text_primary)
        pal.setColor(grp, QPalette.Button, bg_surface)
        pal.setColor(grp, QPalette.ButtonText, text_primary)
        pal.setColor(grp, QPalette.BrightText, QColor(t["accent_text"]))
        pal.setColor(grp, QPalette.Highlight, selected)
        pal.setColor(grp, QPalette.HighlightedText, text_primary)
        pal.setColor(grp, QPalette.PlaceholderText, text_muted)
        pal.setColor(grp, QPalette.Mid, border)
        pal.setColor(grp, QPalette.Midlight, bg_subtle)
        pal.setColor(grp, QPalette.Light, bg_surface)
        pal.setColor(grp, QPalette.Dark, border)
        pal.setColor(grp, QPalette.Shadow, border)
        pal.setColor(grp, QPalette.Link, accent)
        pal.setColor(grp, QPalette.LinkVisited, accent)

    pal.setColor(QPalette.Disabled, QPalette.WindowText, text_muted)
    pal.setColor(QPalette.Disabled, QPalette.Text, text_muted)
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, text_muted)
    pal.setColor(QPalette.Disabled, QPalette.Base, bg_subtle)
    pal.setColor(QPalette.Disabled, QPalette.Button, bg_subtle)
    pal.setColor(QPalette.Disabled, QPalette.Highlight, bg_subtle)
    pal.setColor(QPalette.Disabled, QPalette.HighlightedText, text_muted)
    return pal


def build_stylesheet(is_dark: bool) -> str:
    """Global application QSS covering every native control, popup menu, and dialog."""
    t = get_tokens(is_dark)
    adir = assets_dir()
    chev_down = (adir / ("chevron_dark.svg" if is_dark else "chevron_light.svg")).as_posix()
    chev_up = (adir / ("chevron_up_dark.svg" if is_dark else "chevron_up_light.svg")).as_posix()
    check_svg = (adir / "check_white.svg").as_posix()
    return f"""
QMainWindow, QDialog, QMessageBox {{
    background-color: {t["bg_canvas"]};
    color: {t["text_primary"]};
}}
QMessageBox QPushButton {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_control"]};
    border-radius: 8px;
    padding: 6px 16px;
    min-width: 72px;
}}
QMessageBox QPushButton:hover {{
    background-color: {t["bg_hover"]};
    border-color: {t["border_hover"]};
}}
QWidget {{
    font-family: 'Segoe UI Variable Text', 'Segoe UI', '-apple-system', 'SF Pro Text', 'Inter', 'Ubuntu', 'Noto Sans', sans-serif;
    font-size: 13px;
    color: {t["text_primary"]};
}}

/* Containers & Dividers */
#LeftRail {{
    background-color: {t["bg_canvas"]};
    border-right: 1px solid {t["border_hairline"]};
}}
#CenterWorkspace {{
    background-color: {t["bg_surface"]};
}}
#WorkspaceHeader {{
    background-color: {t["bg_surface"]};
    border-bottom: 1px solid {t["border_hairline"]};
}}
#WorkspaceSubHeader {{
    background-color: {t["bg_surface"]};
    border-bottom: 1px solid {t["border_hairline"]};
}}
#SegmentedBar {{
    background-color: {t["bg_canvas"]};
    border: 1px solid {t["border_hairline"]};
    border-radius: 9px;
    padding: 2px;
}}
#InspectorRoot {{
    background-color: {t["bg_surface"]};
    border-left: 1px solid {t["border_hairline"]};
}}
#InspectorHeader {{
    background-color: {t["bg_surface"]};
    border-bottom: 1px solid {t["border_hairline"]};
}}
#SyncBanner {{
    background-color: {t["bg_canvas"]};
    border-bottom: 1px solid {t["border_hairline"]};
}}
#InfoCard {{
    background-color: {t["bg_subtle"]};
    border: 1px solid {t["border_hairline"]};
    border-radius: 10px;
}}
#EmptyCircle {{
    background-color: {t["bg_subtle"]};
    border: 1px solid {t["border_hairline"]};
    border-radius: 28px;
}}
QFrame#HairlineH {{
    background-color: {t["border_hairline"]};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* ScrollAreas MUST be transparent so parent canvas/surface shows cleanly */
QScrollArea, QScrollArea > QWidget > QWidget {{
    background-color: transparent;
    border: none;
}}

/* Labels */
QLabel {{
    background-color: transparent;
}}
QLabel#SectionOverline {{
    color: {t["text_muted"]};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}
QLabel#MutedText {{
    color: {t["text_secondary"]};
    font-size: 12px;
}}
QLabel#CountPill {{
    background-color: {t["bg_subtle"]};
    color: {t["text_secondary"]};
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 600;
}}

/* Buttons */
QPushButton {{
    border-radius: 8px;
    font-weight: 500;
    font-size: 12.5px;
    padding: 6px 14px;
    outline: none;
}}
QPushButton#PrimaryBtn {{
    background-color: {t["accent"]};
    color: {t["accent_text"]};
    border: 1px solid {t["accent"]};
    font-weight: 600;
    padding: 7px 14px;
}}
QPushButton#PrimaryBtn:hover {{
    background-color: {t["accent_hover"]};
    border-color: {t["accent_hover"]};
}}
QPushButton#PrimaryBtn:disabled {{
    background-color: {t["bg_subtle"]};
    border-color: {t["border_hairline"]};
    color: {t["text_muted"]};
}}

QPushButton#SecondaryBtn {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_control"]};
    padding: 6px 12px;
}}
QPushButton#SecondaryBtn:hover {{
    background-color: {t["bg_hover"]};
    border-color: {t["border_hover"]};
}}
QPushButton#SecondaryBtn:pressed {{
    background-color: {t["bg_subtle"]};
}}
QPushButton#SecondaryBtn:disabled {{
    color: {t["text_muted"]};
    background-color: {t["bg_subtle"]};
    border-color: {t["border_hairline"]};
}}

QPushButton#GhostBtn {{
    background-color: transparent;
    color: {t["text_secondary"]};
    border: 1px solid transparent;
    padding: 5px 10px;
}}
QPushButton#GhostBtn:hover {{
    background-color: {t["bg_subtle"]};
    color: {t["text_primary"]};
}}

QPushButton#IconBtn {{
    background-color: {t["bg_surface"]};
    color: {t["text_secondary"]};
    border: 1px solid {t["border_control"]};
    border-radius: 8px;
    padding: 0px;
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
}}
QPushButton#IconBtn:hover {{
    background-color: {t["bg_hover"]};
    border-color: {t["border_hover"]};
    color: {t["text_primary"]};
}}
QPushButton#IconBtn::menu-indicator {{
    image: none;
    width: 0px;
}}

QPushButton#GhostIconBtn {{
    background-color: transparent;
    color: {t["text_secondary"]};
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 0px;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
}}
QPushButton#GhostIconBtn:hover {{
    background-color: {t["bg_subtle"]};
    color: {t["text_primary"]};
}}
QPushButton#GhostIconBtn::menu-indicator {{
    image: none;
    width: 0px;
}}

QPushButton#DangerBtn {{
    background-color: {t["danger_soft"]};
    color: {t["danger_fg"]};
    border: 1px solid transparent;
    font-weight: 600;
    padding: 6px 12px;
}}
QPushButton#DangerBtn:hover {{
    border-color: {t["danger"]};
}}

/* Sidebar Nav Item */
QPushButton#NavItem {{
    background-color: transparent;
    color: {t["text_secondary"]};
    border: none;
    border-radius: 7px;
    padding: 4px 10px;
    text-align: left;
    font-size: 12px;
    font-weight: 500;
}}
QPushButton#NavItem:hover {{
    background-color: {t["bg_subtle"]};
    color: {t["text_primary"]};
}}
QPushButton#NavItem:checked {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    font-weight: 600;
    border: 1px solid {t["border_hairline"]};
}}
QPushButton#RailUtilBtn {{
    background-color: transparent;
    color: {t["text_secondary"]};
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 4px 8px;
    font-size: 11.5px;
    font-weight: 500;
}}
QPushButton#RailUtilBtn:hover {{
    background-color: {t["bg_subtle"]};
    color: {t["text_primary"]};
}}

/* Workspace Checklist Filter Popover & Chips */
QPushButton#FilterActiveBtn {{
    background-color: {t["accent_soft"]};
    color: {t["accent"]};
    border: 1px solid {t["accent"]};
    font-weight: 600;
    padding: 6px 12px;
}}
QFrame#FilterPopover {{
    background-color: {t["bg_surface"]};
    border: 1px solid {t["border_control"]};
    border-radius: 12px;
}}
QFrame#FilterChipBar {{
    background-color: {t["bg_canvas"]};
    border-bottom: 1px solid {t["border_hairline"]};
}}
QPushButton#FilterChip {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_control"]};
    border-radius: 11px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 500;
}}
QPushButton#FilterChip:hover {{
    border-color: {t["accent"]};
}}
QPushButton#FilterChipExclude {{
    background-color: {t["danger_soft"]};
    color: {t["danger_fg"]};
    border: 1px solid {t["danger_fg"]};
    border-radius: 11px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#FilterChipExclude:hover {{
    background-color: {t["danger_soft"]};
}}
QPushButton#PresetChip {{
    background-color: {t["bg_subtle"]};
    color: {t["text_secondary"]};
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 500;
}}
QPushButton#PresetChip:hover {{
    color: {t["text_primary"]};
}}
QPushButton#PresetChip:checked {{
    background-color: {t["accent_soft"]};
    color: {t["accent"]};
    border-color: {t["accent"]};
    font-weight: 600;
}}
QPushButton#DualFilterChip {{
    background-color: transparent;
    color: {t["text_secondary"]};
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 500;
    text-align: left;
}}
QPushButton#DualFilterChip:hover {{
    background-color: {t["bg_subtle"]};
    color: {t["text_primary"]};
}}
QPushButton#DualFilterChip[mode="include"] {{
    background-color: {t["accent_soft"]};
    color: {t["accent"]};
    border-color: {t["accent"]};
    font-weight: 600;
}}
QPushButton#DualFilterChip[mode="exclude"] {{
    background-color: {t["danger_soft"]};
    color: {t["danger_fg"]};
    border-color: {t["danger_fg"]};
    font-weight: 600;
}}
QFrame#DupPopoverItem {{
    background-color: transparent;
    border: 1px solid {t["border_hairline"]};
    border-radius: 9px;
}}
QFrame#DupPopoverItem:hover {{
    background-color: {t["bg_subtle"]};
    border-color: {t["accent"]};
}}
QLabel#DupCurrentTag {{
    background-color: {t["accent_soft"]};
    color: {t["accent"]};
    border-radius: 5px;
    padding: 1px 6px;
    font-size: 10px;
    font-weight: 600;
}}

/* Category Header Button & Segmented Switcher & Action Strip */
QPushButton#CatNavItem {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 4px 10px;
    text-align: left;
}}
QPushButton#CatNavItem:hover {{
    background-color: {t["bg_subtle"]};
}}
QPushButton#CatNavItem:checked {{
    background-color: {t["bg_surface"]};
    border: 1px solid {t["border_hairline"]};
}}
QPushButton#SegBtn {{
    background-color: transparent;
    color: {t["text_secondary"]};
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 4px 9px;
    font-size: 11.5px;
    font-weight: 500;
}}
QPushButton#SegBtn:hover {{
    color: {t["text_primary"]};
}}
QPushButton#SegBtn:checked {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_hairline"]};
    font-weight: 600;
}}

/* Inputs */
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_control"]};
    border-radius: 8px;
    padding: 6px 11px;
    selection-background-color: {t["accent_soft"]};
    selection-color: {t["text_primary"]};
}}
QLineEdit, QSpinBox, QDoubleSpinBox {{
    min-height: 20px;
}}
QSpinBox, QDoubleSpinBox {{
    padding-right: 26px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 24px;
    border: none;
    background: transparent;
    border-radius: 4px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {t["bg_subtle"]};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    margin: 2px 2px 0 0;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    margin: 0 2px 2px 0;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{chev_up}");
    width: 11px;
    height: 11px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{chev_down}");
    width: 11px;
    height: 11px;
}}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {t["border_hover"]};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1.5px solid {t["accent"]};
}}
QLineEdit#SearchInput {{
    background-color: {t["bg_canvas"]};
    border: 1px solid {t["border_control"]};
    border-radius: 8px;
    padding: 6px 10px 6px 10px;
    min-height: 20px;
}}
QLineEdit#SearchInput:focus {{
    background-color: {t["bg_surface"]};
    border: 1.5px solid {t["accent"]};
}}
QDateEdit {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_control"]};
    border-radius: 7px;
    padding: 4px 24px 4px 8px;
    min-height: 20px;
    font-size: 12px;
}}
QDateEdit:hover {{
    border-color: {t["border_hover"]};
}}
QDateEdit:focus {{
    border: 1.5px solid {t["accent"]};
}}
QDateEdit::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 20px;
    border: none;
}}
QDateEdit::down-arrow {{
    image: url("{chev_down}");
    width: 11px;
    height: 11px;
}}
QCalendarWidget QWidget {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
}}
QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background-color: {t["bg_subtle"]};
    border-bottom: 1px solid {t["border_hairline"]};
    padding: 4px;
}}
QCalendarWidget QToolButton {{
    color: {t["text_primary"]};
    background-color: transparent;
    border: none;
    border-radius: 5px;
    padding: 4px 8px;
    font-weight: 600;
}}
QCalendarWidget QToolButton:hover {{
    background-color: {t["bg_hover"]};
}}
QCalendarWidget QAbstractItemView:enabled {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    selection-background-color: {t["accent"]};
    selection-color: {t["accent_text"]};
}}
QCalendarWidget QAbstractItemView:disabled {{
    color: {t["text_muted"]};
}}

/* ComboBox & Popup List */
QComboBox {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_control"]};
    border-radius: 8px;
    padding: 6px 22px 6px 10px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {t["border_hover"]};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
}}
QComboBox::down-arrow {{
    image: url("{chev_down}");
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_control"]};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {t["bg_subtle"]};
    selection-color: {t["text_primary"]};
}}
QComboBox QAbstractItemView::item {{
    padding: 7px 10px;
    border-radius: 6px;
    min-height: 22px;
}}

/* CheckBox */
QCheckBox {{
    color: {t["text_secondary"]};
    spacing: 8px;
    font-size: 12.5px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1.5px solid {t["border_hover"]};
    background-color: {t["bg_surface"]};
}}
QCheckBox::indicator:hover {{
    border-color: {t["accent"]};
}}
QCheckBox::indicator:checked {{
    background-color: {t["accent"]};
    border-color: {t["accent"]};
    image: url("{check_svg}");
}}

/* Popup Menu (Fixes OS Dark Menu Bleed!) */
QMenu {{
    background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_control"]};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    background-color: transparent;
    color: {t["text_primary"]};
    padding: 7px 14px;
    border-radius: 6px;
    font-size: 12.5px;
}}
QMenu::item:selected {{
    background-color: {t["bg_subtle"]};
    color: {t["text_primary"]};
}}
QMenu::item:disabled {{
    color: {t["text_muted"]};
}}
QMenu::separator {{
    height: 1px;
    background-color: {t["border_hairline"]};
    margin: 5px 6px;
}}

/* Master Table */
QTableView {{
    background-color: {t["bg_surface"]};
    alternate-background-color: {t["bg_surface"]};
    color: {t["text_primary"]};
    border: none;
    gridline-color: transparent;
    selection-background-color: {t["bg_selected"]};
    selection-color: {t["text_primary"]};
    outline: none;
}}
QTableView::item {{
    border: none;
}}
QHeaderView {{
    background-color: {t["bg_surface"]};
    border: none;
}}
QHeaderView::section {{
    background-color: {t["bg_surface"]};
    color: {t["text_muted"]};
    font-size: 10.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    border: none;
    border-bottom: 1px solid {t["border_hairline"]};
    padding: 10px 12px;
}}

/* Tabs in Settings Dialog */
QTabWidget::pane {{
    border: 1px solid {t["border_hairline"]};
    border-radius: 10px;
    background-color: {t["bg_surface"]};
    padding: 12px;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {t["text_secondary"]};
    padding: 8px 14px;
    margin-right: 4px;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:hover {{
    color: {t["text_primary"]};
}}
QTabBar::tab:selected {{
    color: {t["accent"]};
    border-bottom: 2px solid {t["accent"]};
    font-weight: 600;
}}

/* Splitter, ProgressBar, StatusBar, ScrollBars, ToolTip */
QSplitter::handle {{
    background-color: {t["border_hairline"]};
    width: 1px;
}}
QProgressBar {{
    background-color: {t["border_hairline"]};
    border: none;
    border-radius: 1px;
    max-height: 3px;
    min-height: 3px;
}}
QProgressBar::chunk {{
    background-color: {t["accent"]};
    border-radius: 1px;
}}
QStatusBar {{
    background-color: {t["bg_canvas"]};
    border-top: 1px solid {t["border_hairline"]};
    color: {t["text_secondary"]};
    font-size: 11.5px;
    padding: 2px 12px;
}}
QToolTip {{
    background-color: {t["tooltip_bg"]};
    color: {t["tooltip_fg"]};
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 11.5px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 4px 1px;
}}
QScrollBar::handle:vertical {{
    background: {t["scroll_thumb"]};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t["scroll_thumb_hover"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0px;
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 1px 4px;
}}
QScrollBar::handle:horizontal {{
    background: {t["scroll_thumb"]};
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {t["scroll_thumb_hover"]};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    width: 0px;
    background: transparent;
}}
"""


def apply_windows_titlebar_theme(widget: QWidget, is_dark: bool) -> None:
    """Sync the Windows 11 DWM title bar dark/light mode with the app theme."""
    if os.name != "nt":
        return
    try:
        hwnd = int(widget.winId())
        val = ctypes.c_int(1 if is_dark else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 11 / Win10 20H1+)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass
