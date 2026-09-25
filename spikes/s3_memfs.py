"""Spike S3: tiny in-memory read/write FS served with mfusepy. Usage: s3_memfs.py MOUNTPOINT"""
import errno, os, stat, sys, time
import mfusepy as fuse

class MemFS(fuse.Operations):
    use_ns = True

    def __init__(self):
        now = time.time_ns()
        self.files = {"/hello.xml": b"<Export generator=\"IRIS\"/>\n"}
        self.dirs = {"/", "/USER"}
        self.ts = now

    def getattr(self, path, fh=None):
        if path in self.dirs:
            return dict(st_mode=stat.S_IFDIR | 0o755, st_nlink=2, st_mtime=self.ts, st_ctime=self.ts, st_atime=self.ts,
                        st_uid=os.getuid(), st_gid=os.getgid())
        if path in self.files:
            return dict(st_mode=stat.S_IFREG | 0o644, st_nlink=1, st_size=len(self.files[path]),
                        st_mtime=self.ts, st_ctime=self.ts, st_atime=self.ts, st_uid=os.getuid(), st_gid=os.getgid())
        raise fuse.FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        names = [p.rsplit("/", 1)[1] for p in [*self.files, *self.dirs] if p != "/" and (p.rsplit("/", 1)[0] or "/") == path]
        return [".", "..", *names]

    def open(self, path, flags):
        if path not in self.files:
            raise fuse.FuseOSError(errno.ENOENT)
        if flags & os.O_TRUNC:
            self.files[path] = b""
        return 0

    def create(self, path, mode, fi=None):
        if not path.endswith(".xml") and not os.path.basename(path).startswith("._"):
            raise fuse.FuseOSError(errno.EACCES)
        self.files[path] = b""
        return 0

    def read(self, path, size, offset, fh):
        return self.files[path][offset:offset + size]

    def write(self, path, data, offset, fh):
        b = bytearray(self.files[path]); b[offset:offset + len(data)] = data; self.files[path] = bytes(b)
        return len(data)

    def truncate(self, path, length, fh=None):
        self.files[path] = self.files[path][:length].ljust(length, b"\0")
        return 0

    def release(self, path, fh):
        print(f"RELEASE {path} {len(self.files.get(path, b''))}", flush=True)
        return 0

    # metadata no-ops so cp -p / Finder succeed
    def chmod(self, path, mode): return 0
    def chown(self, path, uid, gid): return 0
    def utimens(self, path, times=None): return 0
    def setxattr(self, path, name, value, options, position=0): return 0
    def getxattr(self, path, name, position=0): raise fuse.FuseOSError(getattr(errno, "ENOATTR", errno.ENODATA))
    def listxattr(self, path): return []
    def removexattr(self, path, name): return 0
    def unlink(self, path): raise fuse.FuseOSError(errno.EPERM)
    def statfs(self, path): return dict(f_bsize=4096, f_frsize=4096, f_blocks=1 << 20, f_bfree=1 << 19, f_bavail=1 << 19, f_namemax=255)

if __name__ == "__main__":
    print("LIB", fuse._libfuse_path, fuse.fuse_version_major, fuse.fuse_version_minor, flush=True)
    opts = dict(foreground=True, fsname="irisfs-spike")
    if sys.platform == "darwin":
        opts.update(volname="irisfs-spike", **({"backend": "fskit"} if os.environ.get("FSKIT") else {}))
    fuse.FUSE(MemFS(), sys.argv[1], **opts)
    print("EXITED", flush=True)
