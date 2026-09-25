"""Tailored instructions for installing the FUSE layer on this machine.

Shown by the tray app at startup when no usable FUSE driver is found, and used by `doctor` and the CLI.
Pure logic with injectable probes, so every platform/distribution branch is unit-tested anywhere.
"""

from __future__ import annotations

import os
import platform
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from irisfs.mount import fuselib

Which = Callable[[str], str | None]


class _Detect:
    """Default for `library`: look for the installed FUSE library (None means 'no library')."""


DETECT = _Detect()

FUSE_T_URL = "https://www.fuse-t.org"
MACFUSE_URL = "https://macfuse.github.io"
WINFSP_URL = "https://winfsp.dev/rel/"
LIBFUSE_URL = "https://github.com/libfuse/libfuse"


@dataclass(frozen=True)
class Advice:
    """What to tell the user. `commands` are shell commands to copy; `links` are (label, url)."""

    title: str
    intro: str
    commands: tuple[str, ...] = ()
    links: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = field(default=())

    def as_text(self) -> str:
        parts = [self.title, "", self.intro]
        if self.commands:
            parts += ["", *(f"    {c}" for c in self.commands)]
        if self.links:
            parts += ["", *(f"{label}: {url}" for label, url in self.links)]
        if self.notes:
            parts += ["", *self.notes]
        return "\n".join(parts)


def read_os_release(path: str = "/etc/os-release") -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# (distribution ids, command) - matched against ID first, then ID_LIKE
_LINUX_COMMANDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("debian", "ubuntu", "linuxmint", "pop", "raspbian", "elementary", "kali", "zorin"),
        "sudo apt install fuse3",
    ),
    (("fedora", "rhel", "centos", "rocky", "almalinux", "ol", "amzn", "nobara"), "sudo dnf install fuse3"),
    (("arch", "manjaro", "endeavouros", "garuda", "artix"), "sudo pacman -S fuse3"),
    (("gentoo", "funtoo", "calculate"), "sudo emerge --ask sys-fs/fuse:3"),
    (
        ("opensuse", "opensuse-leap", "opensuse-tumbleweed", "suse", "sles", "sled"),
        "sudo zypper install fuse3",
    ),
    (("alpine", "postmarketos"), "sudo apk add fuse3"),
    (("void",), "sudo xbps-install -S fuse3"),
    (("solus",), "sudo eopkg install fuse3"),
    (
        ("nixos",),
        "add pkgs.fuse3 to environment.systemPackages in configuration.nix, then nixos-rebuild switch",
    ),
)


def linux_command(os_release: dict[str, str]) -> tuple[str | None, str]:
    """(command or None, distribution name) for installing fuse3."""
    distro = os_release.get("PRETTY_NAME") or os_release.get("NAME") or "your distribution"
    ids = [os_release.get("ID", "").lower(), *os_release.get("ID_LIKE", "").lower().split()]
    for candidate in ids:
        for family, command in _LINUX_COMMANDS:
            if candidate in family:
                return command, distro
    return None, distro


def advice(
    system: str | None = None,
    *,
    which: Which = shutil.which,
    os_release: dict[str, str] | None = None,
    library: fuselib.FuseLibrary | _Detect | None = DETECT,
    dev_fuse_exists: bool | None = None,
) -> Advice | None:
    """Advice for this machine, or None when a usable FUSE layer is present."""
    system = system or platform.system()
    lib = fuselib.find_library(system) if isinstance(library, _Detect) else library

    if system == "Darwin":
        return _macos(lib, which)
    if system == "Windows":
        if lib is not None and not lib.problem:
            return None
        commands = ("winget install WinFsp.WinFsp",) if which("winget") else ()
        return Advice(
            "WinFsp is needed",
            "ValhallISC shows IRIS servers as folders through WinFsp, the Windows FUSE driver. "
            "Install it, then click Check again (or restart ValhallISC)."
            + (" With winget:" if commands else " Download the installer (.msi) from the WinFsp site."),
            commands,
            (("WinFsp download page", WINFSP_URL),),
        )
    # Linux and other POSIX
    has_dev = os.path.exists("/dev/fuse") if dev_fuse_exists is None else dev_fuse_exists
    if lib is not None and not lib.problem:
        if has_dev:
            return None
        return Advice(
            "The FUSE kernel module is not loaded",
            "libfuse is installed but /dev/fuse does not exist. Load the module (and make it load at boot):",
            ("sudo modprobe fuse", "echo fuse | sudo tee /etc/modules-load.d/fuse.conf"),
            notes=("In a container, start it with --device /dev/fuse --cap-add SYS_ADMIN.",),
        )
    command, distro = linux_command(os_release if os_release is not None else read_os_release())
    if command is None:
        return Advice(
            "FUSE 3 is needed",
            "ValhallISC needs libfuse 3 (usually the package 'fuse3'). "
            f"Install it with the package manager of {distro}, then click Check again.",
            links=(("libfuse project", LIBFUSE_URL),),
        )
    return Advice(
        "FUSE 3 is needed",
        f"ValhallISC shows IRIS servers as folders through FUSE. On {distro} install it with:",
        (command,),
        links=(("libfuse project", LIBFUSE_URL),),
    )


def _macos(lib: fuselib.FuseLibrary | None, which: Which) -> Advice | None:
    if lib is not None and not lib.problem:
        return None
    brew = which("brew") or next(
        (p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if Path(p).exists()), None
    )
    port = which("port") or ("/opt/local/bin/port" if Path("/opt/local/bin/port").exists() else None)
    links = (("FUSE-T (recommended, no kernel extension)", FUSE_T_URL), ("macFUSE", MACFUSE_URL))
    notes = (
        "macFUSE: after installing, allow its system extension in System Settings → Privacy & Security "
        "(Apple silicon may ask to restart). FUSE-T needs no approval.",
    )
    if lib is not None and lib.problem:
        return Advice(
            "macFUSE is not fully installed",
            f"A FUSE library was found ({lib.path}) but {lib.problem}. Reinstall macFUSE or install FUSE-T:",
            _mac_commands(brew, port),
            links,
            notes,
        )
    how = "With Homebrew:" if brew else "With MacPorts:" if port else "Download and run an installer:"
    return Advice(
        "A FUSE driver is needed",
        f"ValhallISC shows IRIS servers as folders through FUSE. Install FUSE-T or macFUSE. {how}",
        _mac_commands(brew, port),
        links,
        notes,
    )


def _mac_commands(brew: str | None, port: str | None) -> tuple[str, ...]:
    if brew:
        return ("brew install macos-fuse-t/cask/fuse-t", "# or: brew install --cask macfuse")
    if port:
        return ("sudo port install macfuse",)
    return ()
