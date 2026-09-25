# Design review 1 (2026-09-25)

**Canvas (Claude Design):** a private canvas with four boards: the annotated current UI, the proposed Profiles window, the proposed menu bar menu, and "Already fixed".

Screenshots come from `scripts/screenshots.py`, run under Xvfb in the Linux test image with ImageMagick. `wx.ScreenDC` returns stale pixels under Xvfb, which is why ImageMagick does the capture.

| # | Finding | Status |
|---|---|---|
| 1 | Expanding Advanced pushed options under the button row | **fixed**: the fields scroll in their own `ScrolledPanel` and the button row is fixed. (Growing the window instead proved fragile, because GTK resizes asynchronously.) GUI regression test added. |
| 2 | Errors in one block, far from the field (and overlapping Advanced) | **fixed**: each field has its own error line. Test added. |
| 3 | Delete showed a red ✕ (GTK stock art), not a trash bin | **fixed**: own SVG + and trash glyphs in the system text colour. Test added. |
| 4 | List rows show only a name and ○/● | **implemented** (approved): `SimpleHtmlListBox` two-line rows with a status dot and host or mount state; the selected row is rendered in the selection text colour |
| 5 | A mounted profile is a dead end (everything greyed) | **implemented**: a soft green banner with Open folder and Unmount… (asks for confirmation) |
| 6 | "(unchanged)" looked like a value | **fixed**: "Saved — type to replace" |
| 7 | Port spinner ± is useless | **fixed**: plain text field, validated as a number. Test added. |
| 8 | One flat column, no grouping | **implemented**: a Connection tab (Server / Sign in / Mount) and an Options tab (URL prefix, system items, compile). An error on the Options tab switches to it. |
| 9 | Test connection result shown in a modal | **fixed**: inline ✓/✗ line next to the button (full text in the tooltip) |

**Icon:** the tray glyph lines are much thicker, as the user asked (`scripts/make_icons.py`, `MaxFilter(31)`).

## Implemented after approval
- **Profiles window:** a full rework following the proposal board. The + and trash buttons sit under the list, the password has a show/hide toggle, and the fields scroll above a fixed footer.
- **Menu bar menu:**
  - a header line "ValhallISC — N of M mounted";
  - state words instead of ✓;
  - a mounted server is a submenu (where it's mounted, Open folder, Unmount…);
  - a connecting server is disabled with "— Connecting…";
  - an idle server mounts when clicked.
- **Controller:** `ProfileItem.status` and `.subtitle`, `AppController.summary()`, and `open_folder()` (Finder, Explorer or xdg-open).
- **Tests:** 18 GUI smoke tests (rows, banner and its actions, tabs, password toggle, menu structure) and 8 new controller tests. Green on macOS and Linux.
