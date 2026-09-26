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
| `ValhallISC-0.9.1-windows-x86_64.exe` | 22.3 MB | `8407127cff010b46e56977b156196ef6b66f5296ec6049e2c24927d8440605d7` |
| `valhallisc-cli-0.9.1-windows-x86_64.exe` | 22.3 MB | `05f27d56096e3d2c31b0ed4ea7a18b0cc28f5dd4028d5637c6498718333167e3` |

Windows (`ValhallISC.exe`, `valhallisc-cli.exe`) is built by the GitHub Actions workflow `windows.yml` on a Windows runner (artifact `ValhallISC-windows-x86_64` of the `v0.9.1` tag run). Before tagging, the same workflow passed on `91b7fab`: build, the unit tests and the GUI tests on Windows, and the `valhallisc-cli.exe --version` / `doctor` smoke test. The packaged `.exe` pair was then tested by hand on Windows with WinFsp (Profiles window, mount, copy out and in): all working, so G10 has passed. The Windows files are published as `ValhallISC-0.9.1-windows-x86_64.exe` and `valhallisc-cli-0.9.1-windows-x86_64.exe`.

Published as the GitHub release https://github.com/m0nt0/ValhallISC/releases/tag/v0.9.1 with all six files and `SHA256SUMS`; the assets were downloaded again and match the checksums.

## Verification
| Check | macOS arm64 | macOS x86_64 | Linux x86_64 | Linux aarch64 |
|---|---|---|---|---|
| Signed | Developer ID, hardened runtime | Developer ID, hardened runtime | – | – |
| App and DMG notarized and stapled | accepted | accepted | – | – |
| Gatekeeper on the DMG as downloaded (quarantine) and on the app inside | `Notarized Developer ID` | `Notarized Developer ID` | – | – |
| Architecture of the executable | arm64 | x86_64 | x86-64 ELF | aarch64 ELF |
| Real mounts (e2e subset: read, export, 1 MB file, stop, import, `.txt` refused, xattrs, CLI lifecycle, leftovers) | 11/11 (native, FUSE-T) | 11/11 (Rosetta, universal FUSE-T) | – | – |
| Clean Ubuntu 24.04 with only `fuse3` (`scripts/smoke-binary.sh`) | – | – | SMOKE OK (emulated amd64) | SMOKE OK |
