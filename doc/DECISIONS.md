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

## ADR-003: "System" item filter (S1/S2, revised in Phase 4)
- **Context:** `docnames` also returns about 5 000 library items that are mapped into every namespace: `IRISLIB`, `ENSLIB`, `IRISSYS`, `CSPX.*`, `INFORMATION.SCHEMA.*`, and `@OTHER` items such as `HIPAA_*.X12` and Ens BPL/DTL.
- **Decision:** when `show_system` is off, hide a doc if any of these hold:
  - it is a CSP/`@FS` file (always hidden in v1);
  - it is generated (`gen`);
  - its name matches `^(%|Ens[A-Z.-]|Ensemble|Enseb|HIPAA_)`, the prefixes InterSystems reserves;
  - it comes from a `dbsys` database that isn't the namespace's default database (`GET /v8/{ns}` → `db[].default/dbsys`), or from an `@` pseudo-database other than `@OTHER`.
- `@OTHER` docs that pass the name check stay visible; user lookup tables such as `DemoTable.LUT` are `@OTHER` items. In `%SYS`, the default database is `IRISSYS`, so non-`%` items there stay visible.
- **Correction (Phase 4):** `upd` means *up to date* (source compiled), **not** "updatable". A seed routine that failed to compile had `upd:false`, and becomes `true` once it compiles. The first version of this rule hid `upd:false` items, which would have hidden any user routine with a compile error. `upd` is no longer used. Ens-generated routines in the user database (`EnsJob.mac`, `EnsUtil.mac`, …) are hidden by the name rule instead.

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

## ADR-006: Size prefetch on readdir (Phase 5 performance gate)
- **Context:** `st_size` must be exact for copy tools, and it's only known after exporting the document. `readdir` returns names only, so even plain `ls -R` stats every entry. The first performance run, over 2 000 classes in `PERFNS` on macOS, measured `ls -R` at 7.7 s (target under 5 s) and `ls -lR` from cold at 8.1 s: sequential exports at about 4 ms each.
- **Decision:** `readdir` of a package folder submits background exports of its files to a pool of 4 threads, matching the client's concurrency limit. The content cache's single-flight lock makes the following `stat`s wait for, or reuse, the running export. Files whose size is already known or in flight are skipped. `Options.prefetch_workers=0` disables prefetch.
- **Result:** `ls -R` 1.26 s, `ls -lR` from cold 1.47 s, warm 0.15 s on macOS; 1.22 s, 1.43 s and 0.23 s on Linux.
- **Rejected:**
  - `direct_io` with estimated sizes: it breaks tools that trust `st_size`.
  - Exporting several items in one request and splitting the result: it depends on IRIS's exact formatting of multi-item exports.

## ADR-007: macOS copy-engine compatibility (Phase 6)
- **Context:** Finder, `ditto` and editors don't write files the way `cp` does.
- **Decisions:**
  - **Empty placeholder:** an empty file closed after `create` is kept for 60 s and never imported. Finder creates the file, closes it, then reopens it to write.
  - **Hidden temporary files:** names starting with `.` (other than `*.xml`) are in-memory scratch files. Renaming one to `*.xml` imports it; `ditto` and Finder use `.BC.T_*` then rename, and editors do atomic saves this way.
  - **Ghost entry reopened for writing:** it becomes a new write buffer, so copying the same file in twice works.
  - **macFUSE options:** `local` (sidebar), `noappledouble` (no `._` files), `volname` = the mount folder's name. **Not** `noapplexattr`: the kernel would refuse `com.apple.*` xattrs with EPERM, and Finder aborts copies with a permission error. xattrs are accepted as no-ops by `VirtualFS`.

