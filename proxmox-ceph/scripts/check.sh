#!/usr/bin/env bash
# Cerberus — scripts/check.sh
# Validation gate for the proxmox-ceph track.
#   1. bash -n syntax check on every script
#   2. bash -n on every ```bash/```sh block in the track's Markdown
#      (parity with the BCM track's check.sh)
#   3. placeholder-in-command lint: <UPPER-CASE> and lower-case <token>
#      placeholders inside bash blocks (self-tested)
#   4. python compile check on render.py
#   5. render smoke test (renders to a temp dir, verifies substitution)
#   6. rendered-output scan: no blank interface positions in the rendered
#      §02 (empty bridge-ports / bond-slaves / bond-primary, nameless
#      iface / auto) — self-tested, and regression-tested against a site
#      file that defines the host-resolved names as empty
# Every stage fails closed. Exit 0 all passed, 1 any check failed.
set -euo pipefail
TRACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0

echo "== bash syntax =="
for s in "$TRACK_DIR"/scripts/*.sh "$TRACK_DIR"/runbook/00-overview/variables.sh; do
    if bash -n "$s"; then echo "OK   $s"; else echo "FAIL $s"; fail=1; fi
done

# Markdown in scope for the doc-block stages.
DOCS=("$TRACK_DIR"/README.md "$TRACK_DIR"/runbook/*/README.md "$TRACK_DIR"/docs/*.md)

echo "== bash -n on all doc code blocks =="
if python3 - "$TRACK_DIR" "${DOCS[@]}" <<'EOF'
import os, re, subprocess, sys, tempfile
root, files = sys.argv[1], sys.argv[2:]
fails = blocks = 0
for fn in files:
    text = open(fn).read()
    for m in re.finditer(r'```(bash|sh)\n(.*?)```', text, re.S):
        blocks += 1
        line = text.count("\n", 0, m.start(2)) + 1
        with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as t:
            t.write(m.group(2)); tname = t.name
        r = subprocess.run(['bash', '-n', tname], capture_output=True, text=True)
        os.unlink(tname)
        if r.returncode != 0:
            fails += 1
            err = r.stderr.strip().replace(tname, "block")[:140]
            print(f"BASH-FAIL {os.path.relpath(fn, root)}:{line} (block start): {err}")
print(f"bash: {blocks} blocks, {fails} failures")
sys.exit(1 if fails else 0)
EOF
then :; else fail=1; fi

echo "== placeholder-in-command lint (bash blocks) =="
# A <placeholder> token inside a ```bash/```sh fence is a command that
# breaks when pasted as written: unquoted it is a redirect (`<BF3_P1>`
# reads a file named BF3_P1), quoted it is a literal string handed to a
# real tool (`ping <other-node-mgmt-ip>`). Both <UPPER-CASE> and
# lower-case <tokens> are linted. Use the $VARIABLE instead. A lower-case
# token may not have whitespace right before its '>' (so
# `cat <<eof > file` is not mistaken for one).
# Not linted: full-line comments (lines whose first non-blank is '#').
# ALLOWLIST CONVENTION — for a legitimate construct, end the line with
#     # lint: allow-placeholder <why>
# The reason is mandatory, and the marker is per line, never per block.
if python3 - "$TRACK_DIR" "${DOCS[@]}" <<'EOF'
import os, re, sys
TOKEN = re.compile(r'<[A-Z][A-Z0-9_-]*>|<[a-z](?:[^<>\n]*[^<>\s])?>')
ALLOW = re.compile(r'#\s*lint:\s*allow-placeholder\s+\S')
# Self-test: fire on known placeholders, stay quiet on ordinary shell.
MUST_HIT = ['ethtool <BF3_P1> | grep Speed', 'ping -c 3 <other-node-mgmt-ip>',
            "--fingerprint '<paste node-1 SHA256 fingerprint here>'"]
MUST_PASS = ['cat > /etc/x <<EOF', 'cat <<eof > /etc/x', 'cmd 2>&1 | tee log',
             '[ "$a" -lt 3 ] && echo ok']
bad = [t for t in MUST_HIT if not TOKEN.search(t)] + \
      [t for t in MUST_PASS if TOKEN.search(t)]
if bad:
    print("LINT SELF-TEST FAILED:", bad)
    sys.exit(1)
root, files = sys.argv[1], sys.argv[2:]
hits, allowed = [], 0
for fn in files:
    text = open(fn).read()
    for m in re.finditer(r'```(bash|sh)\n(.*?)```', text, re.S):
        first = text.count("\n", 0, m.start(2)) + 1
        for i, line in enumerate(m.group(2).splitlines()):
            if line.lstrip().startswith("#"):
                continue
            toks = TOKEN.findall(line)
            if not toks:
                continue
            if ALLOW.search(line):
                allowed += 1
                continue
            hits.append(f"{os.path.relpath(fn, root)}:{first + i}: {' '.join(toks)}  |  {line.strip()[:110]}")
for h in hits:
    print(f"PLACEHOLDER-IN-COMMAND {h}")
print(f"lint: {len(hits)} hit(s), {allowed} allowlisted line(s) "
      f"(upper- and lower-case <...> tokens; self-test OK)")
sys.exit(1 if hits else 0)
EOF
then :; else fail=1; fi

echo "== python compile =="
if python3 -m py_compile "$TRACK_DIR/scripts/render.py"; then
    echo "OK   scripts/render.py"
else
    echo "FAIL scripts/render.py"; fail=1
fi

# scan_blank_ifaces FILE... — fail on blank interface positions, the
# signature of a renderer that substituted an empty value (H2).
scan_blank_ifaces() {
    python3 - "$@" <<'EOF'
import re, sys
BLANK = [
    (re.compile(r'^\s*bridge-ports\s*$'), "empty bridge-ports"),
    (re.compile(r'^\s*bond-slaves\s*$'), "empty bond-slaves"),
    (re.compile(r'^\s*bond-primary\s*$'), "empty bond-primary"),
    (re.compile(r'^\s*iface\s+inet6?\b'), "iface with no name"),
    (re.compile(r'^\s*auto\s*$'), "auto with no name"),
]
hits = 0
for fn in sys.argv[1:]:
    for n, line in enumerate(open(fn), 1):
        for rx, what in BLANK:
            if rx.search(line):
                hits += 1
                print(f"BLANK-IFACE {fn}:{n}: {what}: {line.rstrip()!r}")
sys.exit(1 if hits else 0)
EOF
}

echo "== render smoke test =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if "$TRACK_DIR/scripts/render.py" --out "$TMP/out" >"$TMP/render.log"; then
    # every section must have rendered
    for d in "$TRACK_DIR"/runbook/*/; do
        sec="$(basename "$d")"
        [ -f "$TMP/out/runbook/$sec/README.md" ] || { echo "FAIL missing $sec"; fail=1; }
    done
    # a defined variable must not survive unsubstituted (excluding shell ${V:-} forms)
    if grep -rhoE '\$\{?(CEPH_PUBLIC_VLAN|MGMT_NET|CLUSTER_NAME)\}?' "$TMP/out" | grep -qv ':-'; then
        echo "FAIL leftover defined variables in rendered output"; fail=1
    else
        echo "OK   render smoke test"
    fi
