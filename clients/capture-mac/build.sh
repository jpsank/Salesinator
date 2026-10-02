#!/bin/sh
# Build "Vexa Capture.app" with only the Command Line Tools (no Xcode, no SwiftPM): swiftc compiles Core/ and App/,
# the bundle is assembled by hand, and it is ad-hoc signed so macOS keeps its permissions between runs of the SAME
# build. A rebuild is a new signature, so macOS asks for the Microphone and Screen Recording permissions again.
set -eu
cd "$(dirname "$0")"
SDK="$(xcrun --show-sdk-path)"
APP=".build/Vexa Capture.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -sdk "$SDK" -target arm64-apple-macos13.0 Core/*.swift App/*.swift -o "$APP/Contents/MacOS/VexaCapture"
cp Info.plist "$APP/Contents/Info.plist"
# VEXA_ADDRESS=https://terminal.example.com ./build.sh — bake in the address the first-run prompt offers (default: this Mac's).
if [ -n "${VEXA_ADDRESS:-}" ]; then
  /usr/libexec/PlistBuddy -c "Add :VexaDefaultAddress string $VEXA_ADDRESS" "$APP/Contents/Info.plist"
fi
codesign --force --sign - --identifier ai.vexa.capture "$APP"
echo "built: $APP"
