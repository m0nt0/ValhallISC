# Release 0.1.0 (2026-09-26)

Built with `scripts/release.sh` from the clean commit **78605c9**. Every artifact reports `valhallisc 0.1.0 (78605c9)`. Output: `dist/release/0.1.0/`.

| Artifact | Size | SHA-256 |
|---|---|---|
| `ValhallISC-0.1.0-macos-arm64.dmg` | 27.2 MB | `8896c75c008505bc0bd02221b3b76b956a2e8dae7a1096d4edbf06982e83fe53` |
| `ValhallISC-0.1.0-macos-x86_64.dmg` | 27.1 MB | `88d4054d2a37c6e5fb3cb7a9219c1a16f679a27a9703c541dab42bed8f2ff4f8` |
| `valhallisc-0.1.0-linux-x86_64` | 43.2 MB | `cecd481a6bcb06a39b7d5f7a468a985c5a02dc95404bbf52ef20892f110ebc8d` |
| `valhallisc-0.1.0-linux-aarch64` | 42.4 MB | `620fafa09f341046ac5d23576d57b3f8dd2a0281665e13b921205cbad7545054` |

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
