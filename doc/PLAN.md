# IRISFS (ValhallISC): implementation plan

> **Status (2026-09-26):** implemented. The product is named **ValhallISC**; the Python package keeps the working title `irisfs`. Gates G0–G9 and G11 have passed on macOS (macFUSE **and** FUSE-T) and on Linux. Still open:
> - the Windows validation of G10 (the tray app works on Windows from source; the packaged `.exe` is not yet validated);
> - Release 0.9.1: signed and notarized DMGs for Apple silicon and Intel, plus Linux x86_64/aarch64 binaries (`doc/progress/release-0.9.1.md`; 0.9.0 in `release-0.9.0.md`). Windows builds come from GitHub Actions.
>
> Where the implementation deviates from this plan, the reason is in `doc/DECISIONS.md` (ADR-001 … ADR-010) and in the gate reports under `doc/progress/`. For users, see `README.md`, `doc/USAGE.md` and `doc/CLI.md`.

> **Audience:** an AI coding agent (or a developer) who will build the program described in `doc/idea`.
> **Goal:** a tray application (Python + wxPython) that mounts one or more InterSystems IRIS servers as local folders. You can then browse classes, routines and other code items as ordinary files: copy them out as XML exports, and copy XML files in to import them.
> **Target OSes:** macOS and Linux first (tested here: macOS natively, Linux in Docker). Windows comes last and the user tests it by hand.

---

## 0. How to use this plan (rules for the implementing agent)

1. **Do the phases in order.** Each phase ends with a **GATE**. Don't start phase N+1 until gate N passes.
2. **A gate passes only when every command in it exits 0 and no required test is skipped.** Gate runs set `IRISFS_REQUIRE_IRIS=1` and `IRISFS_REQUIRE_FUSE=1` where relevant. In that mode a missing dependency fails the test instead of skipping it.
3. **Write tests first where you reasonably can.** Every phase lists the tests it must add. You may add more tests, but you must not remove or weaken existing ones to get a gate to pass.
4. **Record evidence.** When a gate passes, write `doc/progress/G<N>.md`. It contains the date, the commands you ran, a short summary of their output (test counts and timings), and any deviations from this plan with the reasons for them.
5. **Record decisions.** Every non-trivial choice goes in `doc/DECISIONS.md` as a short ADR: context, decision, consequences. This includes library choices, API endpoints found in the spike, and workarounds. Items marked **[VERIFY]** in this plan are assumptions. Confirm each one in the spike and record the result.
6. **Stop and ask the user when:**
   - a step needs a human, such as installing macFUSE or FUSE-T, approving a system extension, or testing on Windows;
   - a gate still fails after 3 real fix attempts;
   - a finding contradicts the product behaviour in `doc/idea`.
7. **Commit at each gate.** Run `git init` if needed, and use one commit per gate with the message `Gate G<N>: <title>`.
8. **Keep the core free of the GUI and of FUSE.** All filesystem logic lives in a pure-Python `VirtualFS` class that can be tested without a mount. The FUSE adapter and the GUI stay thin.

---

## 1. Product requirements (from `doc/idea`, made precise)

### 1.1 Virtual filesystem

| # | Requirement |
|---|---|
| FS-1 | Mounting a profile makes a local folder (the *mount point*) show the IRIS server. |
| FS-2 | The top-level folders are the **namespaces** the user can access. |
| FS-3 | Each namespace folder contains that namespace's code items: classes, routines (MAC/INT), include files, and other documents (the Atelier "OTH" category, e.g. LUT, HL7, DFI). |
| FS-4 | **Layout (default):** package hierarchy, the same as isfs. `Demo.Sub.Thing.cls` appears as `<NS>/Demo/Sub/Thing.cls.xml`. A routine `MyRtn.mac` appears as `<NS>/MyRtn.mac.xml`. The `.xml` suffix is added because the file's contents are an XML export. |
| FS-5 | **Reading a file** returns the item's XML export (the same format as `$SYSTEM.OBJ.Export`). Copying the file out therefore produces an XML export. |
| FS-6 | **Writing a file** is accepted only when the name ends in `.xml`. When the file is closed, its content is validated as an IRIS XML export and **imported** into the namespace, then compiled if the profile says so. Every item in the XML is imported, whatever the file is named. |
| FS-7 | Writing a file with any other extension fails with `EACCES`, and nothing is imported. |
| FS-8 | **Read-only profiles** mount read-only. Every write fails with `EROFS`. |
| FS-9 | v1 doesn't support delete, rename or mkdir of real items; they fail with `EPERM`. Creating new items other than by XML import is future work, as `doc/idea` says ("after that we will check on how to create a new element"). |
| FS-10 | System items (names starting with `%`) and generated items are **hidden by default**. A profile option, `show_system`, shows them. |
| FS-11 | Files and directories created by the OS or file managers are handled without errors: macOS `._*`, `.DS_Store`, `.localized`, Spotlight and Trash probes; Windows `desktop.ini` and `Thumbs.db`. Creating them either fails quietly or goes to a scratch area in memory. They never reach IRIS. |
| FS-12 | Metadata commands used during copies (`chmod`, `chown`, `utimens`, `setxattr`, `truncate`) are accepted as no-ops on a file being written, so that `cp`, Finder and Explorer copies succeed. |

### 1.2 Tray application

| # | Requirement |
|---|---|
| UI-1 | The app starts in the system tray (menu bar on macOS) with no main window. A macOS build has no Dock icon (`LSUIElement`). |
| UI-2 | **Left-click** opens a menu listing every profile with an active or inactive marker. |
| UI-3 | Clicking an **active** profile asks "Unmount <name>?". Yes unmounts it and marks it inactive. |
| UI-4 | Clicking an **inactive** profile connects and mounts it at the profile's mount point. Failures (bad credentials, host unreachable, mount point invalid, FUSE missing) are shown to the user. |
| UI-5 | **Right-click** opens a menu with **Profiles…** and **Quit**. |
| UI-6 | **Quit** asks for confirmation. Yes unmounts every active profile, then exits. If an unmount fails because the mount is busy, the app says so and offers **Force unmount** or **Cancel quit**. |
| UI-7 | **Profiles…** opens a window with a list of profiles on the left and a form on the right. The form has: name, server host/IP, port, user, password, a read-only checkbox, and a directory picker for the mount point. |
| UI-8 | A **"+"** toolbar button creates a new profile. A **trash** button deletes the selected profile after confirmation. |
| UI-9 | While a profile is **active**, its form is read-only and it can't be deleted (the trash button is disabled). If a profile changes state while the window is open, the window updates immediately. |
| UI-10 | *Platform adaptation:* on macOS and on Linux AppIndicator desktops, the OS doesn't reliably send separate left-click and right-click events to tray icons. There, **one combined menu** is shown: profiles, a separator, Profiles…, and Quit. This is chosen automatically and logged. |
| UI-11 | Only one instance of the app can run at a time. |

**Proposed extra profile fields** (these go in a collapsible "Advanced" section; the user can veto them): `use HTTPS`, `verify TLS certificate`, `URL path prefix` (for a web gateway such as `/iris`), `show system items`, and `compile on import` (default **on**, flags `cuk`). An extra **Test connection** button checks credentials and lists namespaces.

### 1.3 Distribution

- PyInstaller.
  - **Linux:** one `--onefile` binary.
  - **Windows:** one `--onefile` `.exe`.
  - **macOS:** a `.app` bundle delivered as a zip or DMG. PyInstaller 6 discourages onefile `.app` builds, and a tray app needs `LSUIElement`. The Finder treats a `.app` as a single item. Also produce a onefile CLI binary for tests.
