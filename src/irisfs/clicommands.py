"""Headless commands: everything the tray app does, scriptable (plan gate G11).

    valhallisc [--batch] profile list|show|create|update|delete ...
    valhallisc [--batch] test NAME | connect NAME | disconnect NAME | status [NAME]

--batch: never prompt, print one JSON object on stdout, and use the exit codes below.
Flag-style aliases (`--create-profile NAME`, `--connect NAME`, ...) are rewritten to these sub-commands.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import sys
from collections.abc import Sequence
from typing import Any

from irisfs.config import mountpoint, secrets
from irisfs.config.profile import Profile, ProfileValidationError
from irisfs.config.store import ProfileStore, default_config_dir
from irisfs.mount import protocol, registry

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # bad arguments or invalid profile values
EXIT_LOGIN = 3  # wrong credentials / server unreachable
EXIT_MOUNT = 4  # FUSE missing, bad mount folder, mount failed or timed out
EXIT_NOT_FOUND = 5  # unknown profile
EXIT_BUSY = 6  # folder in use, or profile connected when it must not be

_LOGIN_CODES = {protocol.AUTH_FAILED, protocol.UNREACHABLE, protocol.SERVER_ERROR}

# flag-style alias -> (sub-command words, takes a NAME argument)
ALIASES: dict[str, tuple[list[str], bool]] = {
    "--list-profiles": (["profile", "list"], False),
    "--show-profile": (["profile", "show"], True),
    "--create-profile": (["profile", "create"], True),
    "--update-profile": (["profile", "update"], True),
    "--delete-profile": (["profile", "delete"], True),
    "--test": (["test"], True),
    "--test-connection": (["test"], True),
    "--connect": (["connect"], True),
    "--disconnect": (["disconnect"], True),
    "--status": (["status"], False),
}
GLOBAL_FLAGS = ("--batch", "-v", "--verbose")


def rewrite_aliases(argv: Sequence[str]) -> list[str]:
    """`--batch --create-profile X --host h` -> `--batch profile create X --host h`. Global flags may appear
    anywhere; they are moved in front of the sub-command."""
    args = list(argv)
    globals_ = [a for a in args if a in GLOBAL_FLAGS]
    rest = [a for a in args if a not in GLOBAL_FLAGS]
    for i, token in enumerate(rest):
        flag, _, inline = token.partition("=")
        if flag not in ALIASES:
            continue
        words, takes_name = ALIASES[flag]
        before, after = rest[:i], rest[i + 1 :]
        name: list[str] = []
        if takes_name:
            if inline:
                name = [inline]
            elif after and not after[0].startswith("-"):
                name, after = [after[0]], after[1:]
        return [*globals_, *words, *name, *before, *after]
    return [*globals_, *rest]


class Output:
    def __init__(self, batch: bool) -> None:
        self.batch = batch

    def ok(self, data: dict[str, Any], human: str) -> int:
        if self.batch:
            print(json.dumps({"ok": True, **data}, ensure_ascii=False, default=str))
        elif human:
            print(human)
        return EXIT_OK

    def fail(self, code: int, error: str, message: str, **extra: Any) -> int:
        if self.batch:
            print(json.dumps({"ok": False, "error": error, "message": message, **extra}, ensure_ascii=False))
        else:
            print(f"error: {message}", file=sys.stderr)
        return code


def open_store() -> ProfileStore:
    config_dir = default_config_dir()
    return ProfileStore(config_dir / "profiles.json", secrets=secrets.default_store(config_dir))


def _registry_dir(store: ProfileStore) -> Any:
    return registry.registry_dir(store.path.parent)


def _find(store: ProfileStore, name: str, out: Output) -> Profile | int:
    profile = store.find_by_name(name)
    if profile is None:
        return out.fail(EXIT_NOT_FOUND, "not_found", f"no profile named {name!r}")
    return profile


def _public(profile: Profile, store: ProfileStore) -> dict[str, Any]:
    data = profile.to_dict()
    data["has_password"] = store.password(profile.id) is not None
    entry = registry.get(_registry_dir(store), profile.id)
    data["connected"] = entry is not None
    if entry is not None:
        data["connection"] = {"pid": entry.pid, "owner": entry.owner, "mountpoint": entry.mountpoint}
    return data


def _read_password(args: argparse.Namespace, out: Output, *, required: bool) -> str | None:
    if getattr(args, "password_stdin", False):
        return sys.stdin.readline().rstrip("\r\n")
    if getattr(args, "password", None) is not None:
        return str(args.password)
    env = os.environ.get("VALHALLISC_PASSWORD")
    if env is not None:
        return env
    if required and not out.batch and sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass("IRIS password: ")
    return None


# ---- profile commands ------------------------------------------------------------------------
def profile_list(args: argparse.Namespace, out: Output) -> int:
    store = open_store()
    profiles = [_public(p, store) for p in sorted(store.profiles(), key=lambda p: p.name.casefold())]
    lines = [
        f"{'●' if p['connected'] else '○'} {p['name']}: {p['username']}@{p['host']}:{p['port']}"
        f" -> {p['mount_point']}" + (" (read-only)" if p["read_only"] else "")
        for p in profiles
    ]
    return out.ok({"profiles": profiles}, "\n".join(lines) or "no profiles")


def profile_show(args: argparse.Namespace, out: Output) -> int:
    store = open_store()
    found = _find(store, args.name, out)
    if isinstance(found, int):
        return found
    data = _public(found, store)
    return out.ok({"profile": data}, "\n".join(f"{k}: {v}" for k, v in data.items()))


def _apply_options(profile: Profile, args: argparse.Namespace) -> None:
    mapping = {
        "host": "host",
        "port": "port",
        "user": "username",
        "mountpoint": "mount_point",
        "read_only": "read_only",
        "https": "https",
        "verify_tls": "verify_tls",
        "prefix": "path_prefix",
        "show_system": "show_system",
        "compile": "compile_on_import",
        "compile_flags": "compile_flags",
    }
    for arg, attr in mapping.items():
        value = getattr(args, arg, None)
        if value is not None:
            setattr(profile, attr, value)


def profile_create(args: argparse.Namespace, out: Output) -> int:
    store = open_store()
    if store.find_by_name(args.name) is not None:
        return out.fail(EXIT_USAGE, "exists", f"a profile named {args.name!r} already exists")
    profile = Profile(name=args.name, host="", username="", mount_point="")
    _apply_options(profile, args)
    if not profile.mount_point:
        from irisfs import APP_NAME

        profile.mount_point = os.path.join(os.path.expanduser("~"), APP_NAME, args.name)
    password = _read_password(args, out, required=True)
    try:
        store.add(profile, password=password)
    except ProfileValidationError as e:
        return out.fail(EXIT_USAGE, "invalid", str(e), fields=e.errors)
    warning = "" if password is not None else " (no password saved: use `profile update --password-stdin`)"
    return out.ok({"profile": _public(store.get(profile.id), store)}, f"created {profile.name}{warning}")


def profile_update(args: argparse.Namespace, out: Output) -> int:
    store = open_store()
    found = _find(store, args.name, out)
    if isinstance(found, int):
        return found
    _apply_options(found, args)
    if args.rename:
        found.name = args.rename
    password = _read_password(args, out, required=False)
    try:
        store.update(found, password=password)
    except ProfileValidationError as e:
        return out.fail(EXIT_USAGE, "invalid", str(e), fields=e.errors)
    except RuntimeError as e:  # ProfileActiveError
        return out.fail(EXIT_BUSY, "connected", str(e))
    return out.ok({"profile": _public(store.get(found.id), store)}, f"updated {found.name}")


def profile_delete(args: argparse.Namespace, out: Output) -> int:
    store = open_store()
    found = _find(store, args.name, out)
    if isinstance(found, int):
        return found
    if registry.get(_registry_dir(store), found.id) is not None:
        return out.fail(EXIT_BUSY, "connected", f"{found.name!r} is connected; disconnect it first")
    interactive = not out.batch and not args.yes and sys.stdin is not None and sys.stdin.isatty()
    if interactive and input(f"Delete profile {found.name!r}? [y/N] ").strip().lower() not in ("y", "yes"):
        return out.fail(EXIT_ERROR, "cancelled", "cancelled")
    store.delete(found.id)
    return out.ok({"deleted": found.name}, f"deleted {found.name}")


# ---- connection commands -------------------------------------------------------------------
def test(args: argparse.Namespace, out: Output) -> int:
    from irisfs.atelier.client import AtelierClient
    from irisfs.atelier.errors import AtelierError

    store = open_store()
    found = _find(store, args.name, out)
    if isinstance(found, int):
        return found
    password = _read_password(args, out, required=False)
    password = password if password is not None else (store.password(found.id) or "")
    try:
        with AtelierClient(
            found.base_url, found.username, password, verify_tls=found.verify_tls, timeout=10
        ) as c:
            info = c.server_info(refresh=True)
    except AtelierError as e:
        return out.fail(EXIT_LOGIN, type(e).__name__, e.message)
    data = {"version": info.version, "api": info.api, "namespaces": list(info.namespaces)}
    return out.ok(data, f"OK: {info.version}\nnamespaces: {', '.join(info.namespaces)}")


def connect(args: argparse.Namespace, out: Output) -> int:
    from irisfs.mount import detached, fuselib

    store = open_store()
    found = _find(store, args.name, out)
    if isinstance(found, int):
        return found
    reg = _registry_dir(store)
    entry = registry.get(reg, found.id)
    if entry is not None:
        return out.ok(
            {"profile": found.name, "mountpoint": entry.mountpoint, "pid": entry.pid, "already": True},
            f"{found.name} is already connected at {entry.mountpoint}",
        )
    if fuselib.find_library() is None:
        return out.fail(EXIT_MOUNT, protocol.FUSE_MISSING, f"no FUSE driver installed. {fuselib.hint()}")
    password = store.password(found.id)
    if password is None:
        return out.fail(EXIT_LOGIN, "no_password", f"no password saved for {found.name!r}")
    try:
        mountpoint.ensure_folder(found.mount_point, platform.system())
    except OSError as e:
        return out.fail(EXIT_MOUNT, protocol.MOUNTPOINT_INVALID, f"cannot create {found.mount_point}: {e}")
    event = detached.connect(found, password, reg, wait=args.wait)
    if event.get("event") == "mounted":
        return out.ok(
            {"profile": found.name, "mountpoint": event.get("mountpoint"), "pid": event.get("pid")},
            f"connected {found.name} at {event.get('mountpoint')}",
        )
    code = str(event.get("code", "MOUNT_FAILED"))
    exit_code = EXIT_LOGIN if code in _LOGIN_CODES else EXIT_MOUNT
    return out.fail(exit_code, code, str(event.get("message", "mount failed")))


def disconnect(args: argparse.Namespace, out: Output) -> int:
    from irisfs.mount import detached

    store = open_store()
    found = _find(store, args.name, out)
    if isinstance(found, int):
        return found
    entry = registry.get(_registry_dir(store), found.id)
    if entry is None:
        return out.ok({"profile": found.name, "already": True}, f"{found.name} is not connected")
    result = detached.disconnect(entry, force=args.force)
    if result.ok:
        return out.ok({"profile": found.name}, f"disconnected {found.name}")
    if result.busy:
        return out.fail(EXIT_BUSY, protocol.BUSY, f"{entry.mountpoint} is in use (use --force)")
    return out.fail(EXIT_MOUNT, protocol.MOUNT_FAILED, result.message or "unmount failed")


def status(args: argparse.Namespace, out: Output) -> int:
    store = open_store()
    entries = registry.entries(_registry_dir(store))
    rows = []
    for p in sorted(store.profiles(), key=lambda p: p.name.casefold()):
        if args.name and p.name.casefold() != args.name.casefold():
            continue
        entry = entries.get(p.id)
        rows.append(
            {
                "profile": p.name,
                "connected": entry is not None,
                "mountpoint": p.mount_point,
                **({"pid": entry.pid, "owner": entry.owner} if entry else {}),
            }
        )
    if args.name and not rows:
        return out.fail(EXIT_NOT_FOUND, "not_found", f"no profile named {args.name!r}")
    human = "\n".join(
        f"{'●' if r['connected'] else '○'} {r['profile']}: "
        + (
            f"connected at {r['mountpoint']} (pid {r['pid']}, by {r['owner']})"
            if r["connected"]
            else "not connected"
        )
        for r in rows
    )
    return out.ok({"profiles": rows}, human or "no profiles")


# ---- parser --------------------------------------------------------------------------------
def _profile_options(p: argparse.ArgumentParser, *, creating: bool) -> None:
    p.add_argument("--host", required=creating, help="IRIS host name or IP address")
    p.add_argument("--port", type=int, help="web server port (default 52773)")
    p.add_argument("--user", required=creating, help="IRIS user name")
    p.add_argument(
        "--mountpoint", help="local folder (default ~/ValhallISC/NAME); Windows: X: or a missing folder"
    )
    p.add_argument("--password-stdin", action="store_true", help="read the password from stdin (recommended)")
    p.add_argument("--password", help="password (visible to other local users: prefer --password-stdin)")
    bool_opt = argparse.BooleanOptionalAction
    p.add_argument("--read-only", action=bool_opt, default=None, help="mount read-only")
    p.add_argument("--https", action=bool_opt, default=None, help="use HTTPS")
    p.add_argument("--verify-tls", action=bool_opt, default=None, help="verify the TLS certificate")
    p.add_argument("--prefix", help="URL path prefix of a web gateway, e.g. /iris")
    p.add_argument("--show-system", action=bool_opt, default=None, help="show system/library items")
    p.add_argument("--compile", action=bool_opt, default=None, help="compile after import")
    p.add_argument("--compile-flags", help="compile flags (default cuk)")


def add_commands(sub: Any, handlers: dict[str, Any]) -> None:
    prof = sub.add_parser("profile", help="manage profiles (list, show, create, update, delete)")
    psub = prof.add_subparsers(dest="profile_command", required=True)
    psub.add_parser("list", help="list profiles")
    p = psub.add_parser("show", help="show one profile")
    p.add_argument("name")
    p = psub.add_parser("create", help="create a profile")
    p.add_argument("name")
    _profile_options(p, creating=True)
    p = psub.add_parser("update", help="change a profile (only the given options)")
    p.add_argument("name")
    p.add_argument("--rename", help="new profile name")
    _profile_options(p, creating=False)
    p = psub.add_parser("delete", help="delete a profile")
    p.add_argument("name")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    handlers["profile"] = lambda a, o: {
        "list": profile_list,
        "show": profile_show,
        "create": profile_create,
        "update": profile_update,
        "delete": profile_delete,
    }[a.profile_command](a, o)

    p = sub.add_parser("test", help="test the connection of a profile")
    p.add_argument("name")
    p.add_argument(
        "--password-stdin", action="store_true", help="test with this password instead of the saved one"
    )
    handlers["test"] = test

    p = sub.add_parser("connect", help="mount a profile in the background")
    p.add_argument("name")
    p.add_argument("--wait", type=float, default=60.0, help="seconds to wait for the mount (default 60)")
    handlers["connect"] = connect

    p = sub.add_parser("disconnect", help="unmount a connected profile (also ones mounted by the tray app)")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="unmount even if the folder is in use")
    handlers["disconnect"] = disconnect

    p = sub.add_parser("status", help="show which profiles are connected")
    p.add_argument("name", nargs="?")
    handlers["status"] = status
