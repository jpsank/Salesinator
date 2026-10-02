#!/bin/sh
# Build "Vexa Capture.app" with only the Command Line Tools (no Xcode, no SwiftPM): swiftc compiles Core/ and App/,
# the bundle is assembled by hand. Signing: a Developer ID (SIGN_IDENTITY) for releases; else the local certificate that
# ./make-local-signing-cert.sh puts in your keychain — one identity across rebuilds, so macOS asks for the Keychain,
# Microphone, Screen Recording, Automation and Notifications approvals once; else ad-hoc, where every rebuild is a new
# app to macOS and all of them are asked again.
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
# VEXA_UPDATE_FEED=https://…/latest.json — the address "Check for updates…" reads (the release script writes that file).
if [ -n "${VEXA_UPDATE_FEED:-}" ]; then
  /usr/libexec/PlistBuddy -c "Add :VexaUpdateFeed string $VEXA_UPDATE_FEED" "$APP/Contents/Info.plist"
fi
# SIGN_IDENTITY="Developer ID Application: …" signs with the hardened runtime and the entitlements notarization needs, and gives the
# app one identity across builds — so macOS stops re-asking for permissions and the Keychain password after every rebuild.
if [ -n "${SIGN_IDENTITY:-}" ]; then
  codesign --force --timestamp --options runtime --entitlements App.entitlements --sign "$SIGN_IDENTITY" --identifier ai.vexa.capture "$APP"
else
  LOCAL_ID="$(security find-identity -p codesigning 2>/dev/null | awk -v n="\"${LOCAL_SIGN_NAME:-Vexa Capture Local Signing}\"" 'index($0, n) { print $2; exit }')"
  if [ -n "$LOCAL_ID" ]; then
    codesign --force --sign "$LOCAL_ID" --identifier ai.vexa.capture "$APP"
    echo "signed with the local certificate (one identity across rebuilds)"
  else
    codesign --force --sign - --identifier ai.vexa.capture "$APP"
    echo "signed ad-hoc — run ./make-local-signing-cert.sh once so rebuilds stop re-asking for permissions"
  fi
fi
echo "built: $APP"