- **The FUSE layer is a system prerequisite and is not bundled.** Each OS needs one of:
  - **macOS:** FUSE-T (no kernel extension; preferred) or macFUSE (5.x can use the FSKit backend on macOS 15.4 and later).
  - **Linux:** `fuse3` (`libfuse3`), with `libfuse2` as a fallback.
  - **Windows:** WinFsp.
- The app detects a missing FUSE layer and shows a message with a download link.

---

## 2. Architecture

```
┌────────────────────── GUI process (wxPython) ──────────────────────┐
│ TrayIcon ─┐                                                        │
│ ProfilesFrame ─┼─► AppController ─► MountManager ─► Worker handles │
│ Prompter (confirm/error) ┘   │                    (subprocesses)   │
│                          ProfileStore + SecretStore                │
└─────────────────────────────────────────────┬──────────────────────┘
                   JSON lines over stdin/stdout│ (config incl. password
                                               ▼  sent on stdin, never argv)
┌──────────────── Mount worker process (one per active profile) ────┐
│ FuseAdapter (mfusepy Operations) ─► VirtualFS ─► AtelierClient ──► IRIS
│                                      │  PathMap, TreeCache,        (HTTP REST
│                                      │  ContentCache, Importer      /api/atelier)
└────────────────────────────────────────────────────────────────────┘
```

**Why each mount is a separate process:** the FUSE main loop blocks and takes over signal handling. If a mount crashes, it must not take down the tray app. Stopping a mount is also simple (unmount, or terminate the process). A `worker` sub-command of the same executable does this, which also works in a PyInstaller onefile build (`sys.executable worker`). Tests and power users also get a GUI-less CLI: `irisfs mount --profile NAME`.

### 2.1 Package layout

```
iris_ftp/
  pyproject.toml              # deps, entry point irisfs = irisfs.__main__:main
  src/irisfs/
    __main__.py               # dispatch: (no args)=gui | mount | unmount | worker | profiles | doctor
    cli.py
    log.py                    # rotating file log in platformdirs user_log_dir
    config/
      profile.py              # Profile dataclass + validation
      store.py                # ProfileStore (JSON, atomic write, schema version)
      secrets.py              # SecretStore (keyring → fallback file w/ 0600 + warning)
      mountpoint.py           # per-OS mount point validation / prepare / restore
    atelier/
      client.py               # AtelierClient (httpx.Client, cookie session reuse)
      errors.py               # AuthError, NotFound, ServerError, ConnectionError
      models.py               # DocInfo(name, cat, ts, db, gen), ServerInfo
    vfs/
      pathmap.py              # docname <-> relative path (pure, bijective)
      tree.py                 # per-namespace directory tree built from docnames, TTL cache
      content.py              # export cache keyed by (ns, doc, ts); LRU, size-bounded
      xmlexport.py            # validate XML export, extract item names (defusedxml)
      importer.py             # import + compile, returns ImportResult
      junk.py                 # OS junk-file rules (._*, .DS_Store, desktop.ini…)
      vfs.py                  # VirtualFS: getattr/readdir/open/read/create/write/truncate/release…
      errors.py               # FsError(errno)
    mount/
      fuselib.py              # locate & load FUSE lib per OS; "doctor" diagnostics
      adapter.py              # mfusepy Operations → VirtualFS
      options.py              # per-OS mount options
      worker.py               # worker main: read config on stdin, mount, emit events
      protocol.py             # event/command dataclasses, JSON-lines codec
      manager.py              # MountManager: state machine, spawn/stop workers, stale cleanup
      unmount.py              # per-OS unmount / force unmount
    gui/
      app.py                  # wx.App, SingleInstanceChecker, wiring
      controller.py           # AppController (no wx imports → unit-testable)
      prompter.py             # Prompter protocol + WxPrompter + FakePrompter
      tray.py                 # TaskBarIcon, menus, click-mode selection
      profiles_frame.py       # list + form + toolbar
      icons/                  # tray icons (active/inactive, macOS template variants)
  tests/
    unit/  integration/  e2e/  gui/
    fakes/fake_atelier.py     # in-memory AtelierClient implementation
    conftest.py               # markers, IRIS/FUSE fixtures, REQUIRE_* enforcement
  docker/
    docker-compose.yml
    .env.test                 # IRIS creds for tests (test-only)
    iris/Dockerfile
    iris/seed/                # UDL sources + seed.script
    linux-test/Dockerfile
    linux-build/Dockerfile
  scripts/
    test.sh                   # unit | integration | e2e | gui | all  (macOS + Linux)
    docker-test.sh            # runs test.sh inside linux-test container
    build-macos.sh  build-linux.sh  build-windows.ps1
  packaging/irisfs.spec
  doc/ idea  PLAN.md  DECISIONS.md  WINDOWS_TEST_CHECKLIST.md  progress/
```

### 2.2 Technology choices (confirm in Phase 0)

| Concern | Choice | Notes |
|---|---|---|
| Python | 3.12 | wxPython 4.2.x has wheels for macOS and Windows. On Linux, use the wheels from `extras.wxpython.org`. |
| Env/deps | `uv` (preferred) or `venv` + `pip` | Pin versions in a lock file. |
| HTTP | `httpx` | A thread-safe `Client` with a cookie jar. **Reusing the session cookie is mandatory:** each new CSP session uses a license slot, and Community Edition has very few. |
| FUSE binding | `mfusepy` (maintained fork of fusepy; libfuse2/3, macFUSE, FUSE-T, WinFsp) | **[VERIFY]** in Spike S3. The fallback is `refuse`. Record the choice in an ADR. |
| XML | `defusedxml` | For validation. Blocks XXE and entity expansion. |
| Config paths | `platformdirs` | |
| Secrets | `keyring` | Falls back to a `0600` file with a logged warning. This is needed in Docker, which has no Secret Service. |
| GUI | wxPython (`wx.adv.TaskBarIcon`, `wx.adv.NotificationMessage`) | |
| Tests | `pytest`, `pytest-timeout`, `hypothesis` | |
| Lint/type checks | `ruff`, `mypy --strict` on `src/irisfs` except `gui/` | |
| Packaging | PyInstaller 6.x | |

### 2.3 IRIS access: the Atelier REST API (what isfs uses)

The base URL is `{http|https}://{host}:{port}{prefix}/api/atelier/`, with Basic auth on the first request and the session cookie after that.

| Need | Endpoint (baseline) | Status |
|---|---|---|
| Server info, API version, namespaces | `GET /api/atelier/` → `result.content.{version, api, namespaces[]}` | [VERIFY] field names |
| List items | `GET /api/atelier/v1/{ns}/docnames/{cat}/{type}?generated=0&filter=` (`cat` ∈ `*`, `CLS`, `RTN`, `CSP`, `OTH`) → name, cat, ts, db, gen | [VERIFY] |
| Item metadata/content | `GET /api/atelier/v1/{ns}/doc/{name}` (also `HEAD` for the timestamp) | [VERIFY] |
| **Export as XML** | Option A: `GET …/doc/{name}?format=xml`. Option B: the XML endpoints added in API v7 or later. Option C: `POST …/cvt/doc/xml` (convert UDL → XML). | **[VERIFY] in Spike S2 and pick one** |
| **Import XML** | Option A: the v7+ XML load endpoint. Option B: `POST …/cvt/xml/doc` (XML → UDL), then `PUT …/doc/{name}` for each item. | **[VERIFY] in Spike S2 and pick one** |
| Compile | `POST /api/atelier/v1/{ns}/action/compile?flags=cuk` with a JSON array of names | [VERIFY] |
| Delete (tests only) | `DELETE /api/atelier/v1/{ns}/doc/{name}` | [VERIFY] |

**How to verify endpoints:** in the IRIS container, read the `UrlMap` XData of `%Api.Atelier.v1` … `%Api.Atelier.vN` (the highest version the server reports):