else
    echo "FAIL render.py"; fail=1
fi

echo "== rendered-output scan: blank interface positions (§02) =="
# Self-test first: the scanner must fire on the H2 blanked stanza, or a
# clean result below means nothing.
# (trailing blanks are deliberate: this is render.py's r12 output verbatim)
printf '%s\n' 'auto ' 'iface  inet manual' '    bond-slaves  ' '    bond-primary ' \
    '    bridge-ports ' > "$TMP/blank-known-bad.txt"
if scan_blank_ifaces "$TMP/blank-known-bad.txt" >/dev/null; then
    echo "FAIL scanner self-test: did not fire on a known-blank stanza"; fail=1
else
    echo "OK   scanner self-test (fires on a known-blank stanza)"
fi
# The template render.
if [ -f "$TMP/out/runbook/02-network/README.md" ] \
   && scan_blank_ifaces "$TMP/out/runbook/02-network/README.md"; then
    echo "OK   rendered §02 (template): no blank interface positions"
else
    echo "FAIL rendered §02 (template): blank interface positions, or §02 missing"; fail=1
fi
# Regression: a site file that DEFINES the host-resolved names as empty
# (the r12 shape) must still render them as pass-through ${VAR}, and the
# render report must say so.
cp "$TRACK_DIR/runbook/00-overview/variables.sh" "$TMP/empty-host-vars.sh"
printf '%s\n' 'BF3_P1="${BF3_P1:-}"' 'BF3_P2="${BF3_P2:-}"' 'MGMT_IF="${MGMT_IF:-}"' >> "$TMP/empty-host-vars.sh"
if "$TRACK_DIR/scripts/render.py" --vars "$TMP/empty-host-vars.sh" --out "$TMP/out-empty" >"$TMP/render-empty.log"; then
    R02="$TMP/out-empty/runbook/02-network/README.md"
    if scan_blank_ifaces "$R02" \
       && grep -q 'bridge-ports ${MGMT_IF}' "$R02" \
       && grep -q 'PASSED THROUGH' "$TMP/render-empty.log" \
       && grep -qE '^  BF3_P1 +\([1-9]' "$TMP/render-empty.log"; then
        echo "OK   empty-defined host names pass through untouched and are reported"
    else
        echo "FAIL empty-defined host names were blanked or not reported"; fail=1
    fi
else
    echo "FAIL render.py (empty-host-vars regression)"; fail=1
fi

if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "CHECKS FAILED"; exit 1; fi