## ADR-008: Windows (WinFsp) behaviour confirmed (first Windows run)
- The [VERIFY] items for Windows are confirmed on Windows (64-bit) with WinFsp's FUSE 2.8 API:
  - WinFsp is found through the registry: `HKLM\SOFTWARE\WinFsp\InstallDir`, then `bin\winfsp-{arch}.dll`.
  - The mount options `uid=-1, gid=-1, FileSystemName=IRISFS, volname=…` work.
  - A folder mount point must not exist when mounting. The controller creates only its parent.
  - Stopping the worker by ending its process (`os._exit`) cleanly removes the mount.
- Import by copying through Explorer works with the same `VirtualFS` code, so no Windows-specific write handling was needed.

## ADR-009: Packaging (Phase 9)
- **macOS:** only `ValhallISC.app`, onedir inside the bundle, with `LSUIElement` (no Dock icon) and the icon from the logo, zipped with `ditto`. The CLI is `dist/valhallisc`, a 400-byte shell wrapper that runs `ValhallISC.app/Contents/MacOS/ValhallISC` (next to it, or in `/Applications`).
  - **Why not a onefile CLI:** a PyInstaller onefile binary extracts itself to a *new* temp folder on every launch, and macOS re-scans the extracted libraries. We measured `--version` at **10–103 s**, against 0.15–0.7 s for the bundle executable. The bundle executable also serves as the mount worker (`sys.executable worker`).
  - **Name clash:** on case-insensitive APFS, `dist/ValhallISC` (the onedir folder) and `dist/valhallisc` are the same path. The onedir folder is now named `ValhallISC-onedir` and removed after bundling.
  - The very first build took 53 minutes of mostly idle waiting while PyInstaller's binary cache filled. Later builds take about 2 minutes.
