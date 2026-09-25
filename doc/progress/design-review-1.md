# Design review 1 (2026-09-25)

**Canvas (Claude Design):** a private canvas with four boards: the annotated current UI, the proposed Profiles window, the proposed menu bar menu, and "Already fixed".

Screenshots come from `scripts/screenshots.py`, run under Xvfb in the Linux test image with ImageMagick. `wx.ScreenDC` returns stale pixels under Xvfb, which is why ImageMagick does the capture.

| # | Finding | Status |
|---|---|---|
| 1 | Expanding Advanced pushed options under the button row | **fixed**: the fields scroll in their own `ScrolledPanel` and the button row is fixed. (Growing the window instead proved fragile, because GTK resizes asynchronously.) GUI regression test added. |
| 2 | Errors in one block, far from the field (and overlapping Advanced) | **fixed**: each field has its own error line. Test added. |
| 3 | Delete showed a red ✕ (GTK stock art), not a trash bin | **fixed**: own SVG + and trash glyphs in the system text colour. Test added. |
| 4 | List rows show only a name and ○/● | proposed (two-line rows: host or mount state); waiting for approval |
| 5 | A mounted profile is a dead end (everything greyed) | proposed (banner with Open folder and Unmount); waiting for approval |
| 6 | "(unchanged)" looked like a value | **fixed**: "Saved — type to replace" |
| 7 | Port spinner ± is useless | **fixed**: plain text field, validated as a number. Test added. |
| 8 | One flat column, no grouping | proposed (Server / Sign in / Mount groups, Options tab); waiting for approval |
| 9 | Test connection result shown in a modal | **fixed**: inline ✓/✗ line next to the button (full text in the tooltip) |

**Icon:** the tray glyph lines are much thicker, as the user asked (`scripts/make_icons.py`, `MaxFilter(31)`).
