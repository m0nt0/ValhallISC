# Design review 2 (2026-09-29)

**Canvas (Claude Design):** a private canvas with five boards: the current UI in the light theme, the findings, the dark theme, menus/icons/dialogs, and the proposed Profiles window.

Screenshots come from `scripts/screenshots.py <dir> [light|dark]`, which now captures natively on macOS (AppKit `cacheDisplayInRect` on the window's frame view: no screen-recording permission) and forces the appearance.

All findings were approved, with one change: the UI says **Mount / Unmount / Mounted** everywhere, not Connect / Disconnect / Connected.

| # | Finding | Status |
|---|---|---|
| 1 | With the profile mounted or mounting, the password field looked editable (on macOS a disabled field draws its hint in the normal text colour) | **fixed**: while locked, the plain field stands in, disabled, with a `••••••••` mask; the mask is never taken for a password. Test added. |
| 2 | The mounted banner was cut off by a long path | **fixed**: two lines, the state in bold ("Mounted · read-only") and the folder, shortened in the middle; the full path and "Unmount to edit or delete this profile" in the tooltip. Test added. |
| 3 | List rows wrapped ("read-only" alone on a third line) | **fixed**: `ProfileItem.row_subtitle`, one short line without the path. Test added. |
| 4 | No primary action in the footer | **fixed**: **Mount** is the default button, rightmost after Revert and Save; it reads **Save & Mount** with unsaved changes. Test connection stays on the left with its result (now ellipsized, full text in the tooltip). Tests added. |
| 5 | Two vocabularies (Connect vs Mounted/Unmount) | **fixed**: Mount, Mounting…, Mounted, Mounted from CLI, Unmount…. `AppController.connect` became `mount`. The CLI keeps `connect` / `disconnect`. |
| 6 | Dark theme: stark white border around the list | **fixed**: no native border; a 1px frame in the window text colour blended 20% into the background. Test updated. |
| 7 | State dots hard to tell apart; the selected row turned its dot black/white | **fixed**: grey idle, amber mounting/unmounting, green mounted (`icons.STATE_DOTS`), kept on the selected row; the tray menu entries carry the same dots. Tests added. |
| 8 | FUSE dialog notes below 4.5:1 contrast | **fixed**: the notes use the text colour blended 35% towards the background instead of the disabled-text colour. |
| 9 | Port 52773 no longer fits everyone (IRIS 2023.2+ has no built-in web server) | **fixed**: after a test that can't reach the server, a hint under Port suggests the web server's port (80, or 443 with HTTPS) and the URL prefix; the mount error says the same. Tests added. |
| 10 | Tray menu: an idle server had no verb | **fixed**: "Dev IRIS — Mount". Test updated. |

**Verified:** unit, GUI and integration tests (503) on macOS; light and dark screenshots of every state checked by eye.
