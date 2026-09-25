# Architecture decision records

## ADR-001: IRIS test image and web server (S1, 2026-09-25)
- **Context:** we needed to know whether current Community containers still serve the Atelier API on 52773.
- **Decision:** pin `containers.intersystems.com/intersystems/iris-community:2026.1` (build 2026.1.0.234). Its Private Web Server serves `/api/atelier/` on 52773, so no webgateway sidecar is needed. The Atelier API version is **8**.
- **Details:**
  - The image has no `curl`, so the healthcheck uses the image's `python3`.
  - `--password-file` is consumed at startup (the file is renamed to `.done`). The test passwords are therefore set when the image is built, in `seed.script` (`ChangePassword=0`, `PasswordNeverExpires=1`).
  - `$SYSTEM.OBJ.LoadDir` loads only XML files by default. Seeding uses `ImportDir(dir, "*.cls;*.mac;*.inc", ...)`.
  - The seeded namespaces are `USER` and `TESTNS`. The seeded users are `_SYSTEM`/`irisfs-test` and the read-only user `irisfs_ro`/`irisfs-ro-test`.

## ADR-002: XML export and import endpoints (S2)
- **Decision:**
  - **Export:** `POST /api/atelier/v8/{ns}/action/xml/export`. The body is a JSON array of doc names (wildcards allowed). The response `result.content` is an array of lines. It wraps `$SYSTEM.OBJ.ExportToStream`, and its output is **byte-identical** to `$SYSTEM.OBJ.Export` apart from the `ts` attribute. We rebuild the file as `"\n".join(lines) + "\n"`, UTF-8.
  - **Import:** `POST /api/atelier/v8/{ns}/action/xml/load?flags=ck`. The body is `[{"file": "<name>", "content": [lines]}]`. It wraps `$SYSTEM.OBJ.LoadStream`. The response is per file: `{"file", "imported": [names], "status": "<error text or empty>"}`.
- **Consequences:**
  - The `cvt/*` conversion endpoints aren't needed. Every export and every import is a single request.
  - Compile errors **do not** fail the request. The item is imported, and `status` contains the compiler errors, so we report them as `compile_errors`.
  - Malformed or non-IRIS XML is rejected by IRIS with nothing imported: `status` contains `ERROR #6301 ... not a IRIS exported file`.
  - Routine names come back with an upper-case extension (`DEMORTN.MAC`), so we normalise the extension to lower case.
  - Timings on the local container: export about 2–15 ms, load about 5–70 ms.
  - Session reuse works through the `CSPSESSIONID-*` cookie. 40 calls without cookie reuse didn't fail on 2026.1, but we reuse the session anyway.

## ADR-003: "System" item filter (S1/S2)
- **Context:** `docnames` also returns about 5 000 library items that are mapped into every namespace: `IRISLIB`, `ENSLIB`, `IRISSYS`, `CSPX.*`, `INFORMATION.SCHEMA.*`, and `@OTHER` items such as `HIPAA_*.X12` and Ens BPL/DTL.
- **Decision:** when `show_system` is off, show a doc only if all of these hold:
  - its `db` is the namespace's **default** database (from `GET /v8/{ns}` → `db[].default`), **or** its `db` is not flagged `dbsys` and doesn't start with `@`;
  - `upd` is true, which hides Ens-generated read-only routines in the user database;
  - its name doesn't start with `%`.
  - `@OTHER` docs are shown unless the name starts with `%`, `Ens`, `EnsLib`, `Ens-` or `HIPAA_`. User lookup tables such as `DemoTable.LUT` are `@OTHER` items.
  - `@FS` items (CSP files) are hidden in v1.
- In `%SYS`, the default database is `IRISSYS`, so non-`%` items there are still shown.

## ADR-004: FUSE binding (S3)
- **Decision:** `mfusepy` 3.1.x, which has a fusepy-compatible API. It supports Linux libfuse3/2, macOS (macFUSE and FUSE-T via `find_library`/`FUSE_LIBRARY_PATH`), and Windows WinFsp (found through the registry). Set `use_ns = True` in `Operations`.
- **Linux (Docker, libfuse 3.14):**
  - Mounting takes about 0.1 s.
  - `cp` out, `cp -p` in, `.txt` create → `EACCES`, and `rm` → `EPERM` all work.
  - A busy unmount (`fusermount3 -u`) fails with "Device or resource busy". Force unmount will use `fusermount3 -uz` (lazy).
  - On SIGTERM, libfuse unmounts cleanly, but `FUSE()` then raises `RuntimeError(8)`, which the worker must treat as a normal exit.
  - The log line "Ignoring invalid max threads value" is harmless.
- **macOS 26.6 (macFUSE 5.3.3 from MacPorts):**
  - The library is `/opt/local/lib/libfuse.2.dylib`. `ctypes.util.find_library` does **not** search `/opt/local/lib`, so `fuselib.py` must search `/usr/local/lib` (macFUSE pkg, FUSE-T), `/opt/local/lib` (MacPorts) and `/opt/homebrew/lib`, then set `FUSE_LIBRARY_PATH` before importing mfusepy.
  - Mounting takes about 1.9 s. `cp` in and out, `.txt` → `EACCES`, `rm` → `EPERM` and `xattr -w` all work.
  - A busy `umount` fails with "Resource busy -- try 'diskutil unmount'". Force unmount will use `diskutil unmount force`.
  - SIGTERM → clean exit 0 and unmounted.

## ADR-005: Tray behaviour and icon on macOS (S4)
- **Click events:** on macOS 26 with wxPython 4.3.1 (wxWidgets 3.3.3), **no** `EVT_TASKBAR_LEFT/RIGHT_*` events are delivered. Every click, left or right, only calls `CreatePopupMenu()`. We verified this with a human clicking.
  - **Decision:** macOS always uses the **combined** menu (UI-10): profiles, a separator, Profiles…, Quit. Linux and Windows use the split left/right menus where the platform delivers the events. An override exists.
- **Main loop:** a tray-only app exits immediately unless it has a top-level window. `SetExitOnFrameDelete(False)` isn't enough.
  - **Decision:** create one hidden `wx.Frame` that is never shown and lives for the whole app.
- **Icon colour:** the user reported the icon was black on a black menu bar. macOS 26's menu bar is transparent, and its foreground colour depends on the wallpaper, so choosing icons by dark mode is unreliable. wx has no API for "template images".
  - **Decision:** draw a monochrome glyph (black plus alpha) at runtime at 1× and 2× in a `wx.BitmapBundle`. After every `SetIcon`, use PyObjC (`pyobjc-framework-Cocoa`, macOS-only dependency) to find the `NSStatusBarButton` in the app's `NSStatusBarWindow` and call `image().setTemplate_(True)`. macOS then tints the icon for any menu bar background.
  - The icon has an active variant (filled) and an inactive one (outline).
- **Linux and Windows:** use coloured icons, since there's no template concept. Choose the colour from `wx.SystemSettings.GetAppearance().IsDark()`, and refresh on `EVT_SYS_COLOUR_CHANGED`.
