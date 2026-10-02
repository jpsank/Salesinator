#!/bin/sh
# Build Vexa Capture, put it in ~/Applications, register its vexacapture:// pairing link with macOS, and open it.
# (macOS lets "Open at login" register an app that lives in an Applications folder.)
set -eu
cd "$(dirname "$0")"
./build.sh
DEST="$HOME/Applications"
mkdir -p "$DEST"
pkill -x VexaCapture 2>/dev/null || true
rm -rf "$DEST/Vexa Capture.app"
ditto ".build/Vexa Capture.app" "$DEST/Vexa Capture.app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST/Vexa Capture.app" >/dev/null 2>&1 || true
open "$DEST/Vexa Capture.app"
echo "installed: $DEST/Vexa Capture.app"
