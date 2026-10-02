#!/bin/sh
# Create a local code-signing certificate for Vexa Capture, once, in a keychain of its own.
#
# Why: without a stable signature every rebuild is a new app to macOS, so the Keychain, Microphone, Screen Recording,
# Automation and Notifications approvals all come back each time. Signed with this certificate every build keeps the
# same identity (macOS pins approvals to the certificate), so each is asked once. It is for builds on THIS Mac; a
# Developer ID (SIGN_IDENTITY, see release.sh) is still what distribution needs.
#
# The certificate lives in ~/Library/Keychains/vexa-capture-signing.keychain-db, a keychain with an empty password that
# nothing else uses — not your login keychain. In the login keychain macOS asks for your keychain password every time
# `codesign` uses the key, and the dialog can loop; here the access rules are set once, by this script, with no dialog.
# The cost: the key is protected only by that file's permissions, which is the right trade for a key that only signs
# local builds of this app. No secret is stored in the repo. build.sh uses the certificate automatically.
set -eu
NAME="${LOCAL_SIGN_NAME:-Vexa Capture Local Signing}"
KC="${LOCAL_SIGN_KEYCHAIN:-$HOME/Library/Keychains/vexa-capture-signing.keychain-db}"

[ -f "$KC" ] || security create-keychain -p "" "$KC"
security set-keychain-settings "$KC"            # never auto-lock
security unlock-keychain -p "" "$KC"

if ! security find-identity -p codesigning "$KC" 2>/dev/null | grep -q "\"$NAME\""; then
  T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
  cat > "$T/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$T/cert.cnf" -keyout "$T/key.pem" -out "$T/cert.pem" 2>/dev/null
  PASS="$(openssl rand -hex 16)"
  # macOS's `security` reads only the older PKCS12 encryption, not what a current openssl writes by default.
  openssl pkcs12 -export -certpbe PBE-SHA1-3DES -keypbe PBE-SHA1-3DES -macalg sha1 -inkey "$T/key.pem" -in "$T/cert.pem" -name "$NAME" -passout "pass:$PASS" -out "$T/id.p12" 2>/dev/null
  security import "$T/id.p12" -k "$KC" -P "$PASS" -T /usr/bin/codesign >/dev/null
  echo "created \"$NAME\" in $KC (valid 10 years)"
else
  echo "already set up: \"$NAME\" is in $KC"
fi
# Let Apple's own tools (codesign) use the key without asking.
security set-key-partition-list -S apple-tool:,apple: -s -k "" "$KC" >/dev/null 2>&1
