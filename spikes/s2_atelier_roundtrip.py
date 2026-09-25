"""Spike S2: verify XML export/import round trip through the Atelier REST API.

Run with the test IRIS container up:  .venv/bin/python spikes/s2_atelier_roundtrip.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

import defusedxml.ElementTree as ET
import httpx

BASE = "http://localhost:52773/api/atelier/v8"
AUTH = ("_SYSTEM", "irisfs-test")
COMPOSE = ["docker", "compose", "-f", "docker/docker-compose.yml"]
ITEMS = ["Demo.Person.cls", "DEMORTN.mac", "DemoInc.inc", "Demo.Unicode.cls", "DemoTable.LUT"]

TS_RE = re.compile(rb'\s+ts="[^"]*"')


def norm(b: bytes) -> bytes:
    """Drop the export timestamp and normalise line endings/trailing newline."""
    return TS_RE.sub(b"", b.replace(b"\r\n", b"\n")).rstrip(b"\n")


def export(c: httpx.Client, ns: str, name: str) -> bytes:
    r = c.post(f"{BASE}/{ns}/action/xml/export", json=[name])
    r.raise_for_status()
    body = r.json()
    assert not body["status"]["errors"], body["status"]
    return ("\n".join(body["result"]["content"]) + "\n").encode("utf-8")


def load(c: httpx.Client, ns: str, data: bytes, flags: str = "ck") -> dict:
    lines = data.decode("utf-8").split("\n")
    r = c.post(
        f"{BASE}/{ns}/action/xml/load",
        params={"flags": flags},
        json=[{"file": "spike.xml", "content": lines}],
    )
    r.raise_for_status()
    body = r.json()
    return {"errors": body["status"]["errors"], "console": body["console"], "result": body["result"]["content"]}


def reference_export(ns: str, name: str) -> bytes:
    """What $SYSTEM.OBJ.Export writes for the same item, read back from the container."""
    script = (
        f'set sc=$system.OBJ.Export("{name}","/tmp/ref.xml","-d") write:\'sc "EXPORT FAILED",!\n'
        "halt\n"
    )
    subprocess.run(
        [*COMPOSE, "exec", "-T", "iris", "iris", "session", "IRIS", "-U", ns],
        input=script.encode(),
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        [*COMPOSE, "exec", "-T", "iris", "cat", "/tmp/ref.xml"], check=True, capture_output=True
    ).stdout


def main() -> int:
    ok = True
    with httpx.Client(auth=AUTH, timeout=30) as c:
        for name in ITEMS:
            t0 = time.perf_counter()
            xml = export(c, "USER", name)
            t_exp = time.perf_counter() - t0
            root = ET.fromstring(xml)
            assert root.tag == "Export", root.tag

            ref = reference_export("USER", name)
            same = norm(xml) == norm(ref)
            print(f"{name}: export {len(xml)} bytes in {t_exp*1000:.0f} ms; equals $SYSTEM.OBJ.Export: {same}")
            if not same:
                ok = False
                print("  --- api ---\n", xml[:400], "\n  --- ref ---\n", ref[:400])

            # Modify: inject a marker comment/value and load it back.
            marker = f"irisfs-spike-{int(time.time())}"
            if name.endswith(".cls"):
                mod = xml.replace(b"<Description>", f"<Description>{marker} ".encode(), 1)
            elif name.endswith(".LUT"):
                mod = xml.replace(b">Italy<", f">Italy {marker}<".encode(), 1)
            else:
                mod = re.sub(rb"(<!\[CDATA\[)", rb"\1; " + marker.encode() + b"\n", xml, count=1)
            assert mod != xml, f"could not modify {name}"
            t0 = time.perf_counter()
            res = load(c, "USER", mod)
            t_load = time.perf_counter() - t0
            again = export(c, "USER", name)
            changed = marker.encode() in again
            print(f"  load in {t_load*1000:.0f} ms -> {res['result']} errors={res['errors']}; change visible: {changed}")
            ok &= changed and not res["errors"]

            # restore original
            load(c, "USER", xml)

        # malformed XML must not change anything
        res = load(c, "USER", b"<Export><Class name='Demo.Broken'>")
        print("malformed load ->", res["result"], res["errors"][:1])

        # compile error: import succeeds, compile reports errors
        util_orig = export(c, "USER", "Demo.Util.cls")
        bad = util_orig.replace(b'quit "Hello, "_name_"!"', b'quit "Hello, "_name_"!" +++ )', 1)
        res = load(c, "USER", bad)
        print("compile-error load ->", res["result"], "errors:", [e.get("error", e)[:120] for e in res["errors"]][:2])
        load(c, "USER", util_orig)

        # license / session reuse: 200 sequential calls on one client
        t0 = time.perf_counter()
        for _ in range(200):
            c.get(f"{BASE}/USER/docnames/CLS", params={"generated": 0}).raise_for_status()
        print(f"200 calls with session reuse OK in {time.perf_counter()-t0:.1f}s; cookies={list(c.cookies.keys())}")

    # Without session reuse (new client each time) - observe behaviour.
    failures = 0
    for i in range(40):
        with httpx.Client(auth=AUTH, timeout=30) as c2:
            r = c2.get(f"{BASE}/USER/docnames/CLS")
            if r.status_code != 200:
                failures += 1
                if failures == 1:
                    print(f"  no-reuse failure at call {i}: HTTP {r.status_code} {r.text[:200]}")
    print(f"40 calls WITHOUT session reuse: {failures} failures")

    print("ROUNDTRIP OK" if ok else "ROUNDTRIP FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
