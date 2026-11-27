#!/usr/bin/env bash
# Build 1 single standalone Linux binary (dist/ResiTrack-Linux)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller --noconfirm --clean ResiTrack.spec
mv -f dist/ResiTrack dist/ResiTrack-Linux
chmod +x dist/ResiTrack-Linux

echo "Selesai: dist/ResiTrack-Linux (1 file standalone)"
