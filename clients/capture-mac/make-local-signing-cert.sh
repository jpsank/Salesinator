#!/bin/sh
# Create a local code-signing certificate for Vexa Capture, once, and put it in your login keychain.
#
# Why: without a stable signature every rebuild is a new app to macOS, so the Keychain, Microphone, Screen Recording,
# Automation and Notifications approvals all come back each time. Signed with this certificate every build keeps the
# same identity (macOS pins approvals to the certificate), so each is asked once. It is for builds on THIS Mac; a
# Developer ID (SIGN_IDENTITY, see release.sh) is still what distribution needs.
#
# The private key is generated here, imported into the login keychain and never written anywhere else; no secrets are
# stored in the repo. build.sh uses the certificate automatically when it exists.
set -eu
NAME="${LOCAL_SIGN_NAME:-Vexa Capture Local Signing}"

if security find-identity -p codesigning 2>/dev/null | grep -q "\"$NAME\""; then
  echo "already set up: \"$NAME\" is in your keychain"; exit 0
fi

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
security import "$T/id.p12" -k "$HOME/Library/Keychains/login.keychain-db" -P "$PASS" -T /usr/bin/codesign >/dev/null
echo "created \"$NAME\" in your login keychain (valid 10 years)"
