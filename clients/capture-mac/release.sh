#!/bin/sh
# Make a signed, notarized, stapled "VexaCapture-<version>.dmg" in dist/ — the build people install and that updates itself.
#
# One-time, with your Apple Developer account (https://developer.apple.com/programs/):
#   1. Install a "Developer ID Application" certificate in your login Keychain (Xcode → Settings → Accounts, or developer.apple.com).
#   2. Store notarization credentials once under a name you choose (an app-specific password from appleid.apple.com):
#        xcrun notarytool store-credentials vexa-notary --apple-id you@example.com --team-id TEAMID
#
# Then:
#   SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=vexa-notary \
#   VEXA_ADDRESS=https://terminal.example.com \
#   VEXA_UPDATE_FEED=https://github.com/<owner>/<repo>/releases/latest/download/latest.json \
#   ./release.sh
#
# It writes dist/VexaCapture-<version>.dmg and dist/latest.json (what "Check for updates…" reads). Uploading both to the release
# the feed address points at is yours to do — the script prints the command and does not run it.
set -eu
cd "$(dirname "$0")"
: "${SIGN_IDENTITY:?set SIGN_IDENTITY to your \"Developer ID Application: …\" certificate name}"
: "${NOTARY_PROFILE:?set NOTARY_PROFILE to the name you gave notarytool store-credentials}"
: "${VEXA_UPDATE_FEED:?set VEXA_UPDATE_FEED to the address latest.json will be served from}"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' Info.plist)"
APP=".build/Vexa Capture.app"
OUT="dist"; DMG="$OUT/VexaCapture-$VERSION.dmg"

./test.sh >/dev/null
./build.sh
codesign --verify --strict --deep "$APP"
codesign -dv "$APP" 2>&1 | grep -q "flags=0x10000(runtime)" || { echo "the app was not signed with the hardened runtime" >&2; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"
ditto -c -k --keepParent "$APP" "$OUT/notarize.zip"
echo "▶ notarizing (a few minutes)…"
xcrun notarytool submit "$OUT/notarize.zip" --keychain-profile "$NOTARY_PROFILE" --wait
rm "$OUT/notarize.zip"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

STAGE="$(mktemp -d)"
ditto "$APP" "$STAGE/Vexa Capture.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Vexa Capture" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$DMG"
spctl --assess --type open --context context:primary-signature "$DMG"

SUM="$(shasum -a 256 "$DMG" | cut -d' ' -f1)"
BASE="${VEXA_UPDATE_FEED%/*}"
printf '{"version": "%s", "url": "%s/%s", "sha256": "%s"}\n' "$VERSION" "$BASE" "$(basename "$DMG")" "$SUM" > "$OUT/latest.json"
echo "✓ $DMG  (sha256 $SUM)"
echo "  upload both files to the release the feed address points at, e.g.:"
echo "    gh release create v$VERSION $DMG $OUT/latest.json --title \"Vexa Capture $VERSION\""
