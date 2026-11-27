#!/usr/bin/env bash
# Build standalone macOS app bundle (dist/ResiTrack.app) & installer DMG (dist/ResiTrack-macOS.dmg)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

# Generate native macOS .icns from assets/icon.png
if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1 && [ -f "assets/icon.png" ]; then
    ICONSET_DIR="$(mktemp -d)/ResiTrack.iconset"
    mkdir -p "$ICONSET_DIR"
    sips -z 16 16     assets/icon.png --out "$ICONSET_DIR/icon_16x16.png"      >/dev/null
    sips -z 32 32     assets/icon.png --out "$ICONSET_DIR/icon_16x16@2x.png"   >/dev/null
    sips -z 32 32     assets/icon.png --out "$ICONSET_DIR/icon_32x32.png"      >/dev/null
    sips -z 64 64     assets/icon.png --out "$ICONSET_DIR/icon_32x32@2x.png"   >/dev/null
    sips -z 128 128   assets/icon.png --out "$ICONSET_DIR/icon_128x128.png"    >/dev/null
    sips -z 256 256   assets/icon.png --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
    sips -z 256 256   assets/icon.png --out "$ICONSET_DIR/icon_256x256.png"    >/dev/null
    sips -z 512 512   assets/icon.png --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
    sips -z 512 512   assets/icon.png --out "$ICONSET_DIR/icon_512x512.png"    >/dev/null
    sips -z 1024 1024 assets/icon.png --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
    iconutil -c icns "$ICONSET_DIR" -o assets/icon.icns
    rm -rf "$(dirname "$ICONSET_DIR")"
fi

pyinstaller --noconfirm --clean ResiTrack.spec
rm -rf dist/ResiTrack

# Ad-hoc sign .app bundle so Apple Silicon & Intel Macs can launch cleanly
if command -v codesign >/dev/null 2>&1 && [ -d "dist/ResiTrack.app" ]; then
    codesign --force --deep --sign - dist/ResiTrack.app
fi

# Package drag-to-Applications .dmg
if command -v hdiutil >/dev/null 2>&1 && [ -d "dist/ResiTrack.app" ]; then
    DMG_STAGE="$(mktemp -d)"
    cp -R dist/ResiTrack.app "$DMG_STAGE/"
    ln -s /Applications "$DMG_STAGE/Applications"
    rm -f dist/ResiTrack-macOS.dmg
    for attempt in 1 2 3; do
        if hdiutil create -volname "ResiTrack" -srcfolder "$DMG_STAGE" -ov -format UDZO dist/ResiTrack-macOS.dmg; then
            break
        fi
        if [ "$attempt" -eq 3 ]; then
            echo "ERROR: hdiutil create gagal setelah 3 percobaan" >&2
            exit 1
        fi
        sleep 2
    done
    rm -rf "$DMG_STAGE"
fi

echo "Selesai: dist/ResiTrack-macOS.dmg (1 file standalone)"
