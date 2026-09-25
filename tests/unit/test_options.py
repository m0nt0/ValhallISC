from irisfs.mount.fuselib import FuseLibrary
from irisfs.mount.options import mount_options

MACFUSE = FuseLibrary("macFUSE (MacPorts)", "/opt/local/lib/libfuse.2.dylib")
FUSE3 = FuseLibrary("libfuse3", "libfuse3.so.3")


def test_macos_volume_named_after_mount_folder_and_local() -> None:
    o = mount_options(
        "Darwin", MACFUSE, profile_name="adhoc", read_only=False, mount_point="/Users/me/IRISFS-test/"
    )
    assert o["volname"] == "IRISFS-test"
    assert o["local"] and o["noappledouble"]
    assert "noapplexattr" not in o  # regression: Finder copy failed with a permission error
    assert "ro" not in o


def test_macos_fuse_t_gets_no_macfuse_only_options() -> None:
    o = mount_options(
        "Darwin",
        FuseLibrary("FUSE-T", "/usr/local/lib/libfuse-t.dylib"),
        profile_name="P",
        read_only=True,
        mount_point="/x/y",
    )
    assert o["ro"] and o["volname"] == "y" and "local" not in o


def test_linux_options() -> None:
    o = mount_options("Linux", FUSE3, profile_name="My Server!", read_only=True, mount_point="/mnt/x")
    assert o["fsname"] == "irisfs-My_Server_" and o["ro"] and o["auto_unmount"]
    assert "volname" not in o


def test_windows_options() -> None:
    o = mount_options(
        "Windows", FuseLibrary("WinFsp", "C:/winfsp-x64.dll"), profile_name="P", read_only=False
    )
    assert o["uid"] == -1 and o["FileSystemName"] == "IRISFS"
