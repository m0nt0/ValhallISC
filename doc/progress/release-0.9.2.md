# Release 0.9.2 "4dava" (2026-09-28)

Fast browsing of large servers: folders are listed one level at a time (ADR-014), and classes in deployed
mode and default Studio projects are shown read-only instead of failing (ADR-015).

Measured on a large production-size IRIS server (IRIS 2024.1.1, one namespace: 831 entries at the root, 30 package
mappings and 287 routine mappings, one package with 39 145 classes):

| Build | First listing of the test namespace | Requests to IRIS | `stat` of the namespace folder while Finder shows it |
|---|---|---|---|
| 0.9.1 | fails: `docnames/*` times out after 30 s, retried | 1 (too big) | - |
| 0.9.2 first cut (`fedd04d`) | 23.0 s | - | ~1.1 s every ~11 s |
| stale-while-revalidate, parallel lookups (`a674f1f`) | 6.4 s | 264 | under 0.1 s |
| `TOP 50` sampling (`8e27483`) | 5.2 s | 265 | under 0.1 s |
| mappings read in %SYS (`e0a690e`, released) | 0.98 s | 5 | under 0.2 s |

Opening a package folder of 185 classes takes about 130 ms; a background reload of the test namespace takes about 0.6 s.

Built with `scripts/release.sh` from the clean commit **82253fe** (tag `v0.9.2`). Every artifact reports `valhallisc 0.9.2 (82253fe)`. Output: `dist/release/0.9.2/`.

| Artifact | Size | SHA-256 |
|---|---|---|
| `ValhallISC-0.9.2-macos-arm64.dmg` | 27.2 MB | `1090cc6103c4454179301fa8133884a8aa33eea57fba5d745c4b4dc0e50c3313` |
| `ValhallISC-0.9.2-macos-x86_64.dmg` | 27.1 MB | `fe3e23b6c7fca75295c41184d3df61e12b62dbfb13ca9891bdd28f6bf343b166` |
| `valhallisc-0.9.2-linux-x86_64` | 43.2 MB | `524f881541fcc8062fad841517e9b66912fade4135bdff9c6083632b7e414ac5` |
| `valhallisc-0.9.2-linux-aarch64` | 42.4 MB | `940bf365bc309da680446bd33831b4e364d867ac6b43cc051fd19505efd6043f` |
| `ValhallISC-0.9.2-windows-x86_64.exe` | 22.3 MB | `7dfad8013ca618589b74443cccf3a407b5c67322761646b766d163011e62edb3` |
| `valhallisc-cli-0.9.2-windows-x86_64.exe` | 22.3 MB | `dc7f3e048f2622d1739d1a5fe053542df917c0eaebfeee685b3a657b2a9a3ab9` |

Windows (`ValhallISC.exe`, `valhallisc-cli.exe`) is built by the GitHub Actions workflow `windows.yml` from the `v0.9.2` tag (artifact `ValhallISC-windows-x86_64`): build, unit and GUI tests on Windows, and the `valhallisc-cli.exe --version` / `doctor` smoke test.

The history was rewritten after the first publication to remove environment-specific names from `doc/DECISIONS.md`; the tag moved from `3127c7e` to `82253fe` (same code) and every artifact was rebuilt from it and re-verified. A first Windows run on the old tag had failed once on a flaky unit test (the test fake gave a re-imported document the same millisecond timestamp, since the Windows clock advances in ~15 ms steps); the fake now issues strictly increasing timestamps.

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
