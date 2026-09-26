"""Scriptable stand-in for `irisfs worker` (never mounts anything). Scenario = profile name (before ":"):

ok            mounts at once; stop -> unmounted, exit 0
auth_fail     error AUTH_FAILED, exit 3
hang          never mounts, ignores everything until killed
busy          mounts; stop without force -> error BUSY (stays up); stop with force -> exit 0
crash         mounts, then exits 1 after 0.3 s
ignore_stop   mounts; ignores stop commands (must be killed)
"""

import json
import sys
import threading
import time


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def main():
    start = json.loads(sys.stdin.readline())
    scenario = start["profile"]["name"].split(":")[0]  # "ok:2" -> "ok" (names must be unique)
    path = start["profile"]["mount_point"]
    if scenario == "auth_fail":
        emit({"event": "error", "code": "AUTH_FAILED", "message": "bad password"})
        return 3
    if scenario == "hang":
        time.sleep(3600)
        return 0
    emit({"event": "mounted", "mountpoint": path})
    if scenario == "crash":
        time.sleep(0.3)
        return 1
    for line in sys.stdin:
        cmd = json.loads(line)
        if cmd.get("cmd") != "stop" or scenario == "ignore_stop":
            continue
        if scenario == "busy" and not cmd.get("force"):
            emit({"event": "error", "code": "BUSY", "message": "Resource busy"})
            continue
        emit({"event": "unmounted", "mountpoint": path})
        return 0
    return 0


if __name__ == "__main__":
    threading.Thread(target=lambda: None).start()
    sys.exit(main())
