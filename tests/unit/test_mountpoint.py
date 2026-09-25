from dataclasses import dataclass, field

import pytest

from irisfs.config import mountpoint as mp
from irisfs.config.mountpoint import MountPointError


@dataclass
class FakeProbe:
    dirs: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    nonempty: set[str] = field(default_factory=set)
    mounts: set[str] = field(default_factory=set)
    readonly: set[str] = field(default_factory=set)
    removed: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    def exists(self, path: str) -> bool:
        return path in self.dirs or path in self.files

    def is_dir(self, path: str) -> bool:
        return path in self.dirs

    def is_empty_dir(self, path: str) -> bool:
        return path in self.dirs and path not in self.nonempty

    def is_mount(self, path: str) -> bool:
        return path in self.mounts

    def writable(self, path: str) -> bool:
        return path not in self.readonly

    def rmdir(self, path: str) -> None:
        self.dirs.discard(path)
        self.removed.append(path)

    def mkdir(self, path: str) -> None:
        self.dirs.add(path)
        self.created.append(path)


# ---- POSIX -----------------------------------------------------------------------------------
def test_posix_ok() -> None:
    mp.validate("/mnt/iris", "Linux", probe=FakeProbe(dirs={"/mnt/iris"}))


@pytest.mark.parametrize(
    ("probe", "path", "message"),
    [
        (FakeProbe(), "", "No mount directory"),
        (FakeProbe(), "relative", "not an absolute path"),
        (FakeProbe(), "/mnt/iris", "does not exist"),
        (FakeProbe(files={"/mnt/iris"}), "/mnt/iris", "not a directory"),
        (FakeProbe(dirs={"/mnt/iris"}, mounts={"/mnt/iris"}), "/mnt/iris", "already a mount point"),
        (FakeProbe(dirs={"/mnt/iris"}, nonempty={"/mnt/iris"}), "/mnt/iris", "not empty"),
        (FakeProbe(dirs={"/mnt/iris"}, readonly={"/mnt/iris"}), "/mnt/iris", "not writable"),
    ],
)
def test_posix_rules(probe: FakeProbe, path: str, message: str) -> None:
    with pytest.raises(MountPointError, match=message):
        mp.validate(path, "Darwin", probe=probe)


@pytest.mark.parametrize("path", ["/mnt/iris", "/mnt/iris/", "/mnt/iris/sub", "/mnt"])
def test_posix_overlap_with_active_mount(path: str) -> None:
    probe = FakeProbe(dirs={"/mnt/iris", "/mnt/iris/sub", "/mnt"})
    with pytest.raises(MountPointError, match="overlaps"):
        mp.validate(path, "Linux", probe=probe, in_use={"/mnt/iris": "Prod"})


def test_posix_sibling_is_not_overlap() -> None:
    mp.validate("/mnt/iris2", "Linux", probe=FakeProbe(dirs={"/mnt/iris2"}), in_use={"/mnt/iris": "Prod"})


def test_posix_prepare_is_noop() -> None:
    probe = FakeProbe(dirs={"/mnt/iris"})
    assert mp.prepare("/mnt/iris", "Linux", probe=probe) is False
    assert probe.removed == []


# ---- Windows ---------------------------------------------------------------------------------
def test_windows_free_drive_ok() -> None:
    mp.validate("X:", "Windows", probe=FakeProbe())


def test_windows_used_drive_rejected() -> None:
    with pytest.raises(MountPointError, match="already in use"):
        mp.validate("c:", "Windows", probe=FakeProbe(dirs={"C:\\"}))


def test_windows_missing_dir_with_parent_ok() -> None:
    mp.validate("C:\\Users\\me\\iris", "Windows", probe=FakeProbe(dirs={"c:\\users\\me"}))


def test_windows_missing_parent_rejected() -> None:
    with pytest.raises(MountPointError, match="Parent folder"):
        mp.validate("C:\\nope\\iris", "Windows", probe=FakeProbe())


def test_windows_existing_nonempty_rejected() -> None:
    probe = FakeProbe(dirs={"c:\\users\\me", "C:\\Users\\me\\iris"}, nonempty={"C:\\Users\\me\\iris"})
    with pytest.raises(MountPointError, match="not empty"):
        mp.validate("C:\\Users\\me\\iris", "Windows", probe=probe)


@pytest.mark.parametrize("path", ["iris", "\\\\server\\share\\iris"])
def test_windows_relative_or_unc_rejected(path: str) -> None:
    with pytest.raises(MountPointError, match="absolute"):
        mp.validate(path, "Windows", probe=FakeProbe())


def test_windows_overlap_is_case_insensitive() -> None:
    with pytest.raises(MountPointError, match="overlaps"):
        mp.validate("c:\\USERS\\me\\iris", "Windows", probe=FakeProbe(), in_use={"C:\\Users\\me\\iris": "P"})


def test_windows_prepare_and_restore_existing_empty_dir() -> None:
    path = "C:\\Users\\me\\iris"
    probe = FakeProbe(dirs={path})
    removed = mp.prepare(path, "Windows", probe=probe)
    assert removed and probe.removed == [path]
    mp.restore(path, "Windows", removed, probe=probe)
    assert probe.created == [path]


def test_windows_prepare_drive_letter_is_noop() -> None:
    assert mp.prepare("X:", "Windows", probe=FakeProbe()) is False


def test_restore_without_removal_does_nothing() -> None:
    probe = FakeProbe()
    mp.restore("C:\\x", "Windows", False, probe=probe)
    assert probe.created == []


def test_real_probe_on_tmp(tmp_path: object) -> None:
    import os
    import platform

    if platform.system() == "Windows":
        pytest.skip("posix-only check")
    d = os.path.join(str(tmp_path), "m")
    os.mkdir(d)
    mp.validate(d, platform.system())
    open(os.path.join(d, "f"), "w").close()
    with pytest.raises(MountPointError, match="not empty"):
        mp.validate(d, platform.system())
