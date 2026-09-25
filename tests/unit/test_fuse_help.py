from pathlib import Path

import pytest

from irisfs.mount import fuse_help
from irisfs.mount.fuselib import FuseLibrary

MACFUSE = FuseLibrary("macFUSE", "/usr/local/lib/libfuse.2.dylib")
FUSE3 = FuseLibrary("libfuse3", "libfuse3.so.3")


def which_only(*tools: str):  # type: ignore[no-untyped-def]
    return lambda name: f"/usr/bin/{name}" if name in tools else None


@pytest.fixture(autouse=True)
def no_default_brew_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    # the macOS branch also probes /opt/homebrew/bin/brew etc.: make the tests independent of this machine
    real_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: False if str(self).endswith(("/bin/brew", "/bin/port")) else real_exists(self),
    )


# ---- macOS ---------------------------------------------------------------------------------------
def test_macos_ok_returns_none() -> None:
    assert fuse_help.advice("Darwin", which=which_only(), library=MACFUSE) is None


def test_macos_homebrew() -> None:
    a = fuse_help.advice("Darwin", which=which_only("brew"), library=None)
    assert a is not None and "Homebrew" in a.intro
    assert a.commands[0] == "brew install macos-fuse-t/cask/fuse-t" and "macfuse" in a.commands[1]
    assert (label for label, _ in a.links)
    assert {url for _, url in a.links} == {fuse_help.FUSE_T_URL, fuse_help.MACFUSE_URL}


def test_macos_macports() -> None:
    a = fuse_help.advice("Darwin", which=which_only("port"), library=None)
    assert a is not None and "MacPorts" in a.intro and a.commands == ("sudo port install macfuse",)


def test_macos_direct_download() -> None:
    a = fuse_help.advice("Darwin", which=which_only(), library=None)
    assert a is not None and a.commands == () and "installer" in a.intro and len(a.links) == 2


def test_macos_half_installed_macfuse() -> None:
    broken = FuseLibrary(
        "macFUSE", "/usr/local/lib/libfuse.2.dylib", "/Library/Filesystems/macfuse.fs is missing"
    )
    a = fuse_help.advice("Darwin", which=which_only("brew"), library=broken)
    assert a is not None and "not fully installed" in a.title and "macfuse.fs" in a.intro


# ---- Windows -------------------------------------------------------------------------------------
def test_windows_with_winget() -> None:
    a = fuse_help.advice("Windows", which=which_only("winget"), library=None)
    assert (
        a is not None
        and a.commands == ("winget install WinFsp.WinFsp",)
        and a.links[0][1] == fuse_help.WINFSP_URL
    )


def test_windows_without_winget() -> None:
    a = fuse_help.advice("Windows", which=which_only(), library=None)
    assert a is not None and a.commands == () and "installer" in a.intro


def test_windows_ok() -> None:
    assert fuse_help.advice("Windows", which=which_only(), library=FuseLibrary("WinFsp", "x.dll")) is None


# ---- Linux ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("os_release", "command"),
    [
        ({"ID": "ubuntu"}, "sudo apt install fuse3"),
        ({"ID": "debian"}, "sudo apt install fuse3"),
        ({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}, "sudo apt install fuse3"),
        ({"ID": "fedora"}, "sudo dnf install fuse3"),
        ({"ID": "rhel", "ID_LIKE": "fedora"}, "sudo dnf install fuse3"),
        ({"ID": "rocky", "ID_LIKE": "rhel centos fedora"}, "sudo dnf install fuse3"),
        ({"ID": "almalinux", "ID_LIKE": "rhel centos fedora"}, "sudo dnf install fuse3"),
        ({"ID": "arch"}, "sudo pacman -S fuse3"),
        ({"ID": "manjaro", "ID_LIKE": "arch"}, "sudo pacman -S fuse3"),
        ({"ID": "gentoo"}, "sudo emerge --ask sys-fs/fuse:3"),
        ({"ID": "opensuse-tumbleweed", "ID_LIKE": "opensuse suse"}, "sudo zypper install fuse3"),
        ({"ID": "sles"}, "sudo zypper install fuse3"),
        ({"ID": "alpine"}, "sudo apk add fuse3"),
        ({"ID": "void"}, "sudo xbps-install -S fuse3"),
        ({"ID": "somethingnew", "ID_LIKE": "debian"}, "sudo apt install fuse3"),
    ],
)
def test_linux_distributions(os_release: dict[str, str], command: str) -> None:
    a = fuse_help.advice("Linux", os_release={"PRETTY_NAME": "Test OS", **os_release}, library=None)
    assert a is not None and a.commands == (command,) and "Test OS" in a.intro


def test_linux_unknown_distribution() -> None:
    a = fuse_help.advice("Linux", os_release={"ID": "exotic", "NAME": "Exotic"}, library=None)
    assert a is not None and a.commands == () and "fuse3" in a.intro and "Exotic" in a.intro


def test_linux_missing_kernel_module() -> None:
    a = fuse_help.advice("Linux", library=FUSE3, dev_fuse_exists=False)
    assert a is not None and "modprobe" in a.commands[0]


def test_linux_ok() -> None:
    assert fuse_help.advice("Linux", library=FUSE3, dev_fuse_exists=True) is None


def test_read_os_release(tmp_path: Path) -> None:
    f = tmp_path / "os-release"
    f.write_text('NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu 24.04 LTS"\n# comment\n')
    assert fuse_help.read_os_release(str(f)) == {
        "NAME": "Ubuntu",
        "ID": "ubuntu",
        "ID_LIKE": "debian",
        "PRETTY_NAME": "Ubuntu 24.04 LTS",
    }
    assert fuse_help.read_os_release(str(tmp_path / "missing")) == {}


def test_as_text_contains_everything() -> None:
    a = fuse_help.advice("Darwin", which=which_only("brew"), library=None)
    assert a is not None
    text = a.as_text()
    assert a.title in text and a.commands[0] in text and fuse_help.FUSE_T_URL in text and "Privacy" in text
