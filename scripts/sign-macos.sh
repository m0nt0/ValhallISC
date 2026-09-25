#!/usr/bin/env bash
# Sign (hardened runtime), optionally notarize + staple, and package ValhallISC for distribution.
#
#   SIGN_IDENTITY="Developer ID Application: Your Company (TEAMID)" \
#   NOTARY_PROFILE=valhallisc-notary \
#   scripts/sign-macos.sh [--no-build]
#
# SIGN_IDENTITY   required; a "Developer ID Application" certificate is needed for distribution outside the
#                 App Store (an "Apple Development" one works for local testing but cannot be notarized).
# NOTARY_PROFILE  optional; a notarytool keychain profile created once with
#                 xcrun notarytool store-credentials valhallisc-notary --apple-id you@example.com \
#                     --team-id TEAMID --password <app-specific password>
#                 Without it the app is signed but not notarized (Gatekeeper will still warn).
# Output: dist/ValhallISC.app (signed[, notarized, stapled]) and dist/ValhallISC-<version>-macos-<arch>.dmg
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SIGN_IDENTITY:?set SIGN_IDENTITY (see the header of this script)}"
NOTARY_PROFILE=${NOTARY_PROFILE:-}
APP=dist/ValhallISC.app
ENT=packaging/entitlements.plist
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
ARCH=$(uname -m)
DMG="dist/ValhallISC-${VERSION}-macos-${ARCH}.dmg"

[ "${1:-}" = "--no-build" ] || scripts/build-macos.sh

sign() { codesign --force --timestamp --options runtime --sign "$SIGN_IDENTITY" "$@"; }

echo "== signing nested code (inside-out)"
# every Mach-O file that is not a symlink, deepest first; frameworks are signed as bundles afterwards
find "$APP/Contents/Frameworks" "$APP/Contents/Resources" -type f -print0 \
  | xargs -0 file | grep -E "Mach-O" | cut -d: -f1 \
  | awk '{ print length, $0 }' | sort -rn | cut -d" " -f2- \
  | while IFS= read -r bin; do sign "$bin"; done
find "$APP/Contents/Frameworks" -type d -name "*.framework" -prune -print0 | while IFS= read -r -d '' fw; do
  sign "$fw"
done
echo "== signing the app"
sign --entitlements "$ENT" "$APP/Contents/MacOS/ValhallISC"
sign --entitlements "$ENT" "$APP"

echo "== verifying"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv --verbose=2 "$APP" 2>&1 | grep -E "Authority=|TeamIdentifier|Runtime|flags" | head -5

notarize() {  # $1 = file to submit
  xcrun notarytool submit "$1" --keychain-profile "$NOTARY_PROFILE" --wait
}

if [ -n "$NOTARY_PROFILE" ]; then
  echo "== notarizing the app"
  ZIP=dist/ValhallISC-notarize.zip
  rm -f "$ZIP"; ditto -c -k --keepParent "$APP" "$ZIP"
  notarize "$ZIP"
  xcrun stapler staple "$APP"
  rm -f "$ZIP"
  spctl --assess --type execute --verbose=2 "$APP"
else
  echo "== NOTARY_PROFILE not set: skipping notarization (Gatekeeper will warn on other Macs)"
fi

echo "== building the DMG"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
cp dist/valhallisc "$STAGE/valhallisc"          # CLI wrapper (finds the app next to it or in /Applications)
cp LICENSE "$STAGE/LICENSE.txt"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "ValhallISC ${VERSION}" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"
if [ -n "$NOTARY_PROFILE" ]; then
  echo "== notarizing the DMG"
  notarize "$DMG"
  xcrun stapler staple "$DMG"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
fi
ls -la "$DMG"
echo "done"