- **Linux:** one onefile binary `valhallisc-linux-<arch>`, built in the `linux-test` image (Ubuntu 24.04 with the distro's wxPython; it needs `binutils` and `libpython3.12t64`).
  - Runtime needs glibc 2.39 or newer (Ubuntu 24.04 / Debian 13+) and `fuse3`, plus GTK3 for the tray GUI; desktop distros have it. The CLI and worker run without GTK.
  - The build host's architecture decides the binary's: aarch64 on Apple Silicon. For x86_64, build on an x86_64 Docker host, or with `docker buildx --platform linux/amd64` (slow, emulated).
- **Windows:** `ValhallISC.exe` (onefile, windowed: tray app and worker) plus `valhallisc-cli.exe` (onefile, console: `doctor`, `mount`, …). The names differ because NTFS is case-insensitive too.
  - **Watch items for the Windows test:**
    - onefile startup time with Defender scanning (every mount spawns a worker);
    - whether killing a onefile worker's bootloader also stops its Python child.

    If either is a problem, switch Windows to onedir plus a zip.
- **Windowed builds:** PyInstaller may set `sys.stdout`, `sys.stderr` and `sys.stdin` to `None`. Logging skips the stderr handler, and the worker works on file descriptors 0 and 1 directly.

## ADR-010: UI after design review 1 (deviations from the original spec)
- **Context:** a design review in Claude Design (`doc/progress/design-review-1.md`) found layout bugs and usability gaps. The user approved the proposed redesign.
- **Decisions and deviations from `doc/idea`:**
  - **"+" and trash buttons:** the spec placed them "on the top". They now sit **under the profile list** (the macOS convention for list editing), drawn as our own SVG glyphs so they look the same on every platform.
  - **Clicking an active server:** the spec says clicking it asks to unmount. In the menu a mounted server is now a **submenu** (its location, Open folder, Unmount…), because native menus can't hold buttons. **Unmount… still asks for confirmation**, as the spec requires. Clicking an idle server still mounts it directly.
  - **Active marker:** state words ("Mounted", "Connecting…", "Connected from CLI") replace the bare ✓. A header line shows "N of M mounted".
  - **Profile form:** a Connection tab (Server / Sign in / Mount) and an Options tab (URL prefix, system items, compile). Errors appear under their field, and the test result is shown inline. The fields scroll above a fixed footer, because growing the window proved unreliable with GTK's asynchronous resizes.
  - **Read-only form while mounted (spec):** kept, now with a banner offering Open folder and Unmount….
- The macOS combined menu (ADR-005) and the Windows/Linux left/right split are unchanged.

## ADR-011: FUSE is a prerequisite, detected and explained, and never bundled
- **Never bundled:** PyInstaller had pulled macFUSE's `libfuse*.dylib` and `MFMount.framework` into the `.app`, because `mfusepy` loads libfuse at import time. They're now filtered out of every build (`packaging/valhallisc.spec`), and the macOS build script fails if any reappear.
  - macFUSE isn't ours to redistribute.
  - The user-space library must match the installed driver.
  - `fuselib` always loads the installed library by absolute path.
  - `libiconv` stays, because the MacPorts Python itself needs it.
- **Startup check:** `irisfs/mount/fuse_help.py` produces tailored `Advice` (title, intro, commands, links, notes). The logic is pure, with injectable probes, and tested for every branch:
  - **macOS:** a Homebrew command (FUSE-T tap, or the macFUSE cask), a MacPorts command (`port install macfuse`), or download links. Also the case "macFUSE present but not fully installed".
  - **Linux:** `/etc/os-release` `ID`, then `ID_LIKE`, mapped to apt, dnf, pacman, emerge, zypper, apk, xbps, eopkg or NixOS. Unknown distributions get a generic hint and the libfuse link. If libfuse is present but `/dev/fuse` is missing, the advice is `modprobe`.
  - **Windows:** a `winget` command when available, and the WinFsp download page.
- **The tray app** shows a modeless `FuseMissingDialog` (copy the commands, open the links, Check again) at startup and when a mount is attempted. `doctor` and the CLI print the same text.
- **Single instance per settings folder** (`VALHALLISC_CONFIG_DIR`): discovered when the test suite ran next to the user's own running app.

## ADR-012: macOS distribution (signing, hardened runtime, notarization)
- **Channel:** a DMG outside the App Store. The App Store sandbox forbids FUSE and helper processes. This needs a **Developer ID Application** certificate and notarization (`doc/RELEASING.md`).
- **`scripts/sign-macos.sh`:**
  - signs every nested Mach-O file inside-out, then the frameworks, then the app, with `--options runtime --timestamp`;
  - verifies the result;
  - optionally notarizes and staples the app;
  - builds a signed DMG (app, CLI wrapper, `LICENSE.txt`, Applications link), and notarizes and staples it too.
- **Entitlements** (`packaging/entitlements.plist`):
  - `disable-library-validation`, to load the user's macFUSE/FUSE-T, which another team signs;
  - `allow-unsigned-executable-memory`, for ctypes/libffi FUSE callbacks.
- **Verified with an Apple Development identity** (hardened runtime on): the signed app mounts, reads, imports and runs the CLI lifecycle (11 e2e checks with `IRISFS_BIN` = the signed executable). The GUI starts and exits cleanly.
- **Found while signing:** macFUSE's `libswiftCompatibilitySpan.dylib` (from inside `MFMount.framework`) was being bundled. The FUSE filter now also matches source paths.
- **Not yet done:**
  - notarization: waiting for a Developer ID certificate and a notarytool profile;
  - an Intel or universal2 build: the current build is arm64, because the Python is arm64.

## ADR-013: FUSE-T support (verified 2026-09-26)
- **Result:** with FUSE-T 1.2.7 (the NFS backend) the full macOS e2e suite passes (37/37). FUSE-T is preferred when both drivers are installed. The first run found three FUSE-T-specific problems:
  1. **The worker died with SIGPIPE on every unmount** (exit -13).
     - libfuse (and FUSE-T) resets SIGPIPE to the default action at teardown whenever it's `SIG_IGN`. Python sets `SIG_IGN` at startup, but libfuse never installed it itself.
     - FUSE-T then writes to its closed NFS socket.
     - **Fix:** the worker installs a no-op Python handler for SIGPIPE, which the teardown leaves alone. Blocking the signal wasn't enough, because FUSE-T changes the thread signal masks.
  2. **"Not found" persisted after an IRIS outage.**
     - The macOS NFS client kept a failed lookup cached, with no retry even 2 minutes later.
     - **Fix:** mount option `noattrcache` (no measurable cost: `ls -lR` of 2 000 classes takes 1.32 s either way). Namespace folders also carry their listing's change time as mtime (`TreeCache.changed_at`), which moves on content changes and after a failed load, so NFS clients revalidate.
  3. **An xattr test wrote a 64-byte FinderInfo.** It must be exactly 32 bytes. FUSE-T rightly refuses other sizes (ERANGE), while macFUSE had accepted it. This was a test bug.
- **Also:** `location=ValhallISC`, so Finder groups FUSE-T volumes under "ValhallISC" and not "localhost". `VALHALLISC_FUSE_OPTIONS` passes extra mount options, for debugging.

## ADR-014: Folders are listed one level at a time (0.9.2)
- **Context:** up to 0.9.1 the first access to a namespace fetched `docnames/*`: every document of the namespace, including the thousands of mapped library items that the system filter then hides (5 000 entries for an almost empty `USER`), and again whenever the 10 s cache expired. On servers with many classes, opening a namespace was very slow.
- **Decision:** a folder is listed only when it is opened, with the query VS Code's isfs uses: `POST action/query` on `%Library.RoutineMgr_StudioOpenDialog`, one package level per call (`vfs/folders.py`, `AtelierClient.list_folder`). Each folder is cached for `tree_ttl`.
  - **Spec syntax:** `*` for the root, `Demo.Sub/*` for a package. `Demo/Sub/*` returns nothing below the first level.
  - **"Other" documents** (lookup tables, DTL, BPL, HL7 schemas, DFI) are listed only at the root, by full name. The root listing sorts them into their package folders.
  - **System filter (ADR-003) kept:** `SystemFiles` follows `show_system`; generated items are never listed, as before (`docnames` was always called with `generated=0`). The name rule is applied to documents and to packages (with a trailing dot, so it holds for everything inside). The query doesn't report databases, so each folder is listed with `Mapped=1` and `Mapped=0`: entries present only in the first are mapped. If every mapped database of the namespace is a system one they are hidden; otherwise `docnames?filter=<name>.%` (SQL LIKE) tells where they come from, once per mapped package, and sub-packages inherit the answer.
  - **Fallback:** if the server refuses the query (`ServerError`, for example no SQL privilege on the procedure), that namespace goes back to the whole-namespace listing until the next refresh.
  - **Folder mtime (ADR-013):** still one change time per namespace, now moved when any folder's listing changes or recovers after a failed load. A folder's `stat` doesn't load its content unless it was listed before (only then can a client hold a stale copy).
- **Verified:** on the test IRIS the new and the old listing give identical trees for USER, PERFNS and %SYS, with and without system items, and opening a namespace takes 6–44 ms instead of 140–267 ms. A unit test makes the same comparison over the fake (mapped packages from user and system databases, nested "other" documents), and the contract test `test_list_folder_one_level_at_a_time` runs against the fake and the real IRIS. The macOS e2e suite passes, perf test included.
- **After the first test on a large production-size IRIS server (2026-09-28):**
  - an expired listing is served at once and reloaded in background (`Options.background_refresh`): Finder stats an open namespace folder every ~11 s, and each stat waited ~1.1 s for IRIS. Only a folder never loaded (or invalidated by an import) waits;
  - a namespace folder's own `stat` and names that cannot exist (`.DS_Store`, `Icon\r`, ...) ask IRIS nothing;
  - a mapped package's database is read from one sample document (`GET doc`), the lookups run in parallel (the first listing of the namespace took 23 s, mostly sequential lookups), and `docnames?filter=` is no longer used: on that server it timed out after 30 s;
  - a mapped entry is first placed from the namespace's mapping table, read once in %SYS (`Config.MapPackages_List`, `Config.MapRoutines_List`, `vfs/mapping.py`): the most specific package mapping, or for a routine the exact name before the longest wildcard, gives its database. Only what the table can't tell (range patterns, unknown databases, "other" documents), or every entry when the account can't read %SYS, is asked about one by one: a class through the class dictionary's index (`SELECT TOP 1 Name FROM %Dictionary.ClassDefinition WHERE Name %STARTSWITH ?`, constant cost), otherwise a limited one-level walk, then `GET doc` for its database. On that server the per-entry lookups had been 234 requests on the first listing of the namespace, and walking a package of 39 145 classes to find a sample took 4-5 s by itself;
  - every folder load is logged with its duration (INFO when over 1 s); `VALHALLISC_DEBUG=1` logs every operation and request.
- **Rejected:** `docnames` with a `filter` for every folder: the filter is a flat SQL LIKE, so a package's listing would still return everything below it.

## ADR-015: Deployed classes are shown read-only (0.9.2)
- **Context:** a class in deployed mode has no source, and IRIS refuses its export with `#6309`. Default Studio projects (`Default_<user>.prj`) are refused the same way (`#5848`) and are treated alike (`NotExportableError`). Its size was unknown, so `ls -l` printed "Input/output error" for it (for example `%Library.SQLCatalogPriv` in `%SYS` with system items shown).
- **Decision:** the client raises `DeployedError` for `#6309`. The file stays listed as `r--r--r--` with size 0; opening it for reading or writing fails with `EACCES` (reading it as an empty file would let `cp` produce an empty copy). The state is remembered per document timestamp, so the class is not exported again at every `stat`, and a class that gets its source back shows up normally.

## ADR-016: A regular app: Dock icon, reopening, Windows installer (0.9.3)
- **Context:** on a friend's Mac the app "didn't start": it did (the log says so), but a menu-bar-only app (`LSUIElement`) has no Dock icon and no window, and its menu-bar icon can end up hidden behind the notch. Opening it again did nothing either: macOS reactivates the running app, and the app ignored that. On Windows the single `.exe` appeared nowhere (Start menu, Installed apps).
- **Decision:**
  - **macOS:** no more `LSUIElement`: the app is in the Dock and in Cmd-Tab. A click on the Dock icon, or opening the app again from Finder or Launchpad (`wx.App.MacReopenApp`), shows the Profiles window. Cmd-Q and Quit in the Dock arrive as a request to end the session (`EVT_QUERY_END_SESSION`): it is vetoed and the menu's Quit runs (confirmation, unmount, exit). On Windows and Linux that event only comes with a system shutdown, so it is not bound there.
  - **Every platform:** a second launch (the Start menu, the executable, the command line on macOS) no longer shows "already running": it drops `show-profiles.request` in the settings folder and exits with 0; the running app polls for it every second and shows its Profiles window (`gui/instance.py`). A request left over from an earlier run is discarded at startup.
  - **Windows installer** (`packaging/windows/valhallisc.iss`, Inno Setup, built in CI): per-user install without administrator rights in `%LOCALAPPDATA%\Programs\ValhallISC`, a Start menu entry, an optional desktop icon, an entry in Installed apps with the uninstaller, and the running app closed before an update or uninstall. `AppId` is fixed so updates replace the installed version. The CI installs it silently, checks the files, the Installed apps entry and the Start menu entry, then uninstalls it and checks that nothing is left. The single `.exe` files are still published.
- **Verified on macOS** with the built app: `lsappinfo` reports it as a Foreground (Dock) app, `open` on the running app logs the reopen, a second process hands over and exits, and a quit request from AppleScript is vetoed and turns into the confirmation dialog.
