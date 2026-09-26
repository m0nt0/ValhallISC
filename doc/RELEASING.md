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

### Architectures (Apple silicon and Intel)

The build has the architecture of the Python that runs it. Output goes to `dist/macos-<arch>/`.
- **Apple silicon:** `.venv` (arm64).
- **Intel:** an x86_64 Python run under Rosetta. One-time setup, with no installer and no sudo:
  ```sh
  .venv/bin/pip install uv
  .venv/bin/uv python install cpython-3.12-macos-x86_64-none
  PYX=$(.venv/bin/uv python find cpython-3.12-macos-x86_64-none)
  arch -x86_64 "$PYX" -m venv .venv-x86_64
  arch -x86_64 .venv-x86_64/bin/pip install -e ".[gui,dev,build]"
  ```
  Then sign and notarize with `arch -x86_64 env PYTHON=.venv-x86_64/bin/python scripts/sign-macos.sh`. FUSE-T ships a universal library, so the Intel app can even be tested on Apple silicon under Rosetta.

## All platforms in one go

```sh
SIGN_IDENTITY="Developer ID Application: <Name> (<TEAMID>)" NOTARY_PROFILE=valhallisc-notary scripts/release.sh
```

It refuses to run with uncommitted changes, so every artifact carries the same git revision. It:
1. builds, signs and notarizes the macOS arm64 and x86_64 DMGs;
2. builds the Linux x86_64 binary (under emulation on Apple silicon) and the aarch64 binary;
3. smoke-tests both Linux binaries on clean containers of their own architecture;
4. collects everything in `dist/release/<version>/` with `SHA256SUMS`.

## Linux

`scripts/build-linux.sh` produces `dist/valhallisc-linux-<arch>` in Docker (Ubuntu 24.04 base, so glibc 2.39 or newer is needed). Smoke-test it on a clean machine with `scripts/smoke-binary.sh`.

## Windows

A Mac can't build for Windows: Rosetta only translates macOS programs, and Docker Desktop only runs Linux containers. A Wine cross-build would be untestable, because WinFsp is a kernel driver. Instead, the GitHub Actions workflow `.github/workflows/windows.yml` builds on a real Windows machine. It runs on every `v*` tag, on pull requests that touch the code, and on demand (Actions → Windows build → Run workflow). It runs the unit tests on Windows, smoke-tests the CLI, and uploads `ValhallISC.exe` and `valhallisc-cli.exe` as an artifact.

On a Windows machine, `scripts\build-windows.ps1` produces `dist\ValhallISC.exe` and `dist\valhallisc-cli.exe`. Signing them with an Authenticode certificate (`signtool sign /fd SHA256 /tr <timestamp url> /td SHA256 …`) avoids SmartScreen warnings. That isn't automated yet.