```
docker compose exec iris iris session IRIS -U %SYS
USER>zw ##class(%Dictionary.XDataDefinition).%OpenId("%Api.Atelier.v8||UrlMap").Data.Read(99999)
```

You can also read the vscode-objectscript source (`src/api/index.ts`). Compare the export output with `$SYSTEM.OBJ.Export(name, file)` in the container. The two should be byte-identical apart from the `ts=` attribute on `<Export>`. If none of the export or import options works, the fallback is `POST …/action/query`, which calls a small helper stored procedure. That fallback needs user approval, because it installs a class on the server. **Stop and ask** before using it.

### 2.4 Key behaviours of `VirtualFS`

- **Tree:** built for each namespace from `docnames`. It has a TTL, 10 s by default (configurable), and is invalidated after every import. Directories are package segments. Files are `<LastSegment>.<ext>.xml`.
- **Mapping (`pathmap`):** `Demo.Sub.Thing.cls` ↔ `Demo/Sub/Thing.cls.xml`. Routines and include files use the same dot-splitting as isfs. CSP items (`/csp/...`) are **excluded in v1**. The mapping must be a bijection over valid names, which a Hypothesis property test checks. On case-insensitive filesystems (macOS, Windows), if two names differ only by case, keep one and log a warning.
- **`getattr` size:** OS copy tools need an accurate `st_size`. When the tree isn't cached or the entry is stale, the size comes from the content cache. That can trigger an export, with at most 4 exports running at once. `mtime` comes from the doc `ts`. The trade-off is that `ls -l` on a big folder triggers exports. This is measured in the performance test (§G5). If it's too slow, use the fallback: report the size from an estimate and mount with `direct_io`. Record the decision in an ADR.
- **Reads:** served by offset and length from the cached export (`bytes`, UTF-8).
- **Writes:** `create` or `open(O_WRONLY|O_TRUNC)` on a `*.xml` path starts an in-memory `WriteBuffer`. If the file isn't truncated, the buffer starts with the current export. `write` and `truncate` edit the buffer. The buffer tracks a **dirty** flag. If a file is opened for writing and closed without any `write` or `truncate`, it is discarded and nothing is imported. Tools and editors often open files read-write without changing them, so an unchanged file must never be reimported. On `release` (the last close) of a **dirty** buffer:
  1. Validate the XML with `defusedxml`. The root must be `<Export>`, and at least one item must be present (`<Class name>`, `<Routine name type>`, `<Document name>`, …).
  2. Import it.
  3. Compile it if the profile's compile option is on.
  4. Invalidate the tree and content caches for the affected items.
  5. Emit an `imported` or `import_failed` event (the tray shows a notification).
  6. If the file name doesn't match an imported item, keep a "ghost" entry for 60 s so the copy tool's post-copy `stat` succeeds.

  `release` can't report errors reliably, so errors reach the user through events and the log. `flush` does the XML well-formedness check early and returns `EIO` if it fails. That way `cp` reports an error for malformed XML on the platforms that pass `flush` errors back.
- **Junk files:** names matching the junk rules are held in a scratch area in memory for the session and never sent to IRIS. On macOS, also pass the mount options `noappledouble` and `noapplexattr` when the FUSE implementation supports them.
- **Read-only:** mount with `ro`, and also enforce read-only in `VirtualFS` (`EROFS`).
- **Errors:** IRIS unreachable → `EIO` (the mount stays up and recovers). HTTP 401 during a session → re-authenticate once, then `EACCES`. Item not found → `ENOENT`.
- **Concurrency:** FUSE may call in from several threads. Protect the caches with locks, and allow at most 4 requests to IRIS at once.

### 2.5 Worker protocol (JSON lines)

- **Parent → worker (stdin):**
  - First line: `{"cmd":"start","profile":{...},"password":"..."}`.
  - After that: `{"cmd":"stop"}` or `{"cmd":"ping"}`.
- **Worker → parent (stdout):**
  - `{"event":"mounted","mountpoint":...}`
  - `{"event":"error","code":"AUTH_FAILED|UNREACHABLE|FUSE_MISSING|MOUNTPOINT_INVALID|MOUNT_FAILED","message":...}`
  - `{"event":"imported","ns":...,"items":[...],"compiled":true,"compile_errors":[...]}`
  - `{"event":"import_failed","ns":...,"file":...,"message":...}`
  - `{"event":"unmounted"}`
- Logs go to stderr and the log file, never to stdout.
- Before mounting, the worker **checks credentials** with `GET /api/atelier/`. If the check fails, it reports the error without mounting.

### 2.6 `MountManager` state machine

```
INACTIVE ──mount()──► MOUNTING ──"mounted"──► ACTIVE ──unmount()──► UNMOUNTING ──"unmounted"/exit──► INACTIVE
    ▲                    │ error/timeout(30s)                │ worker died unexpectedly
    └────────────────────┴───────────────────────────────────┴──► INACTIVE (+ notify, stale-mount cleanup)
```

- Unmount order:
  1. Send `stop` to the worker.
  2. If it hasn't stopped within 5 s, run the per-OS unmount: `fusermount3 -u` or `fusermount -u` on Linux, `umount` on macOS (`diskutil unmount force` when forced), and on Windows terminate the process, after which WinFsp cleans up.
  3. If it still hasn't stopped after another 5 s, kill the worker and report `BUSY` if the mount point is still mounted.
- **On start:** find stale mounts from a previous crash (profile mount points that are still FUSE mounts) and unmount them.
- State changes are published to subscribers (the tray and the Profiles window) with `wx.CallAfter`.

### 2.7 Mount point rules (`config/mountpoint.py`)

