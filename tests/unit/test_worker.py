import pytest

from irisfs.config.profile import Profile
from irisfs.mount import fuselib
from irisfs.mount.worker import Worker, WorkerConfig


def test_worker_passes_mount_folder_to_options(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: the volume name must come from the mount folder, not the profile name.
    monkeypatch.setattr(
        fuselib,
        "find_library",
        lambda system=None: fuselib.FuseLibrary("macFUSE", "/opt/local/lib/libfuse.2.dylib"),
    )
    profile = Profile(name="adhoc", host="h", username="u", mount_point="/Users/me/IRISFS-test")
    opts = Worker(WorkerConfig(profile, "pw"), lambda e: None, system="Darwin").fuse_options()
    assert opts["volname"] == "IRISFS-test"
    assert opts["local"] is True


def test_start_message_parsing() -> None:
    msg = {
        "cmd": "start",
        "profile": Profile(name="n", host="h", username="u", mount_point="/m").to_dict(),
        "password": "pw",
        "tree_ttl": 3,
    }
    cfg = WorkerConfig.from_message(msg)
    assert cfg.profile.name == "n" and cfg.password == "pw" and cfg.tree_ttl == 3
    with pytest.raises(ValueError):
        WorkerConfig.from_message({"cmd": "stop"})
