"""Command line interface. With no sub-command the tray GUI starts."""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Callable, Sequence

from irisfs import APP_NAME, __version__, log
from irisfs.mount import fuselib

Handler = Callable[[argparse.Namespace], int]


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"'{args.command}' is not implemented yet", file=sys.stderr)
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    import platformdirs

    lines = [
        f"irisfs {__version__}",
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


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, Handler]]:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Mount InterSystems IRIS code as files.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command")
    handlers: dict[str, Handler] = {}

    sub.add_parser("gui", help="start the tray application (default)")
    handlers["gui"] = _not_implemented

    p = sub.add_parser("mount", help="mount a profile or an ad-hoc server in the foreground")
    p.add_argument("--profile", help="profile name")
    p.add_argument("--host")
    p.add_argument("--port", type=int, default=52773)
    p.add_argument("--user")
    p.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    p.add_argument("--mountpoint")
    p.add_argument("--read-only", action="store_true")
    handlers["mount"] = _not_implemented

    p = sub.add_parser("unmount", help="unmount a mount point")
    p.add_argument("path")
    p.add_argument("--force", action="store_true")
    handlers["unmount"] = _not_implemented

    sub.add_parser("worker", help=argparse.SUPPRESS)  # internal: mount worker, JSON lines on stdin/stdout
    handlers["worker"] = _not_implemented

    p = sub.add_parser("profiles", help="list configured profiles")
    handlers["profiles"] = _not_implemented

    sub.add_parser("doctor", help="print environment diagnostics")
    handlers["doctor"] = cmd_doctor
    return parser, handlers


def main(argv: Sequence[str] | None = None) -> int:
    parser, handlers = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "gui"
    return handlers[args.command](args)
