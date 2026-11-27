"""ResiTrack entry point with custom Windows branding & taskbar integration."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from resitrack.config import assets_dir
from resitrack.icons import clear_cache
from resitrack.main_window import MainWindow

ASSETS_DIR = assets_dir()
ICON_PATH = ASSETS_DIR / "icon.png"
ICO_PATH = ASSETS_DIR / "icon.ico"


def setup_windows_taskbar_icon():
    """Ensure Windows Taskbar displays the application icon instead of default Python icon."""
    if os.name == "nt":
        try:
            myappid = "ResiTrack.ShipmentTracker.Desktop.1.0"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass


def main():
    setup_windows_taskbar_icon()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("ResiTrack")
    app.setStyle("Fusion")
    app.aboutToQuit.connect(clear_cache)

    # Set Application Icon (Taskbar & Alt-Tab)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    elif ICO_PATH.exists():
        app.setWindowIcon(QIcon(str(ICO_PATH)))

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
