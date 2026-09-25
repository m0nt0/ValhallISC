"""Spike S3 driver: mount s3_memfs in a subprocess and exercise it with real shell tools."""
import os, shutil, signal, subprocess, sys, tempfile, time

def unmount(mp):
    if sys.platform == "darwin":
        return subprocess.run(["umount", mp], capture_output=True, text=True)
    return subprocess.run(["fusermount3", "-u", mp], capture_output=True, text=True)

def wait_mounted(mp, t=15):
    end = time.time() + t
    while time.time() < end:
        if os.path.ismount(mp): return True
        time.sleep(0.1)
    return False

def start(mp):
    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "s3_memfs.py"), mp],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert wait_mounted(mp), "not mounted"
    return p

def main():
    ok = True
    tmp = tempfile.mkdtemp(prefix="irisfs-s3-"); mp = os.path.join(tmp, "mnt"); os.mkdir(mp)
    t0 = time.time(); p = start(mp); print(f"mounted in {time.time()-t0:.2f}s")
    try:
        print("ls:", sorted(os.listdir(mp)), sorted(os.listdir(mp + "/USER")))
        out = os.path.join(tmp, "out.xml")
        subprocess.run(["cp", mp + "/hello.xml", out], check=True)
        print("cp out:", open(out, "rb").read())
        src = os.path.join(tmp, "in.xml"); open(src, "wb").write(b"<Export>" + b"x" * 200000 + b"</Export>\n")
        r = subprocess.run(["cp", "-p", src, mp + "/USER/in.xml"], capture_output=True, text=True)
        print("cp -p in:", r.returncode, r.stderr.strip()); ok &= r.returncode == 0
        ok &= os.path.getsize(mp + "/USER/in.xml") == os.path.getsize(src)
        r = subprocess.run(["cp", src.replace("in.xml", "out.xml"), mp + "/USER/notes.txt"], capture_output=True, text=True)
        print("cp .txt (expect fail):", r.returncode, r.stderr.strip()); ok &= r.returncode != 0
        r = subprocess.run(["rm", mp + "/hello.xml"], capture_output=True, text=True)
        print("rm (expect EPERM):", r.returncode, r.stderr.strip()); ok &= r.returncode != 0
        if sys.platform == "darwin":
            r = subprocess.run(["xattr", "-w", "com.example.t", "v", mp + "/USER/in.xml"], capture_output=True, text=True)
            print("xattr -w:", r.returncode, r.stderr.strip())
        # busy unmount: a process holding cwd inside the mount
        busy = subprocess.Popen(["sleep", "30"], cwd=mp + "/USER")
        time.sleep(0.3)
        r = unmount(mp); print("unmount while busy:", r.returncode, (r.stderr or r.stdout).strip())
        busy.kill(); busy.wait()
        if os.path.ismount(mp):
            r = unmount(mp); print("unmount after release:", r.returncode, (r.stderr or r.stdout).strip())
        p.wait(timeout=10)
        print("worker exit:", p.returncode, "mounted:", os.path.ismount(mp)); ok &= not os.path.ismount(mp)
        # SIGTERM path
        p = start(mp); p.send_signal(signal.SIGTERM)
        try: p.wait(timeout=5); print("SIGTERM -> exit", p.returncode, "mounted:", os.path.ismount(mp))
        except subprocess.TimeoutExpired: print("SIGTERM ignored")
        if os.path.ismount(mp): unmount(mp); p.wait(timeout=5)
        ok &= not os.path.ismount(mp)
    finally:
        if os.path.ismount(mp): unmount(mp)
        out = p.stdout.read() if p.stdout else ""
        print("--- worker output ---\n" + out.strip())
        shutil.rmtree(tmp, ignore_errors=True)
    print("FUSE OK" if ok else "FUSE FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