- **macOS/Linux:** the path must exist, be a directory, be empty, not already be a mount point, and be writable. It must not be inside another active mount, and no other profile may use it.
- **Windows** (WinFsp needs a path that *doesn't* exist, or a drive letter):
  - A free drive letter such as `X:` is allowed.
  - Otherwise the directory's parent must exist. If the directory exists and is empty, the app deletes it just before mounting and recreates it empty after unmounting. This is documented in the UI tooltip.

---

## 3. Test environment

### 3.1 IRIS in Docker (`docker/docker-compose.yml`)

- Service `iris` uses the image `containers.intersystems.com/intersystems/iris-community:<pinned tag>`. **[VERIFY]** in S1 that the image serves `/api/atelier/` on port 52773:
  - Newer releases removed the Private Web Server. If `curl` fails, either add a `webgateway` service (`intersystems/webgateway-nginx`, same tag) or pin to the newest tag that still has the PWS.
  - Record the result in an ADR.
- Ports: `52773:52773` for HTTP, and `1972:1972` only for debugging.
- **Credentials:** use the image's `--password-file` option to set `_SYSTEM`'s password from `docker/.env.test` (a test-only password such as `irisfs-test`). This avoids the forced change on first login. [VERIFY]
- **Seed** (baked into the image at build time with `iris start` → `iris session IRIS < seed.script` → `iris stop`):
  - Namespace `USER`:
    - `Demo.Person` (persistent, with properties and a method)
    - `Demo.Util` (class methods)
    - `Demo.Sub.Thing` (nested package)
    - `Demo.Unicode` (comments with `àèìòù €  漢字 😀`)
    - `Demo.Big` (a generated class larger than 1 MB)
    - `DEMORTN.mac`, `Demo.Rtn2.mac` (a routine name with a dot)
    - `DemoInc.inc`
    - one OTH document if an easy one exists (for example a `.LUT`). **[VERIFY]**
  - Namespace `TESTNS` (a new database and namespace created with `Config.Databases` and `Config.Namespaces`) with `Test.A`, `Test.B`, and a routine.
  - Namespace `PERFNS`, created by a flag in the compose profile `perf`, with 2 000 generated classes in 50 packages.
  - User `irisfs_ro`, which can read code but has no write privilege on the code databases. It's used for permission-denied tests.
- Load the seed sources with `$SYSTEM.OBJ.LoadDir("/seed/<ns>", "ck", , 1)`. Keep the sources in UDL (`.cls`, `.mac`, `.inc`) so they're easy to edit.
- Healthcheck: `curl -fsu _SYSTEM:$PW http://localhost:52773/api/atelier/`.

### 3.2 Linux test container (`docker/linux-test`)

- Base image `ubuntu:24.04` with `python3.12`, `fuse3`, `libfuse3-3`, `xvfb`, `xauth`, and the GTK3 runtime libraries. wxPython is installed from `https://extras.wxpython.org/wxPython4/extras/linux/gtk3/ubuntu-24.04/`.
- Run options: `devices: [/dev/fuse]`, `cap_add: [SYS_ADMIN]`, `security_opt: [apparmor:unconfined]`, and the repo mounted at `/work`.
- The container reaches IRIS at `iris:52773`, while the macOS host uses `localhost:52773`. Tests read `IRISFS_TEST_HOST` and `IRISFS_TEST_PORT`.
- `scripts/docker-test.sh <suite>` runs `docker compose run --rm linux-test scripts/test.sh <suite>`.

### 3.3 macOS host

- Prerequisites (**user action**; the agent must ask the user to do these):
  - Docker Desktop, which is already installed.
  - **FUSE-T** (`brew install --cask fuse-t`, preferred) **or macFUSE**. Neither is installed yet.
  - Python 3.12. It's already present at `/opt/local/bin/python3`.
  - `uv` is optional.
- The macOS end-to-end tests mount under the `$TMPDIR/irisfs-e2e-*` directories.

### 3.4 Test layers and markers

| Layer | Marker | Needs | Runs on |
|---|---|---|---|
| unit | *(none)* | nothing (uses `FakeAtelierClient`) | macOS and Linux |
| integration | `iris` | IRIS container | macOS and Linux |
| e2e | `iris`, `fuse` | IRIS and a real mount; exercises it with `ls`, `cp`, `cat`, and Python `open()` | macOS and Linux |
| gui | `gui` | wx and a display (Xvfb on Linux) | Linux in Docker (headless); macOS smoke test |
| perf | `perf` | `PERFNS` | on demand, required at G5 |
| manual | checklist | a human | macOS (G8) and Windows (G10) |

`scripts/test.sh all` runs unit, integration, e2e and gui with `IRISFS_REQUIRE_IRIS=1 IRISFS_REQUIRE_FUSE=1`.

---

## 4. Phases and gates

### Phase 0: Environment and technical spikes

**Goal:** remove the big unknowns before writing product code. Spike code lives in `spikes/`, which is deleted or ignored afterwards.

- **S1: IRIS container.**
  - Write `docker/iris/Dockerfile`, the seed files and the compose file.
  - Run `docker compose up -d --wait iris`.
  - Confirm that `curl -u _SYSTEM:… http://localhost:52773/api/atelier/` returns JSON listing `USER`, `TESTNS` and `%SYS`.
  - Record the IRIS version and the Atelier `api` version.
- **S2: Atelier XML round trip.** Write a Python script that uses `httpx`:
  1. List docnames in `USER`.
  2. Export `Demo.Person.cls` as XML using each of the candidate options in §2.3.
  3. Compare each result with `$SYSTEM.OBJ.Export` output from `docker exec`, ignoring the `ts` attribute.
  4. Change a comment in the XML and import it using each import option.
  5. Compile.
  6. Export again and check that the change is there.
  7. Repeat for `DEMORTN.mac`, `DemoInc.inc` and `Demo.Unicode.cls`.
  8. Measure the time for each call.
  9. Check that 200 consecutive calls on one `httpx.Client` don't exhaust licenses, and record what happens without cookie reuse.
- **S3: FUSE hello world.** Write a 30-line read/write in-memory filesystem using `mfusepy`, run in a subprocess, and unmount it programmatically.
  - (a) On Linux in `linux-test`, with libfuse3.
  - (b) On macOS with FUSE-T or macFUSE, after the user has installed it. Check what `mfusepy` needs to find the library (for example the `FUSE_LIBRARY_PATH` environment variable pointing to `libfuse-t.dylib`).
  - Test `cp` in and out, Finder-style `xattr` calls, and unmounting while a shell is `cd`'d inside (the busy case).
  - If `mfusepy` fails on either OS, try `refuse`.
- **S4: wx tray on macOS.** Write a minimal `TaskBarIcon` that logs `EVT_TASKBAR_LEFT_DOWN` and `EVT_TASKBAR_RIGHT_DOWN`, to confirm UI-10. Build it with PyInstaller as a `.app` with `LSUIElement` and confirm there's no Dock icon.

**Deliverables:** `docker/` (working), `doc/DECISIONS.md` with ADRs for the image tag and web server, the export method, the import method, the FUSE binding, macOS FUSE handling, and the tray click mode. Also the `spikes/` scripts and their output.

**GATE G0**
- [ ] `docker compose up -d --wait iris` becomes healthy from a clean state in under 3 min.
- [ ] Spike S2 prints `ROUNDTRIP OK` for all 4 seed items, using the export and import methods chosen in the ADRs.
- [ ] Spike S3 prints `FUSE OK` on Linux (Docker) **and** macOS.
- [ ] S4 shows how the tray behaves on macOS; recorded in an ADR.
- [ ] Every [VERIFY] item in §2.3, §3.1 and §2.2 is resolved in `DECISIONS.md`.
- [ ] **User checkpoint:** show the user the ADR summary before Phase 1. This matters most if the choices change the file layout or the import behaviour.

---

### Phase 1: Project skeleton and tooling

**Tasks:**
- Write `pyproject.toml` with the `src` layout, pinned dependencies, and optional dependency groups `[gui]`, `[dev]` and `[build]`.
- Add configuration for `ruff`, `mypy` and `pytest`, including markers and `--timeout=120`.
- Write `tests/conftest.py`:
  - fixtures `iris_conn` (from env or `docker/.env.test`) and `fuse_available`;
  - `REQUIRE_*` enforcement: if `IRISFS_REQUIRE_IRIS=1` and IRIS isn't reachable, fail, don't skip.
- Write `scripts/test.sh` and `scripts/docker-test.sh`, and `docker/linux-test/Dockerfile`.
- Write `irisfs/__main__.py` with sub-command stubs, and `log.py`.
- Add `irisfs doctor`, which prints the OS, Python version, FUSE library location, whether keyring works, and the config and log paths.

**Tests:** `test_cli_help` (every sub-command responds to `--help`) and `test_doctor_runs`.

**GATE G1**
- [ ] `ruff check . && ruff format --check . && mypy src/irisfs` all pass.
- [ ] `scripts/test.sh unit` passes on macOS.
- [ ] `scripts/docker-test.sh unit` passes in Linux Docker.
- [ ] `python -m irisfs doctor` finds a FUSE library on macOS and in Linux Docker.

---

### Phase 2: Profiles and secrets

**Tasks:**
- Write the `Profile` dataclass with fields `id` (UUID), `name`, `host`, `port=52773`, `https=False`, `verify_tls=True`, `path_prefix=""`, `username`, `read_only=False`, `mount_point`, `show_system=False`, `compile_on_import=True`, `compile_flags="cuk"`.
- Write `validate()`, which returns field-level errors.
- Write `ProfileStore`: a JSON file in `platformdirs.user_config_dir("irisfs")` with `{"version":1,"profiles":[...]}`.
  - Writes are atomic: write a temporary file, then `os.replace`.
  - Names must be unique (case-insensitive).
  - It supports CRUD and an `is_active` hook that `MountManager` supplies later. `delete` and `update` of an active profile raise `ProfileActiveError`.
- Write `SecretStore`: `keyring` with the service name `irisfs` and the profile id as the key. The fallback is `secrets.json` with mode `0600` and a warning in `doctor`. Deleting a profile deletes its secret.
- Write `mountpoint.validate(path, os_name, other_profiles)` implementing §2.7, with an injectable filesystem-probe interface so it can be unit-tested on any OS.

**Tests (unit):**
- CRUD round trip.
- Duplicate names are rejected.
- A crash in the middle of a write leaves the old file intact (simulated with a monkeypatched `os.replace`).
- A corrupt JSON file produces a clear error and a backup copy, not a crash.
- A wrong schema version is handled.
- An active profile can't be deleted or updated.
- Passwords are never written to the profiles JSON (grep the file).
- Secret round trip through the fallback backend.
- Mount point rules, one test per rule, for posix and windows (use the fake probe).
- Port bounds and an empty host are rejected.

**GATE G2**
- [ ] Unit suite green on macOS and in Linux Docker. Line coverage of `config/` is at least 90% (`pytest --cov=irisfs.config`).

---

### Phase 3: Atelier client

**Tasks:**
- Write `AtelierClient(base_url, user, password, verify_tls, timeout)` with these methods:
  - `server_info()`
  - `namespaces()`
  - `list_docs(ns, include_system, include_generated) -> list[DocInfo]`
  - `doc_timestamp(ns, name)`
  - `export_xml(ns, name) -> bytes`
  - `import_xml(ns, data: bytes) -> list[str]` (the imported item names)
  - `compile(ns, names, flags) -> CompileResult`
  - `delete_doc(ns, name)`, used only by tests
- Use the endpoints chosen in G0.
- Reuse the session through the `httpx.Client` cookie jar, with a concurrency semaphore of 4.
- Retry once after a transient connection error. On a 401 with a live session, re-authenticate once.
- Map errors to typed exceptions. Never log passwords or the `Authorization` header (test this).
- Write `tests/fakes/fake_atelier.py`, an in-memory implementation with the same interface. It's seeded from fixtures and can inject errors.
- Add a **contract test suite**: the same tests run against both `FakeAtelierClient` and the real client (parametrized fixture), so the fake can't drift from reality.

**Tests:**
- *Unit:* URL building (prefix, https, namespace `%SYS` URL-encoded as `%25SYS`), error mapping from recorded responses (`httpx.MockTransport`), and log redaction.
- *Integration (IRIS):*
  - `namespaces()` includes `USER` and `TESTNS`.
  - `list_docs` finds every seed item with correct categories. `%` items are excluded by default and included with the flag.
  - The export of each seed item parses as XML with root `Export`, and equals `$SYSTEM.OBJ.Export` output ignoring `ts`.
  - The unicode item survives the round trip byte for byte.
  - Import of a modified item: the change is visible and compiled.
  - Importing a new class creates it. Clean up afterwards.
  - Importing malformed XML raises an error and leaves the server state unchanged.
  - A wrong password raises `AuthError` quickly (under 3 s).
  - An unreachable host raises `ConnectionError` within the timeout.
  - The `irisfs_ro` user can export, but importing raises a permission error.
  - 300 sequential calls and 50 parallel exports succeed with no license errors.

**GATE G3**
- [ ] `scripts/test.sh unit integration` green on macOS against the Docker IRIS.
- [ ] `scripts/docker-test.sh unit integration` green in Linux Docker.
- [ ] The contract suite passes for both the fake and the real client.

---

### Phase 4: VirtualFS core (no FUSE yet)

**Tasks:**
- `pathmap`: `doc_to_path(name) -> tuple[str, ...]` and `path_to_doc(parts) -> str | None`. Invalid or unknown paths return `None`.
- `tree`: a tree per namespace with TTL and invalidation. `readdir(ns, dirparts)` and `lookup(ns, parts)` return a `Dir` or a `File(DocInfo)`.
- `content`: an LRU export cache bounded by total bytes (default 256 MB), keyed by `(ns, name, ts)`. At most one export runs per key at a time.
- `xmlexport`:
  - `validate(data) -> ExportManifest(items=[(kind, name)])`, using `defusedxml`.
  - It rejects DTDs and entities, a root other than `Export`, and a file with zero items.
- `importer`: `import_file(ns, data, profile) -> ImportResult`.
- `junk`: rules per OS.
- `vfs.VirtualFS`: operations `getattr`, `readdir`, `open`, `read`, `create`, `write`, `truncate`, `flush`, `release`, `unlink`, `rename`, `mkdir`, `rmdir`, `chmod`, `chown`, `utimens`, `setxattr`, `getxattr`, `listxattr`, `removexattr` and `statfs`.
  - Each returns plain Python data or raises `FsError(errno)`.
  - The behaviour follows §2.4 and FS-1…FS-12.
  - It emits events through an injected callback.
- The root directory lists namespaces. Namespace names are shown exactly as the server returns them (`%SYS` is allowed on every OS).

**Tests (unit, against `FakeAtelierClient`):**
- *pathmap:* a Hypothesis property test that `path_to_doc(doc_to_path(n)) == n` for generated valid class, routine and include names; rejection of CSP names; a table of examples.
- *getattr and readdir:*
  - root, namespace, package directories, and files;
  - `st_size` equals `len(export)`;
  - `mtime` equals the doc `ts`;
  - `ENOENT` for unknown paths;
  - system items hidden and shown by the flag.
- *read:* offsets and lengths, including reading past the end of the file and reading at the exact size.
- *Write flow:*
  - `create("x.xml")` → `write` in chunks at offsets → `release`: the fake records exactly one import with the joined bytes, and a compile when the option is on.
  - `open` with `O_TRUNC` on an existing file → import.
  - `open` without `O_TRUNC` and a partial write → the buffer is based on the existing export.
- *No accidental import:* opening a file with `O_RDWR` or `O_WRONLY`, without `O_TRUNC`, then closing it with no write → zero import calls. Reading files, including thousands of reads, never triggers an import.
- *Rejections:* `create("x.txt")` fails with `EACCES`, and nothing is imported. `unlink`, `rename`, `mkdir` and `rmdir` fail with `EPERM`. Read-only mode: every mutating op fails with `EROFS`.
- *Bad XML:* malformed XML fails at `flush` with `EIO` and emits an `import_failed` event with no import call. An XXE payload is rejected. A root other than `Export` is rejected.
- *Junk files:* `._Person.cls.xml` and `.DS_Store` are accepted in the scratch area, readable back, never imported, and gone after remount.
- *Ghost entries:* importing `foo.xml` that contains `Demo.New` → `foo.xml` is visible for 60 s (inject a clock), `Demo/New.cls.xml` is visible immediately, and `foo.xml` has vanished after the TTL.
- *No-op metadata:* `chmod`, `utimens` and `setxattr` on a write buffer succeed. On a real item they succeed as no-ops, or fail with `EPERM` in read-only mode. Decide which and document it.
- *Errors:*
  - The fake raises a connection error → `EIO`, and the next call recovers.
  - Auth expiry → one re-authentication.
  - A compile error → an `imported` event with `compile_errors` filled in.
- *Concurrency:* 32 threads reading 20 files at random offsets finish without errors, and each item is exported only once (the fake counts calls).

**Tests (integration):** the same scenarios through `VirtualFS` with the real client against IRIS: read all seed items, import a modified `Demo.Util`, import a new class, and import malformed XML.

**GATE G4**
- [ ] Unit and integration suites green on macOS and in Linux Docker.
- [ ] Coverage of `vfs/` is at least 90%.
- [ ] `mypy --strict` is clean for `vfs/` and `atelier/`.

---

### Phase 5: FUSE adapter, worker, CLI mount (read path end-to-end)

**Tasks:**
- `mount/fuselib.py`: find the FUSE library.
  - Linux: libfuse3, then libfuse2.
  - macOS: FUSE-T (`/usr/local/lib/libfuse-t.dylib`), then macFUSE (`/usr/local/lib/libfuse.2.dylib`). **[VERIFY]** these paths.
  - Windows: find WinFsp through the registry key `HKLM\SOFTWARE\WOW6432Node\WinFsp\InstallDir`, then `bin\winfsp-x64.dll`.
  - Set whatever environment variable or loader hook the chosen binding needs, before importing it.
- `mount/options.py`: `fsname=irisfs-<profile>`, `volname=<profile name>` on macOS, `ro` when read-only, `noappledouble` and `noapplexattr` on macOS when supported, and `uid`/`gid` set to the current user. Add `direct_io` only if G5 perf requires it.
- `mount/adapter.py`: `Operations` subclass. Every method delegates to `VirtualFS` and turns `FsError` into `FuseOSError`. Unexpected exceptions are logged with a traceback and become `EIO`.
- `mount/worker.py`:
  1. Read the `start` command from stdin.
  2. Build the client and check auth.
  3. Validate the mount point.
  4. Run FUSE in the foreground in the worker's main thread.
  5. Read stdin for `stop` in a side thread; on `stop`, unmount the worker's own mount point.
  6. Emit events.
  7. Handle SIGTERM and SIGINT by unmounting.
  8. Exit 0 on a clean unmount.
- `cli.py`:
  - `irisfs mount --profile NAME` runs a worker attached to the terminal and prints events.
  - `irisfs mount --host … --port … --user … --password-stdin --mountpoint … [--read-only]` is for tests.
  - `irisfs unmount PATH [--force]`.

**E2E tests** (`tests/e2e/`, markers `iris` and `fuse`; a fixture starts the worker through the CLI in a subprocess, waits for `mounted`, and always unmounts in teardown):

| ID | Scenario | Expected |
|---|---|---|
| E2E-01 | `ls <mnt>` | contains `USER` and `TESTNS` |
| E2E-02 | `ls <mnt>/USER/Demo` | contains `Person.cls.xml`, `Util.cls.xml`, `Unicode.cls.xml`, `Big.cls.xml`, `Sub/`, `Rtn2.mac.xml` |
| E2E-03 | `cat <mnt>/USER/Demo/Person.cls.xml` | well-formed XML; `Export/Class[@name='Demo.Person']` |
| E2E-04 | `cp` the file out to a temp directory | equals the `$SYSTEM.OBJ.Export` reference, ignoring `ts` |
| E2E-05 | `cp` out `Unicode.cls.xml` | UTF-8 bytes identical to the reference |
| E2E-06 | `cp` out `Big.cls.xml` (over 1 MB) twice | sha256 matches the reference both times |
| E2E-07 | `stat` a file | size equals the byte length of the content; `mtime` matches the doc `ts` |
| E2E-08 | `cp -R <mnt>/TESTNS <tmp>` | the full tree copies without errors; the file count equals the docnames count |
| E2E-13 | unmount via `stop` | the mount point is no longer mounted (`os.path.ismount` is False); the worker exits 0 within 5 s |
| E2E-14 | `docker compose pause iris` while mounted, `cat` a file that isn't cached, then `unpause` | the read fails with `EIO` quickly (timeout at most 10 s); after `unpause`, reads succeed with no remount |
| E2E-15 | mount with a wrong password | an `error` event with `AUTH_FAILED`; nothing is mounted; exit code ≠ 0 |
| E2E-16 | 50 parallel `cat`s | all succeed; no license errors in the IRIS log (`messages.log` via `docker exec`) |
| E2E-19 | mount on a non-empty directory | `MOUNTPOINT_INVALID` |

**Performance test** (`perf` marker, uses `PERFNS` with 2 000 classes): measure `ls -R` and `ls -lR` from a cold cache, and record the times in G5.md. The targets are `ls -R` under 5 s and `ls -lR` under 60 s. If `ls -lR` misses its target, apply the `direct_io`/estimated-size fallback from §2.4, record an ADR, and re-test that E2E-04…06 still pass.

**GATE G5**
- [ ] `scripts/test.sh e2e` green on macOS (FUSE-T or macFUSE).
- [ ] `scripts/docker-test.sh e2e` green on Linux.
- [ ] Perf results recorded, and targets met or the fallback ADR accepted.
- [ ] Manual check on macOS: open the mount in Finder, browse, drag a file to the Desktop, and open the copy in a text editor (it's XML). Record the result.

---

### Phase 6: Write path end-to-end (import by copying)

**Tasks:**
- Test the write operations from Phase 4 through FUSE on both OSes, and fix platform quirks: Finder's `._` files and `com.apple.FinderInfo` xattrs, `cp -p`, GNU `cp`'s `fallocate` and `copy_file_range`, and `rsync --inplace`.
- Make `flush` report malformed XML as `EIO`.

**E2E tests:**

| ID | Scenario | Expected |
|---|---|---|
| E2E-20 | Export `Demo.Util` to temp, add a method with `sed`, `cp` it back over `<mnt>/USER/Demo/Util.cls.xml` | the method exists (checked via the API); compiled; an `imported` event |
| E2E-21 | `cp newclass.xml <mnt>/USER/` (contains `Demo.NewOne`) | `Demo/NewOne.cls.xml` appears within 1 s; the class exists and is compiled; the ghost `newclass.xml` vanishes after the TTL |
| E2E-22 | `cp notes.txt <mnt>/USER/` | `cp` fails (`EACCES`); nothing is imported |
| E2E-23 | `cp broken.xml <mnt>/USER/` (malformed) | `cp` fails, or an `import_failed` event is emitted (assert both, per platform ADR); server unchanged |
| E2E-24 | Read-only mount: `cp` in | fails with `EROFS`; reads still work |
| E2E-25 | `rm`, `mv` and `mkdir` inside the mount | fail with `EPERM`; server unchanged |
| E2E-26 | XML containing a class with a compile error | imported; the `imported` event has `compile_errors` filled in |
| E2E-27 | XML containing 3 items (a class, a routine and an include file) | all 3 are imported |
| E2E-28 (macOS) | `cp -X` versus plain `cp`, then `xattr -w com.example test <file>` during the write, then `ditto` | the copy succeeds; no `._*` item ever reaches IRIS (check docnames) |
| E2E-29 | Round trip: `cp -R <mnt>/TESTNS <tmp>`, delete `Test.A` via the API, `cp <tmp>/TESTNS/Test/A.cls.xml <mnt>/TESTNS/Test/` | `Test.A` is restored and identical |
| E2E-30 | Import as the `irisfs_ro` user | an `import_failed` event mentioning a permission error; the `cp` itself may succeed (ADR) |
| E2E-31 | XXE payload file | rejected; no outbound request (the test uses a local HTTP listener to prove it) |

Clean up after every test: delete the created items and restore the modified seed items. The integration fixture restores `Demo.Util` from its seed source.

**GATE G6**
- [ ] All E2E-2x tests green on macOS and in Linux Docker, run 3 times in a row (checks for flaky tests).
- [ ] Manual check on macOS: drag an XML file from the Desktop into the mounted `USER` folder in Finder, and confirm the class exists in IRIS (Management Portal or the API). Record the result.

---

### Phase 7: MountManager (GUI-less orchestration)

**Tasks:**
- Implement the `MountManager` state machine from §2.6:
  - `mount(profile_id)`, `unmount(profile_id, force=False)`, `unmount_all()`, `state(profile_id)`, and `subscribe(callback)`.
  - A worker handle: `subprocess.Popen([sys.executable, "-m", "irisfs", "worker"])`, or `[sys.executable, "worker"]` when frozen. It has a stdout reader thread that parses events.
  - Timeouts: 30 s to mount, 5 s + 5 s to unmount.
- Clean up stale mounts on startup.
- Hand `ProfileStore` the `is_active` hook.
- Add a hidden CLI helper, `irisfs manager-demo`, that mounts two profiles and unmounts them, for manual debugging.

**Tests:**
- *Unit* (a fake worker, i.e. a Python script that follows the protocol with scripted behaviour):
  - every state transition;
  - a mount timeout;
  - a worker crashing while `ACTIVE` → `INACTIVE` plus a notification;
  - an unmount escalation (stop ignored → OS unmount → kill);
  - two profiles on the same mount point are rejected;
  - subscribers get ordered events;
  - `unmount_all` sends stop to every worker at once.
- *E2E:*
  - Mount two profiles at the same time (`USER` via `_SYSTEM`, and a read-only profile) at different mount points; both work; `unmount_all` leaves both unmounted.
  - Kill a worker with `SIGKILL` → the manager reports `INACTIVE`, and the next start cleans up the stale mount.
  - Busy unmount (a subprocess keeps a file open in the mount) → `BUSY` is reported; `force=True` succeeds (on Linux that's `fusermount -uz`).

**GATE G7**
- [ ] Unit and E2E manager tests green on macOS and in Linux Docker.
- [ ] No zombie processes or leftover mounts after the whole suite: a final check test lists `mount | grep irisfs` and `pgrep -f "irisfs worker"`, and both must be empty.

---

### Phase 8: GUI (tray and Profiles window)

**Tasks:**
- `AppController`, pure Python with no `wx` import:
  - `on_profile_clicked(id)`: if the profile is active → `prompter.confirm("Unmount …?")` → `manager.unmount`; otherwise → `manager.mount`.
  - `on_quit()`: confirm → `unmount_all` → handle `BUSY` (confirm force) → exit.
  - `profiles_view_model()`: the list of profiles with their states.
  - Profile CRUD pass-through with validation errors per field.
  - `test_connection(profile, password)` runs in a thread.
- `Prompter` protocol: `confirm`, `error`, `info` and `notify`. `WxPrompter` uses `wx.MessageDialog` and `wx.adv.NotificationMessage`.
- `tray.py`:
  - `TaskBarIcon` with active and inactive icon variants (macOS template images for dark mode). The tooltip is "IRISFS: N mounted".
  - Click mode: `split` (left: profiles, right: Profiles… and Quit) or `combined` (UI-10). It's detected per platform, and an env var or config value can override it.
  - Profile menu items are check items (✓ = active). While mounting or unmounting, the label shows "(connecting…)" or "(unmounting…)" and the item is disabled.
- `profiles_frame.py`:
  - A `wx.Frame` with a toolbar (`wx.ART_PLUS`, `wx.ART_DELETE`), a list on the left (`wx.ListCtrl` with a state icon per profile), and on the right a form:
    - Name
    - Host
    - Port (`wx.SpinCtrl` 1–65535)
    - User
    - Password (`wx.TE_PASSWORD`)
    - Read-only checkbox
    - Mount point (`wx.DirPickerCtrl`)
    - Advanced (collapsible): HTTPS, verify TLS, path prefix, show system items, compile on import
    - **Test connection**, **Save** and **Revert** buttons
  - Invalid fields are highlighted with error text.
  - For an active profile, every control is disabled, the trash button is disabled, and there's a note: "Unmount this profile to edit it".
  - Unsaved changes prompt before switching to another profile or closing.
  - The frame subscribes to manager state changes.
- `app.py`:
  - `wx.App` with `SingleInstanceChecker`; a second launch shows a notification and exits.
  - Load the profiles, clean up stale mounts, and create the tray.
  - No windows at startup.
  - On macOS, `LSUIElement` is set in the bundle (Phase 9). When running from source, call `wx.GetApp().SetActivationPolicy`, or accept the Dock icon. **[VERIFY]**
- Errors from the worker (`AUTH_FAILED`, `FUSE_MISSING`, …) become human-readable dialogs. `FUSE_MISSING` includes the install link for the OS.

**Tests:**
- *Unit (controller with fakes):*
  - clicking an active profile → confirm → yes → unmount; no → nothing happens;
  - clicking an inactive profile → mount; a mount error shows an error;
  - quit → confirm no → nothing happens; yes → `unmount_all` → exit; `BUSY` → force prompt;
  - deleting an active profile is refused; deleting an inactive profile → confirm → deleted along with its secret;
  - saving an invalid profile shows field errors;
  - state updates reach the view model.
- *GUI smoke (Xvfb in Linux Docker, `wx` marker; on macOS run locally):*
  - Create `ProfilesFrame` with a temporary store and a `FakePrompter`.
  - Add a profile with the toolbar handler → fill the controls programmatically → Save → the store contains it.
  - Select a profile marked active → every form control `IsEnabled() == False` and the trash button is disabled.
  - Build the tray menu in both click modes → the item labels and check states match the view model.
  - The app starts and exits cleanly within 5 s (`wx.CallLater` → `ExitMainLoop`).

**Manual checklist (macOS, the user or the agent with the user's help)** → `doc/progress/G8-manual.md`:
1. Launch: the icon appears in the menu bar and there's no window.
2. Menu shows the profiles; mount one; the Finder shows the volume and folder; the ✓ appears.
3. Click the active profile → confirm → it unmounts.
4. Profiles…: add, edit, save, delete (with confirmation); an active profile is read-only and can't be deleted.
5. Wrong password → a clear error.
6. Quit with 2 active mounts → confirm → both unmounted → the app exits.
7. Quit while a Terminal is `cd`'d into the mount → the busy prompt appears → force works.
8. Dark mode icon legibility.

**GATE G8**
- [ ] Controller unit tests and GUI smoke tests green (Linux Docker with Xvfb, and macOS).
- [ ] Manual checklist on macOS all ✓, with any issues fixed or logged in `doc/KNOWN_ISSUES.md` and approved by the user.

---

### Phase 9: Packaging (PyInstaller) for macOS and Linux

**Tasks:**
- `packaging/irisfs.spec`:
  - one entry point (`irisfs/__main__.py`);
  - hidden imports for the FUSE binding and `keyring` backends;
  - bundle the icons;
  - `--windowed` for the GUI on macOS and Windows;
  - the macOS `BUNDLE` with `info_plist={"LSUIElement": True, "CFBundleIdentifier": "org.irisfs.app", ...}`.
- The frozen executable must dispatch `worker` and `mount` sub-commands correctly (`sys.executable` → the binary itself). Add a `multiprocessing.freeze_support()` guard even though `subprocess` is used.
- `scripts/build-macos.sh` produces `dist/IRISFS.app`, a zip, and `dist/irisfs` (onefile CLI).
- `scripts/build-linux.sh` builds in the `docker/linux-build` container. It uses an older glibc base (Debian bookworm or `manylinux_2_28`) for wider compatibility, and produces `dist/irisfs-linux-x86_64` (plus arm64 if the host is arm64; note which).
- `irisfs --version` shows the version and git sha.

**Tests:**
- *Binary smoke:*
  - `dist/irisfs --version`
  - `dist/irisfs doctor`
  - the E2E subset E2E-01, 03, 04, 13, 20 and 22, **run with the built binary**. The fixture uses `IRISFS_BIN` instead of `python -m irisfs`.
  - Run this on macOS with the onefile CLI and with the `.app`'s inner executable, and on Linux in a **clean** container (`ubuntu:24.04` + `fuse3` only, no Python) against Docker IRIS.
- *GUI binary smoke on Linux (Xvfb):* launch, confirm the process is alive after 3 s, then send SIGTERM → clean exit.

**GATE G9**
- [ ] Builds are reproducible from clean with the scripts.
- [ ] Binary smoke E2E is green on macOS and on clean Linux.
- [ ] Manual check on macOS: double-click `IRISFS.app` → tray icon, no Dock icon → mount and unmount works. (Gatekeeper: document `xattr -dr com.apple.quarantine IRISFS.app` for unsigned builds. Signing and notarization are out of scope.)
- [ ] Binary sizes and startup times recorded.

---

### Phase 10: Windows readiness (the user tests)

**Tasks:**
- `scripts/build-windows.ps1`: create a venv, install dependencies, run the unit tests, build with PyInstaller → `dist\irisfs.exe`.
- Optionally, a GitHub Actions workflow (`windows-latest`) that builds the `.exe` and runs the unit tests (not E2E, because WinFsp in CI is optional).
- Check the Windows-specific code paths with unit tests that use fake probes: WinFsp discovery via the registry (mock `winreg`), the drive-letter and directory mount-point rules, and the delete-and-recreate of an empty directory.
- Write `doc/WINDOWS_TEST_CHECKLIST.md`. It covers installing WinFsp, running Docker Desktop IRIS or pointing at a reachable IRIS, the build steps, and the manual test list (mirroring E2E-01…31 in Explorer terms plus the G8 GUI checklist). It also covers Explorer quirks (`desktop.ini`, `Thumbs.db`, the copy-in dialog), how to collect logs (log path from `irisfs doctor`), and a form for reporting results.

**GATE G10 (user)**
- [ ] The user runs the checklist on Windows. Issues found are fixed with a regression test added on the relevant layer, then macOS and Linux gates G5–G9 are re-run to confirm nothing broke.

---

### Phase 11: Headless CLI (added at the user's request)

**Goal:** everything the tray app does can also be done from the command line, for scripts and CI.

**Tasks:**
- Sub-commands: `profile list|show|create|update|delete`, `test`, `connect`, `disconnect`, `status`. Flag-style aliases (`--create-profile NAME`, `--connect NAME`, …) are rewritten to the sub-commands.
- `--batch`: never prompt, print one JSON object on stdout, and use fixed exit codes: 0 ok, 1 error, 2 usage or invalid, 3 login, 4 mount, 5 not found, 6 busy or connected.
- `connect` starts a **detached** worker that outlives the CLI. A worker started with `detach` ignores EOF on stdin.
- A **mount registry** (`<config>/mounts/<profile id>.json`, written by every worker once mounted and removed on exit; entries with a dead pid are ignored) is shared by the CLI and the tray:
  - the tray shows CLI mounts as mounted and can unmount them;
  - the CLI can disconnect tray mounts;
  - the tray's startup cleanup skips live mounts;
  - a clean external unmount (worker exit code 0) isn't reported as a crash.
- Passwords come from `--password-stdin`, `VALHALLISC_PASSWORD`, `--password` (discouraged), or an interactive prompt. They're never printed.

**Tests:**
- *Unit:* alias rewriting, profile CRUD through `main()`, JSON output and exit codes, validation field errors, not-found for every command, a connected profile can't be deleted, `connect` without FUSE (4) or without a password (3), and the registry (stale entries dropped).
- *E2E:*
  - full lifecycle: create → test → connect (the CLI exits, the mount stays) → read and import by copying → status → idempotent connect → delete refused → disconnect → delete;
  - wrong password → 3;
  - busy disconnect → 6, then `--force` works;
  - the tray manager sees a CLI mount and unmounts it;
  - the CLI disconnects a tray mount with no crash report.

**GATE G11**
- [ ] Unit and e2e CLI tests green on macOS and Linux (e2e run 3× in a row).
- [ ] The leftover check stays green (detached workers are always cleaned up).
- [ ] The binary smoke test runs the same flow with the packaged executable (`IRISFS_BIN`).

## 5. Future work (out of scope for v1, as `doc/idea` says)

- Creating new items without XML (for example writing `Foo.cls` as UDL and treating it as a create), with UDL mode as an optional second view (`<NS>/.udl/...`).
- Delete (`rm` → `DELETE` doc), rename, and atomic-save support for editors (temp file + rename).
- CSP and web application files, and a view by database instead of by namespace.
- A per-namespace filter in the profile (mount only selected namespaces).
- A server-side change notification instead of TTL polling.
- Code signing and notarization, installers (DMG, MSI, AppImage), and auto-start at login.

## 6. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Newer IRIS containers lack a built-in web server | Tests can't reach Atelier | Webgateway sidecar or pinned tag (S1) |
| Atelier has no direct XML export or import on the chosen version | The core feature is blocked | Conversion endpoints (`cvt`), the v7+ XML endpoints, or, with approval, a query-based helper (S2) |
| Community Edition license exhaustion | Random 503 errors | Cookie session reuse; concurrency cap of 4; test E2E-16 |
| macOS FUSE needs a kernel extension | Friction for users | Prefer FUSE-T; support macFUSE; `doctor` explains |
| Accurate `st_size` needs an export per file | Slow directory listings | Content cache; perf gate; `direct_io` fallback |
| Tray click events differ per OS | UI spec can't be met exactly | Combined-menu mode (UI-10), documented |
| Linux GNOME hides legacy tray icons | Icon invisible | Document the AppIndicator extension; `doctor` warns |
| `release` can't return errors | The user doesn't see failed imports | `flush` validation + notifications + log |
| Explorer and Finder metadata calls | Copies fail | No-op metadata ops (FS-12), junk handling (FS-11), E2E-28 |
| PyInstaller onefile start-up delay per worker | Slow mounts | Acceptable (~1–2 s); measured in G9; the macOS `.app` is onedir anyway |

## 7. Summary checklist of gates

| Gate | Proves | Where | Status (report) |
|---|---|---|---|
| G0 | IRIS container, XML export and import, FUSE, tray behaviour all feasible | macOS + Linux Docker | passed (`G0.md`) |
| G1 | Tooling, CI scripts, `doctor` | macOS + Linux | passed (`G1.md`) |
| G2 | Profiles and secrets | macOS + Linux | passed (`G2.md`) |
| G3 | Atelier client (contract-tested against the fake) | macOS + Linux + IRIS | passed (`G3.md`) |
| G4 | VirtualFS logic | macOS + Linux + IRIS | passed (`G4.md`) |
| G5 | Read-only mount end-to-end, performance | macOS + Linux + IRIS + FUSE | passed, including the manual Finder check (`G5.md`) |
| G6 | Import by copying end-to-end | macOS + Linux + IRIS + FUSE | passed, including the manual Finder drag-in (`G6.md`) |
| G7 | Multi-mount orchestration, crash and busy handling | macOS + Linux | passed (`G7.md`) |
| G8 | Tray and Profiles GUI | Linux (Xvfb) + macOS manual | passed: automated suites, plus the macOS manual checklist confirmed by the user (`G8-manual.md`); reworked after design review 1 (`design-review-1.md`) |
| G9 | Packaged binaries | macOS `.app` + CLI, Linux onefile | passed for macOS and Linux (`G9.md`) |
| G10 | Windows | the user | from source: mount, import and GUI verified (`G10-windows-first-run.md`); packaged `.exe` pending |
| G11 | Headless CLI (`--batch`, profiles, connect and disconnect, shared mount registry) | macOS + Linux | passed (`G11.md`) |
