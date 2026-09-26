# Release 0.1.0: macOS (2026-09-26)

**Artifact:** `dist/ValhallISC-0.1.0-macos-arm64.dmg` (27.2 MB, Apple silicon)
SHA-256 `185a300460d604902b9fe0da9072f5feaf75da5f9796cc9aafad09ccac11e25f`

**Contents:** `ValhallISC.app`, `valhallisc` (the CLI wrapper), `LICENSE.txt`, an Applications shortcut.

| Check | Result |
|---|---|
| Signed with | Developer ID, hardened runtime, secure timestamp |
| App notarization | Accepted; ticket stapled (`stapler validate`: worked) |
| DMG notarization | Accepted; ticket stapled |
| Gatekeeper, DMG **as downloaded** (quarantine attribute set) | `accepted, source=Notarized Developer ID` |
| Gatekeeper, the app inside the mounted DMG | `accepted, source=Notarized Developer ID` |
| Notarized app as the mount worker, on FUSE-T (e2e subset: read, export, 1 MB file, stop, import, `.txt` refused, xattrs, CLI lifecycle, leftovers) | 11/11 passed |

Built with: `SIGN_IDENTITY=<Developer ID> NOTARY_PROFILE=valhallisc-notary scripts/sign-macos.sh`
