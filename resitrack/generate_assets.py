"""Generate minimalist Swiss-modern branding assets for ResiTrack (PNG, ICO, banner)."""
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)


def render_logo(size: int = 512) -> QImage:
    """Render a symmetric, minimal geometric parcel emblem inside a warm vermilion squircle."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    s = size / 512.0
    margin = 34.0 * s
    radius = 114.0 * s
    bg_rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)

    # Clean coral-vermilion gradient
    grad = QLinearGradient(margin, margin, size - margin, size - margin)
    grad.setColorAt(0.0, QColor("#F25C3B"))
    grad.setColorAt(1.0, QColor("#DC3D1E"))

    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(bg_rect, radius, radius)

    # Hairline inner rim
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 45), max(1.0, 2.5 * s)))
    p.drawRoundedRect(bg_rect.adjusted(1.5 * s, 1.5 * s, -1.5 * s, -1.5 * s), radius, radius)

    # Symmetric hexagon / isometric parcel centered cleanly
    cx = size / 2.0
    cy = size / 2.0 + 4.0 * s
    dx = 116.0 * s
    dy_top = 62.0 * s
    dy_mid = 128.0 * s

    top_v = QPointF(cx, cy - dy_mid)
    right_top = QPointF(cx + dx, cy - dy_top)
    right_bot = QPointF(cx + dx, cy + dy_top)
    bot_v = QPointF(cx, cy + dy_mid)
    left_bot = QPointF(cx - dx, cy + dy_top)
    left_top = QPointF(cx - dx, cy - dy_top)
    center_v = QPointF(cx, cy - 2.0 * s)

    # Top face (pure crisp white)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    p.drawPolygon(QPolygonF([top_v, right_top, center_v, left_top]))

    # Left face (soft warm porcelain)
    p.setBrush(QColor("#F2EAE7"))
    p.drawPolygon(QPolygonF([left_top, center_v, bot_v, left_bot]))

    # Right face (subtle cool shadow)
    p.setBrush(QColor("#DFD5D1"))
    p.drawPolygon(QPolygonF([center_v, right_top, right_bot, bot_v]))

    # Minimal tape ribbon stripe across top face + clean checkmark notch
    ribbon = QPolygonF([
        QPointF(cx - dx * 0.45, cy - dy_mid + (dy_mid - dy_top) * 0.45),
        QPointF(cx + dx * 0.45, cy - dy_mid + (dy_mid - dy_top) * 0.45 + 16.0 * s),
        QPointF(cx + dx * 0.45 - 24.0 * s, cy - dy_mid + (dy_mid - dy_top) * 0.45 + 32.0 * s),
        QPointF(cx - dx * 0.45 - 24.0 * s, cy - dy_mid + (dy_mid - dy_top) * 0.45 + 16.0 * s),
    ])
    p.setBrush(QColor("#E84C2B"))
    p.drawPolygon(ribbon)

    # Clean vertical tracking notch on right face
    stroke_pen = QPen(QColor("#E84C2B"), max(1.5, 16.0 * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(stroke_pen)
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(cx + dx * 0.36, cy + 44.0 * s), QPointF(cx + dx * 0.56, cy + 28.0 * s))

    # Crisp edge hairlines inside parcel for Swiss precision
    p.setPen(QPen(QColor("#D0C5C0"), max(0.8, 1.8 * s)))
    p.drawLine(left_top, center_v)
    p.drawLine(center_v, right_top)
    p.drawLine(center_v, bot_v)

    p.end()
    return img


def render_banner(width: int = 340, height: int = 56) -> QImage:
    img = QImage(width, height, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    logo_icon = render_logo(44)
    p.drawImage(6, 6, logo_icon)

    font_title = QFont("Segoe UI", 16, QFont.Bold)
    font_title.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
    p.setFont(font_title)
    p.setPen(QColor("#111318"))
    p.drawText(58, 28, "ResiTrack")

    font_sub = QFont("Segoe UI", 9)
    p.setFont(font_sub)
    p.setPen(QColor("#5E6574"))
    p.drawText(59, 44, "Shopee Multi-Courier Tracker")

    p.end()
    return img


def main():
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    assets_dir = Path(__file__).resolve().parents[1] / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    img512 = render_logo(512)
    png_path = assets_dir / "icon.png"
    img512.save(str(png_path), "PNG")
    print(f"Created: {png_path} ({png_path.stat().st_size} bytes)")

    for s in (256, 128, 64, 48, 32, 16):
        render_logo(s).save(str(assets_dir / f"icon_{s}.png"), "PNG")

    ico_img = render_logo(256)
    ico_path = assets_dir / "icon.ico"
    ico_img.save(str(ico_path), "ICO")
    print(f"Created: {ico_path} ({ico_path.stat().st_size} bytes)")

    banner_img = render_banner(340, 56)
    banner_path = assets_dir / "logo_banner.png"
    banner_img.save(str(banner_path), "PNG")
    print(f"Created: {banner_path}")


if __name__ == "__main__":
    main()
