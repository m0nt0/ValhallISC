# Release 0.9.0 (2026-09-26)

Built with `scripts/release.sh` from the clean commit **0c33bfe** (tag `v0.9.0`). Every artifact reports `valhallisc 0.9.0 (0c33bfe)`. Output: `dist/release/0.9.0/`.

| Artifact | Size | SHA-256 |
|---|---|---|
| `ValhallISC-0.9.0-macos-arm64.dmg` | 27.2 MB | `93b6eac0e193d924cfc4279ed257431c4758dc0134c57cb0b759fbc331e06dca` |
| `ValhallISC-0.9.0-macos-x86_64.dmg` | 27.1 MB | `a5842a9142222bd79e2d223aa145f9eabb5721333e49ec44faf51c5a39db68c4` |
| `valhallisc-0.9.0-linux-x86_64` | 43.2 MB | `1518b3e7172fff76022e7bd6bfd60d49c8e17d60e88a73dc1cf2dbb4613eb6f3` |
| `valhallisc-0.9.0-linux-aarch64` | 42.4 MB | `05b8cefc0313deb556bac176c7f1499bf20a7e159688b63b873ebcfffe2a16fd` |

Windows (`ValhallISC.exe`, `valhallisc-cli.exe`) is built by the GitHub Actions workflow `windows.yml` on a Windows runner.

## Verification
| Check | macOS arm64 | macOS x86_64 | Linux x86_64 | Linux aarch64 |
|---|---|---|---|---|
| Signed | Developer ID, hardened runtime | Developer ID, hardened runtime | – | – |
| App and DMG notarized and stapled | accepted | accepted | – | – |
| Gatekeeper on the DMG as downloaded (quarantine) and on the app inside | `Notarized Developer ID` | `Notarized Developer ID` | – | – |
| Architecture of the executable | arm64 | x86_64 | x86-64 ELF | aarch64 ELF |
| Real mounts (e2e subset: read, export, 1 MB file, stop, import, `.txt` refused, xattrs, CLI lifecycle, leftovers) | 11/11 (native, FUSE-T) | 11/11 (Rosetta, universal FUSE-T) | – | – |
| Clean Ubuntu 24.04 with only `fuse3` (`scripts/smoke-binary.sh`) | – | – | SMOKE OK (emulated amd64) | SMOKE OK |
