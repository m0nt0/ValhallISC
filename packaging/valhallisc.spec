# PyInstaller spec for ValhallISC.  Build with the scripts in scripts/ (they write irisfs/_build.py first).
#   macOS:   dist/ValhallISC.app (menu-bar app, LSUIElement); the CLI is the app's own executable (ADR-009:
#            onefile binaries re-extract on every launch and macOS re-scans them: 10-100 s startups)
#   Linux:   dist/valhallisc (onefile: tray app when run without arguments, CLI otherwise)
#   Windows: dist/ValhallISC.exe (onefile, windowed) + dist/valhallisc-cli.exe (onefile, console CLI)
# Names must differ case-insensitively (macOS APFS and NTFS are case-insensitive).
# The FUSE layer (macFUSE/FUSE-T, fuse3, WinFsp) is a system prerequisite and is not bundled.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
ASSETS = ROOT / "assets"
import tomllib

VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]  # single source

hiddenimports = (
    ["irisfs._build", "mfusepy"]
    + collect_submodules("irisfs")
    + collect_submodules("keyring.backends")
)
if sys.platform == "darwin":
    hiddenimports += ["AppKit", "Foundation"]
if sys.platform == "win32":
    hiddenimports += ["win32timezone", "winreg"]

datas = [
    (str(ROOT / "src" / "irisfs" / "gui" / "icons"), "irisfs/gui/icons"),
    (str(ROOT / "LICENSE"), "."),  # GPL v3: the license travels with the binaries
    (str(ROOT / "doc" / "THIRD_PARTY.md"), "."),
]
datas += copy_metadata("keyring")  # keyring discovers its backends through entry points

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "hypothesis", "mypy", "ruff", "PIL"],
    noarchive=False,
)
# Never bundle the FUSE layer: it is a system prerequisite that must match the installed driver
# (kernel extension / FSKit / WinFsp service), and macFUSE's libraries are not ours to redistribute.
# irisfs.mount.fuselib loads the installed library by absolute path at runtime.
_FUSE_PARTS = ("libfuse", "mfmount", "winfsp", "macfuse", "fuse-t")  # (libiconv stays: Python needs it)


def _is_fuse(entry: tuple[str, str, str]) -> bool:
    # match the destination name AND the source path: libswiftCompatibilitySpan.dylib comes from inside
    # macfuse.fs/…/MFMount.framework and its own name doesn't mention fuse
    dest, source = entry[0].lower(), str(entry[1]).lower()
    return any(part in dest or part in source for part in _FUSE_PARTS)


a.binaries = [b for b in a.binaries if not _is_fuse(b)]
a.datas = [d for d in a.datas if not _is_fuse(d)]
pyz = PYZ(a.pure)  # noqa: F821


def onefile(name: str, *, console: bool, icon: str | None) -> object:
    return EXE(  # noqa: F821
        pyz, a.scripts, a.binaries, a.datas, [],
        name=name, console=console, icon=icon, upx=False, strip=False,
        runtime_tmpdir=None, disable_windowed_traceback=False,
    )


if sys.platform == "darwin":
    # Menu-bar app: onedir bundle (PyInstaller discourages onefile .app bundles)
    app_exe = EXE(  # noqa: F821
        pyz, a.scripts, [], exclude_binaries=True, name="ValhallISC",
        console=False, icon=str(ASSETS / "valhallisc.icns"), upx=False,
    )
    # onedir folder name must not collide with dist/valhallisc on case-insensitive APFS
    coll = COLLECT(app_exe, a.binaries, a.datas, name="ValhallISC-onedir", upx=False)  # noqa: F821
    BUNDLE(  # noqa: F821
        coll,
        name="ValhallISC.app",
        icon=str(ASSETS / "valhallisc.icns"),
        bundle_identifier="org.valhallisc.app",
        version=VERSION,
        info_plist={
            "CFBundleName": "ValhallISC",
            "CFBundleDisplayName": "ValhallISC",
            "CFBundleShortVersionString": VERSION,
            "LSUIElement": True,  # menu-bar only: no Dock icon
            "NSHighResolutionCapable": True,
        },
    )
elif sys.platform == "win32":
    onefile("ValhallISC", console=False, icon=str(ASSETS / "valhallisc.ico"))
    onefile("valhallisc-cli", console=True, icon=str(ASSETS / "valhallisc.ico"))
else:
    onefile("valhallisc", console=True, icon=None)
