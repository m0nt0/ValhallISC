# Release 0.9.2 "4dava" (2026-09-28)

Fast browsing of large servers: folders are listed one level at a time (ADR-014), and classes in deployed
mode and default Studio projects are shown read-only instead of failing (ADR-015).

Measured on a large production-size IRIS server (IRIS 2024.1.1, one namespace: 831 entries at the root, 30 package
mappings and 287 routine mappings, one package with 39 145 classes):

| Build | First listing of the test namespace | Requests to IRIS | `stat` of the namespace folder while Finder shows it |
|---|---|---|---|
| 0.9.1 | fails: `docnames/*` times out after 30 s, retried | 1 (too big) | - |
| 0.9.2 first cut (`fedd04d`) | 23.0 s | - | ~1.1 s every ~11 s |
| stale-while-revalidate, parallel lookups (`c878e51`) | 6.4 s | 264 | under 0.1 s |
| `TOP 50` sampling (`0383caa`) | 5.2 s | 265 | under 0.1 s |
| mappings read in %SYS (`e6199e0`, released) | 0.98 s | 5 | under 0.2 s |

Opening a package folder of 185 classes takes about 130 ms; a background reload of the test namespace takes about 0.6 s.

Built with `scripts/release.sh` from the clean commit **3127c7e** (tag `v0.9.2`). Every artifact reports `valhallisc 0.9.2 (3127c7e)`. Output: `dist/release/0.9.2/`.

| Artifact | Size | SHA-256 |
|---|---|---|
| `ValhallISC-0.9.2-macos-arm64.dmg` | 27.2 MB | `66d748829a6aadf858effd93594f510232f153ed850adf8b93768691dd307b05` |
| `ValhallISC-0.9.2-macos-x86_64.dmg` | 27.1 MB | `0e992b2b49dd009994dee5d81f5f8929bc376037639190f4c87fe17db0e5df53` |
| `valhallisc-0.9.2-linux-x86_64` | 43.2 MB | `3faac714e7d3bab6ab26198c0f963d273dd43ce7d0cf28cda478690d92df7f32` |
| `valhallisc-0.9.2-linux-aarch64` | 42.4 MB | `07123626602fdc3c00a6daaf22608c2d318cf31cef119268cc664f9225d05790` |
| `ValhallISC-0.9.2-windows-x86_64.exe` | 22.3 MB | `9d4c15541a3b24f4d9372839aebb6251295768cb4dfe5998e4a046ce2e219a35` |
| `valhallisc-cli-0.9.2-windows-x86_64.exe` | 22.3 MB | `e13635d5afba5238714bf4e107fffe2c849c85965c8f85767f7a8a02d33739c2` |

Windows (`ValhallISC.exe`, `valhallisc-cli.exe`) is built by the GitHub Actions workflow `windows.yml` from the `v0.9.2` tag (artifact `ValhallISC-windows-x86_64`): build, unit and GUI tests on Windows, and the `valhallisc-cli.exe --version` / `doctor` smoke test. On the tag the unit tests first failed once: the test fake gave a re-imported document the same millisecond timestamp (the Windows clock advances in ~15 ms steps). The job was re-run on the same commit and passed; the fake now issues strictly increasing timestamps (commit after the tag).

Published as the GitHub release https://github.com/m0nt0/ValhallISC/releases/tag/v0.9.2 with all six files and `SHA256SUMS`.

## Verification
| Check | macOS arm64 | macOS x86_64 | Linux x86_64 | Linux aarch64 |
|---|---|---|---|---|
| Signed | Developer ID, hardened runtime | Developer ID, hardened runtime | – | – |
| App and DMG notarized and stapled | accepted | accepted | – | – |
| Gatekeeper on the DMG as downloaded (quarantine) and on the app inside | `Notarized Developer ID` | `Notarized Developer ID` | – | – |
| Architecture of the executable | arm64 | x86_64 | x86-64 ELF | aarch64 ELF |
| Real mounts (e2e subset: read, export, 1 MB file, stop, import, `.txt` refused, xattrs, CLI lifecycle, leftovers) | 11/11 (native, FUSE-T) | 11/11 (Rosetta, universal FUSE-T) | – | – |
| Clean Ubuntu 24.04 with only `fuse3` (`scripts/smoke-binary.sh`) | – | – | SMOKE OK (emulated amd64) | SMOKE OK |
