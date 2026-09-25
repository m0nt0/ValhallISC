# Releasing ValhallISC

## macOS: signed and notarized DMG

To distribute outside the Mac App Store, Apple requires:
1. a **Developer ID Application** certificate (not "Apple Development" and not "Apple Distribution");
2. **notarization** by Apple, stapled to the app and the DMG.

Without both, Gatekeeper blocks the app on other Macs. The App Store itself isn't an option: its sandbox doesn't allow FUSE mounts or helper processes.

### One-time setup

1. **Create the certificate.** The Apple Developer team's **Account Holder** must do this, in <https://developer.apple.com/account/resources/certificates/add>:
   - choose **Developer ID → Developer ID Application**;
   - upload a CSR (Keychain Access → Certificate Assistant → Request a Certificate From a Certificate Authority);
   - download the certificate and double-click it to install it in the login keychain.

   Check it's installed:
   ```sh
   security find-identity -v -p codesigning | grep "Developer ID Application"
   ```
2. **Store the notarization credentials** in the keychain. First create an app-specific password at <https://account.apple.com> → Sign-In and Security → App-Specific Passwords. Then:
   ```sh
   xcrun notarytool store-credentials valhallisc-notary \
       --apple-id <apple id email> --team-id <TEAMID> --password <app-specific password>
   ```
   An App Store Connect API key works too (`--key`, `--key-id`, `--issuer`).

### Each release

```sh
# bump the version in pyproject.toml and src/irisfs/__init__.py (a unit test checks they match), commit, then:
SIGN_IDENTITY="Developer ID Application: <Name> (<TEAMID>)" \
NOTARY_PROFILE=valhallisc-notary \
scripts/sign-macos.sh
```

`scripts/sign-macos.sh`:
1. builds the app (`scripts/build-macos.sh`; pass `--no-build` to reuse `dist/ValhallISC.app`);
2. signs every nested binary inside-out, then the frameworks, then the app, with the **hardened runtime**, a secure timestamp, and `packaging/entitlements.plist`;
3. verifies with `codesign --verify --deep --strict`;
4. notarizes the app (`notarytool submit --wait`), staples it, and checks it with `spctl`;
5. builds `dist/ValhallISC-<version>-macos-<arch>.dmg`, containing the app, the `valhallisc` CLI wrapper, `LICENSE.txt` and an Applications shortcut, then signs, notarizes and staples the DMG.

The entitlements, with the reason for each:

| Entitlement | Why |
|---|---|
| `com.apple.security.cs.disable-library-validation` | The FUSE library the app loads is the user's own macFUSE / FUSE-T, signed by another team. |
| `com.apple.security.cs.allow-unsigned-executable-memory` | Every FUSE operation is a ctypes/libffi callback, which needs writable and executable memory. |

With an **Apple Development** identity the same script produces a signed, hardened, *not notarizable* build. That's useful to check the entitlements on your own Mac, but it's not for distribution.

### Architectures

The build has the architecture of the Python used: `arm64` with the MacPorts or Homebrew Python on Apple silicon. For Intel Macs, build on an Intel Mac, or with an x86_64 Python under Rosetta (`arch -x86_64`). A `universal2` build needs a universal2 Python (python.org installer) and a universal2 wxPython.

## Linux

`scripts/build-linux.sh` produces `dist/valhallisc-linux-<arch>` in Docker (Ubuntu 24.04 base, so glibc 2.39 or newer is needed). Smoke-test it on a clean machine with `scripts/smoke-binary.sh`.

## Windows

`scripts\build-windows.ps1` produces `dist\ValhallISC.exe` and `dist\valhallisc-cli.exe`. Signing them with an Authenticode certificate (`signtool sign /fd SHA256 /tr <timestamp url> /td SHA256 …`) avoids SmartScreen warnings. That isn't automated yet.
