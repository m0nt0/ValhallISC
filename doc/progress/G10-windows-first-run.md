# Windows: first run (2026-09-25)

**Environment:** Windows (64-bit, AMD64), Python 3.12.7, wxPython 4.3.1 msw, WinFsp (FUSE 2.8 API, `C:\Program Files (x86)\WinFsp\bin\winfsp-x64.dll`), keyring `WinVaultKeyring`. Run from source (`python -m irisfs gui`) against the Docker IRIS on the Mac, over Wi-Fi.

## Verified by the user
| Item | Evidence (console log) |
|---|---|
| WinFsp found through the registry (`fuselib._windows_winfsp`) | `doctor`: `fuse: WinFsp at …winfsp-x64.dll`, version 2.8 |
| Tray in split mode (left: servers, right: Profiles…/Quit) | `tray click mode: split` |
| Test connection | OK |
| Mount at a missing folder (WinFsp creates it) | `tet mounted`, `test1 mounted` |
| Unmount (the worker exits; WinFsp removes the mount) | `worker exited (0), was unmounting` |
| Wrong password | dialog "The server rejected the user name or password", worker exit 3 |
| Two profiles at once, and Quit unmounts both | `test1`/`test2 mounted` → both `exited (0)` → `exiting` |
| Import by copying an XML file in with Explorer | `notification: test1: imported: Demo.FinderHello.cls` |

## Bugs found and fixed
1. **The mount folder's parent wasn't created on Windows,** so the default `~\ValhallISC\<name>` failed with "Parent folder … does not exist". Now the parent is created, and the folder itself is left missing for WinFsp (`AppController._prepare_folder`, unit test).
2. **The error dialog crashed** with "wrapped C/C++ object of type ProfilesFrame has been deleted" after the Profiles window was closed. The prompter now ignores a destroyed parent (GUI regression test).
3. **Cosmetic:** `'SetFocus' failed with error 0x57` after **+**. The focus call is now deferred until the window is shown.

## Notes
- Copying files over Remote Desktop produced **zero-filled `.py` files** (`SyntaxError: source code string cannot contain null bytes`). Transfer a zip (`git archive`) instead, and check with `Get-FileHash`.
- Still to do on Windows: the full checklist from Phase 10 (Explorer quirks such as `desktop.ini`/`Thumbs.db`, busy unmount, drive-letter mount points, read-only mount), and running the **unit test suite** on Windows.
