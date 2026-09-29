# Release 0.9.3 "4DavaColNotch" (2026-09-29)

A regular app on every platform (ADR-016), source control state in file managers (ADR-017), and the Profiles window after design review 2 (`doc/progress/design-review-2.md`).

- **macOS:**
  - Dock icon (no more `LSUIElement`); reopening the app shows the Profiles window;
  - a menu bar with Profiles… (Cmd-,) and Quit (Cmd-Q).
- **Every platform:**
  - a second launch hands over to the running app;
  - windows carry the logo.
- **Windows:** a per-user Inno Setup installer.
- **Source control:**
  - Finder tags on macOS;
  - `user.xdg.*` attributes on Linux;
  - `_CHECKED_OUT.txt` on Windows and Linux;
  - documents that are not editable are read-only.
- **Profiles window:**
  - Mount is the default button ("Save & Mount" with unsaved changes);
  - one vocabulary: mount / unmount / mounted;
  - two-line banner and one-line list rows;
  - state dots also in the tray menu;
  - a port hint for IRIS 2023.2 and later;
  - dark theme and contrast fixes.

Built with `scripts/release.sh` from the clean commit **52647fb** (tag `v0.9.3`). Output: `dist/release/0.9.3/`. A first build from `ffe0fe3` (before design review 2) was never published; the tag was moved and every artifact rebuilt.

| Artifact | Size | SHA-256 |
|---|---|---|
| `ValhallISC-0.9.3-macos-arm64.dmg` | 27.3 MB | `bf92c9848361ff166e23fb775c519dbf37c6ad854621432bb52aaa62db4759ab` |
| `ValhallISC-0.9.3-macos-x86_64.dmg` | 27.2 MB | `bf4237c03b09fa4c1256b97a4bef0e69b5454244904df1069047a3a0350b616a` |
| `valhallisc-0.9.3-linux-x86_64` | 43.3 MB | `98af28df447bd33a6dbc5475b73ecdec1ffd0b2099d0fb707e9db91967f09856` |
| `valhallisc-0.9.3-linux-aarch64` | 42.5 MB | `5a843682cd200c98c37dca6f8bffdd9477108ec37fb8b30cb574fb6205431e71` |
| `ValhallISC-0.9.3-windows-x86_64-setup.exe` | 46.4 MB | `bbbb55434fd304cfaaa79d6f760b1892d851ee64c08fb2876fb67caf77ff55a7` |
| `ValhallISC-0.9.3-windows-x86_64.exe` | 22.4 MB | `6445fb15009de98db0dee3aca275028a12350cb1f99493cbd8188a5e44b174ba` |
| `valhallisc-cli-0.9.3-windows-x86_64.exe` | 22.4 MB | `bc330717fad33018046b107ecd28f6f14054d92e743c34607fe609646a6cd8f1` |

The Windows files come from the GitHub Actions run of `windows.yml` on `52647fb` (artifact `ValhallISC-windows-x86_64`). That run:
- built the files;
- ran the unit and GUI tests on Windows;
- ran the `valhallisc-cli.exe --version` / `doctor` smoke test;
- installed the setup silently, checked the files, the Installed apps entry and the Start menu entry, then uninstalled it and checked that nothing was left.

Published as the GitHub release https://github.com/m0nt0/ValhallISC/releases/tag/v0.9.3 with all seven files and `SHA256SUMS`. The release title was corrected after creation: its curly quotes had been mangled when the command was pasted.

## Verification
| Check | Result |
|---|---|
| macOS DMGs signed, notarized and stapled (app and DMG) | accepted, both architectures |
| Gatekeeper on the DMGs as downloaded from the release (quarantine set) | `Notarized Developer ID`, both |
| Published `SHA256SUMS` identical to the local one; downloaded DMGs match it | OK |
| Clean Ubuntu 24.04 with only `fuse3` (`scripts/smoke-binary.sh`) | SMOKE OK on x86_64 (emulated) and aarch64 |
| Windows CI (build, tests, installer install/uninstall) | green |
| Unit, GUI and integration tests on macOS | 503 passed |
| Unit and GUI tests on Linux (Docker, Xvfb) | 417 + 26 passed |
| Real mounts from the built DMGs (e2e subset, including source control) | 12/12 on the `ffe0fe3` build; not re-run on `52647fb`, which changes only the GUI |

Not verified:
- macOS with macFUSE (extended attributes);
- the appearance in Windows Explorer;
- a physical Cmd-Q keypress (checked in-process instead);
- the Dock on a second Mac.
