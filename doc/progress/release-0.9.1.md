# Release 0.9.1 (2026-09-26)

Fixes the Windows Profiles window, which never opened in 0.9.0: the profile list combined two border styles
(`HLB_DEFAULT_STYLE` already contains `BORDER_SUNKEN`, plus `BORDER_THEME`) and wxMSW asserts on that
("unknown border style"). The Windows CI now also runs the GUI tests with real wxMSW widgets, so this class of
bug fails the build.

Built with `scripts/release.sh` from the clean commit **91b7fab** (tag `v0.9.1`). Every artifact reports `valhallisc 0.9.1 (91b7fab)`. Output: `dist/release/0.9.1/`.

| Artifact | Size | SHA-256 |
|---|---|---|
| `ValhallISC-0.9.1-macos-arm64.dmg` | 27.2 MB | `c99b2ef27beaa865584c2f4137b10f77c7e3fe96e170bd0dedde5588b79f903e` |
| `ValhallISC-0.9.1-macos-x86_64.dmg` | 27.1 MB | `677a9687fcb99f98a2652d6183a225795d3e34bf985492d0296845e4062585f3` |
| `valhallisc-0.9.1-linux-x86_64` | 43.2 MB | `4ad7883757ceeebe0de5aae0141fbaf89452cfab1adb5e1e399ab64517657d91` |
| `valhallisc-0.9.1-linux-aarch64` | 42.4 MB | `e2d6cb0633f8bea2902cebec0368b982e3e98414ced27c6eb335f2062283ff6f` |

Windows (`ValhallISC.exe`, `valhallisc-cli.exe`) is built by the GitHub Actions workflow `windows.yml` on a Windows runner (artifact `ValhallISC-windows-x86_64` of the `v0.9.1` tag run). Before tagging, the same workflow passed on `91b7fab`: build, the unit tests and the GUI tests on Windows, and the `valhallisc-cli.exe --version` / `doctor` smoke test. Real WinFsp mounts of the packaged `.exe` are still to be validated by hand (G10).

## Verification
| Check | macOS arm64 | macOS x86_64 | Linux x86_64 | Linux aarch64 |
|---|---|---|---|---|
| Signed | Developer ID, hardened runtime | Developer ID, hardened runtime | – | – |
| App and DMG notarized and stapled | accepted | accepted | – | – |
| Gatekeeper on the DMG as downloaded (quarantine) and on the app inside | `Notarized Developer ID` | `Notarized Developer ID` | – | – |
| Architecture of the executable | arm64 | x86_64 | x86-64 ELF | aarch64 ELF |
| Real mounts (e2e subset: read, export, 1 MB file, stop, import, `.txt` refused, xattrs, CLI lifecycle, leftovers) | 11/11 (native, FUSE-T) | 11/11 (Rosetta, universal FUSE-T) | – | – |
| Clean Ubuntu 24.04 with only `fuse3` (`scripts/smoke-binary.sh`) | – | – | SMOKE OK (emulated amd64) | SMOKE OK |
