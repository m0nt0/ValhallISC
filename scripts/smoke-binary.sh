#!/bin/sh
# Smoke-test a packaged ValhallISC binary with shell tools only (no Python needed on the machine).
# Usage: smoke-binary.sh <binary> <iris host> [port]
set -eu
BIN=$1; HOST=$2; PORT=${3:-52773}
MNT=$(mktemp -d)/mnt; mkdir -p "$MNT"
fail() { echo "SMOKE FAILED: $*"; "$BIN" unmount --force "$MNT" >/dev/null 2>&1 || true; exit 1; }

"$BIN" --version
"$BIN" doctor || fail "doctor"
echo irisfs-test | "$BIN" mount --host "$HOST" --port "$PORT" --user _SYSTEM --password-stdin --mountpoint "$MNT" > /tmp/smoke-events.txt 2>/tmp/smoke-err.txt &
i=0; until grep -q '"mounted"' /tmp/smoke-events.txt 2>/dev/null; do i=$((i+1)); [ $i -gt 150 ] && fail "not mounted: $(cat /tmp/smoke-err.txt)"; sleep 0.2; done
ls "$MNT" | grep -q USER || fail "namespaces not listed"
head -c 60 "$MNT/USER/Demo/Person.cls.xml" | grep -q '<?xml' || fail "cannot read a class"
cp "$MNT/USER/Demo/Unicode.cls.xml" /tmp/u.xml && grep -q '漢字' /tmp/u.xml || fail "copy out"
sed 's/Demo.Unicode/Demo.SmokeBin/' /tmp/u.xml > /tmp/smoke.xml
cp /tmp/smoke.xml "$MNT/USER/" || fail "copy in"
i=0; until grep -q '"imported"' /tmp/smoke-events.txt; do i=$((i+1)); [ $i -gt 100 ] && fail "no import event"; sleep 0.2; done
ls "$MNT/USER/Demo" | grep -q SmokeBin.cls.xml || fail "imported class not listed"
if cp /tmp/u.xml "$MNT/USER/notes.txt" 2>/dev/null; then fail ".txt was accepted"; fi
"$BIN" unmount "$MNT" || fail "unmount"
wait
grep -q '"unmounted"' /tmp/smoke-events.txt || fail "no unmounted event"
echo "SMOKE OK"
