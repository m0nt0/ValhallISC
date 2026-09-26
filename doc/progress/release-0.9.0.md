# Release 0.9.0 (2026-09-26)

Built with `scripts/release.sh` from the clean commit **5477f0a** (tag `v0.9.0`). Every artifact reports `valhallisc 0.9.0 (5477f0a)`. Output: `dist/release/0.9.0/`.

| Artifact | Size | SHA-256 |
|---|---|---|
| `ValhallISC-0.9.0-macos-arm64.dmg` | 27.2 MB | `2a5a532760e1db834d015b070bd79b7112b94e0322a5a17fe9abd94a37a6b1ba` |
| `ValhallISC-0.9.0-macos-x86_64.dmg` | 27.1 MB | `ffcc23d7eef7cc04bd83a2880369432e63d6453b97e251105c37b2d8100ed366` |
| `valhallisc-0.9.0-linux-x86_64` | 43.2 MB | `0bb1d65f231a45700c3e072b1567549e13d70569ff7e6cd810be1739c6e784b8` |
| `valhallisc-0.9.0-linux-aarch64` | 42.4 MB | `6b83135370f472051609c8242c0750ccdaba9984ed339b7ed4fd329a70326c5a` |

Windows (`ValhallISC.exe`, `valhallisc-cli.exe`) is built by the GitHub Actions workflow `windows.yml` on a Windows runner (artifact `ValhallISC-windows-x86_64` of the `v0.9.0` tag run). Before tagging, the same workflow passed on `5477f0a`: build, the 372 unit tests on Windows (the first CI runs found `os.O_ACCMODE` missing on Windows, which broke every open() on a WinFsp mount, plus POSIX-only assumptions in tests; fixed), and the `valhallisc-cli.exe --version` / `doctor` smoke test. Real WinFsp mounts of the packaged `.exe` are still to be validated by hand (G10).

## Verification
| Check | macOS arm64 | macOS x86_64 | Linux x86_64 | Linux aarch64 |
|---|---|---|---|---|
| Signed | Developer ID, hardened runtime | Developer ID, hardened runtime | – | – |
| App and DMG notarized and stapled | accepted | accepted | – | – |
| Gatekeeper on the DMG as downloaded (quarantine) and on the app inside | `Notarized Developer ID` | `Notarized Developer ID` | – | – |
| Architecture of the executable | arm64 | x86_64 | x86-64 ELF | aarch64 ELF |
| Real mounts (e2e subset: read, export, 1 MB file, stop, import, `.txt` refused, xattrs, CLI lifecycle, leftovers) | 11/11 (native, FUSE-T) | 11/11 (Rosetta, universal FUSE-T) | – | – |
| Clean Ubuntu 24.04 with only `fuse3` (`scripts/smoke-binary.sh`) | – | – | SMOKE OK (emulated amd64) | SMOKE OK |
