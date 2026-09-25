"""Command line interface. With no sub-command the tray GUI starts."""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from irisfs import APP_NAME, CLI_NAME, GIT_SHA, __version__, log
from irisfs.mount import fuselib

if TYPE_CHECKING:
    from irisfs.config.store import ProfileStore

Handler = Callable[[argparse.Namespace], int]


def cmd_doctor(args: argparse.Namespace) -> int:
    import platformdirs

    lines = [
        f"{APP_NAME} {__version__}",
        f"os: {platform.system()} {platform.release()} ({platform.machine()})",
        f"python: {sys.version.split()[0]} ({sys.executable}){' frozen' if fuselib.is_frozen() else ''}",
        f"config dir: {platformdirs.user_config_dir(APP_NAME, appauthor=False)}",
        f"log dir: {log.log_dir()}",
    ]
    ok = True
    lib = fuselib.find_library()
    if lib is None:
        ok = False
        lines.append(f"fuse: NOT FOUND - {fuselib.hint()}")
    else:
        lines.append(
            f"fuse: {lib.kind} at {lib.path}" + (f" (WARNING: {lib.problem})" if lib.problem else "")
        )
        try:
            mod = fuselib.ensure_loaded()
            lines.append(f"fuse version: {mod.fuse_version_major}.{mod.fuse_version_minor}")
        except fuselib.FuseNotFoundError as e:
            ok = False
            lines.append(f"fuse load error: {e}")
    try:
        import keyring

        lines.append(f"keyring: {keyring.get_keyring().__class__.__name__}")
    except Exception as e:  # keyring backends can fail in many ways; doctor just reports
        lines.append(f"keyring: unavailable ({e})")
    try:
        import wx

        lines.append(f"wxPython: {wx.version()}")
    except ImportError:
        lines.append("wxPython: not installed (GUI unavailable)")
    print("\n".join(lines))
    return 0 if ok else 1


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from irisfs.gui.app import main as gui_main
    except ImportError as e:
        print(f"The GUI needs wxPython: {e}", file=sys.stderr)
        return 2
    return gui_main()


def cmd_worker(args: argparse.Namespace) -> int:
    from irisfs.mount import worker

    return worker.main()


def _store() -> ProfileStore:
    from irisfs.config import secrets
    from irisfs.config.store import ProfileStore, default_config_dir

    config_dir = default_config_dir()
    return ProfileStore(config_dir / "profiles.json", secrets=secrets.default_store(config_dir))


def cmd_mount(args: argparse.Namespace) -> int:
    import json

    from irisfs.config.profile import Profile
    from irisfs.mount.worker import Worker, WorkerConfig

    log.setup("mount", verbose=args.verbose)
    if args.profile:
        store = _store()
        found = store.find_by_name(args.profile)
        if found is None:
            print(f"no profile named {args.profile!r}", file=sys.stderr)
            return 2
        profile = found
        if args.mountpoint:
            profile.mount_point = args.mountpoint
        if args.read_only:
            profile.read_only = True
        password = (
            sys.stdin.readline().rstrip("\n") if args.password_stdin else (store.password(profile.id) or "")
        )
    else:
        if not (args.host and args.user and args.mountpoint):
            print("either --profile or --host, --user and --mountpoint are required", file=sys.stderr)
            return 2
        profile = Profile(
            name="adhoc",
            host=args.host,
            port=args.port,
            username=args.user,
            mount_point=args.mountpoint,
            read_only=args.read_only,
        )
        password = sys.stdin.readline().rstrip("\n") if args.password_stdin else ""

    def emit(event: dict[str, object]) -> None:
        print(json.dumps(event, ensure_ascii=False), flush=True)

    return Worker(WorkerConfig(profile=profile, password=password), emit).run()


def cmd_unmount(args: argparse.Namespace) -> int:
    from irisfs.mount.unmount import unmount

    result = unmount(args.path, platform.system(), force=args.force)
    if not result.ok:
        print(f"unmount failed: {result.message}", file=sys.stderr)
        return 1
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    store = _store()
    if store.load_warning:
        print(f"warning: {store.load_warning}", file=sys.stderr)
    for p in store.profiles():
        flags = " (read-only)" if p.read_only else ""
        print(f"{p.name}: {p.username}@{p.base_url} -> {p.mount_point}{flags}")
    return 0


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, Handler]]:
    parser = argparse.ArgumentParser(prog=CLI_NAME, description="Mount InterSystems IRIS code as files.")
    version = f"%(prog)s {__version__}" + (f" ({GIT_SHA})" if GIT_SHA else "")
    parser.add_argument("--version", action="version", version=version)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command")
    handlers: dict[str, Handler] = {}

    sub.add_parser("gui", help="start the tray application (default)")
    handlers["gui"] = cmd_gui

    p = sub.add_parser("mount", help="mount a profile or an ad-hoc server in the foreground")
    p.add_argument("--profile", help="profile name")
    p.add_argument("--host")
    p.add_argument("--port", type=int, default=52773)
    p.add_argument("--user")
    p.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    p.add_argument("--mountpoint")
    p.add_argument("--read-only", action="store_true")
    handlers["mount"] = cmd_mount

    p = sub.add_parser("unmount", help="unmount a mount point")
    p.add_argument("path")
    p.add_argument("--force", action="store_true")
    handlers["unmount"] = cmd_unmount

    sub.add_parser("worker", help=argparse.SUPPRESS)  # internal: mount worker, JSON lines on stdin/stdout
    handlers["worker"] = cmd_worker

    p = sub.add_parser("profiles", help="list configured profiles")
    handlers["profiles"] = cmd_profiles

    sub.add_parser("doctor", help="print environment diagnostics")
    handlers["doctor"] = cmd_doctor
    return parser, handlers


def main(argv: Sequence[str] | None = None) -> int:
    parser, handlers = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "gui"
    return handlers[args.command](args)
