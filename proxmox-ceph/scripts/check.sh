#!/usr/bin/env bash
# Cerberus — scripts/check.sh
# Validation gate for the proxmox-ceph track.
#   1. bash -n syntax check on every script
#   2. bash -n on every ```bash/```sh block in the track's Markdown
#      (parity with the BCM track's check.sh)
#   3. placeholder-in-command lint: <UPPER-CASE>, lower-case <token> and
#      mixed-case <Foo-Bar> placeholders inside bash blocks (self-tested)
#   4. paste-atomicity lint (B1): every runbook block that sources the
#      site file or calls a cerberus_require_* guard is a fail-closed
#      subshell `( set -euo pipefail … )`, and no block sources the site
#      file by a relative path (self-tested)
#   5. dangling-reference lint (scripts/reflint.py, self-tested): every
#      section / sub-section / file reference lands on something real
#   5b. customer design brief: .md / .docx / .pdf agree (docs/build-briefs.py
#      --verify; FAILS without pdftotext — ruled fail-closed, M7 #5)
#   5c. python compile check on render.py, reflint.py, build-briefs.py (in memory)
#   6. render smoke test (renders to a temp dir, verifies substitution);
#      site-file default (no site file -> exit 2; template only when named,
#      with the NOTE); links resolve inside the deliverable; --force only
#      replaces a previous render's directory
#   7. rendered-output scan: no blank interface positions in the rendered
#      §02 (empty bridge-ports / bond-slaves / bond-primary, nameless
#      iface / auto) — self-tested, and regression-tested against a site
#      file that defines the host-resolved names as empty
#   8. render credential class + tripwire (H6/I5): credential-class names
#      are never substituted; a live secret in the variables FILE makes
#      render refuse; one in the ENVIRONMENT only warns; the value never
#      reaches the guide or the report. Template-default audit covers
#      MAILTO and the pool sizing.
# Every stage fails closed. Exit 0 all passed, 1 any check failed.
# Writes nothing into the tree.
set -euo pipefail
TRACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
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
# real tool (`ping <other-node-mgmt-ip>`). <UPPER-CASE>, lower-case and
# mixed-case (<Foo-Bar>, <Node IP>) tokens are all linted. Use the
# $VARIABLE instead. A token may not have whitespace right before its '>'
# (so `cat <<eof > file` is not mistaken for one).
# Not linted: full-line comments (lines whose first non-blank is '#').
# Trailing comments ARE linted (ruled 2026-11-16): write them as content.
# ALLOWLIST CONVENTION — for a legitimate construct, end the line with
#     # lint: allow-placeholder <why>
# The reason is mandatory, and the marker is per line, never per block.
if python3 - "$TRACK_DIR" "${DOCS[@]}" <<'EOF'
import os, re, sys
# The second alternative covers upper-, lower- and mixed-case tokens; the
# first is kept as the documented upper-case form.
TOKEN = re.compile(r'<[A-Z][A-Z0-9_-]*>|<[A-Za-z](?:[^<>\n]*[^<>\s])?>')
ALLOW = re.compile(r'#\s*lint:\s*allow-placeholder\s+\S')
# Self-test: fire on known placeholders, stay quiet on ordinary shell.
MUST_HIT = ['ethtool <BF3_P1> | grep Speed', 'ping -c 3 <other-node-mgmt-ip>',
            "--fingerprint '<paste node-1 SHA256 fingerprint here>'",
            'ping -c 3 <Node-IP>', 'ssh root@<Foo-Bar> uptime',
            'scripts/preflight.sh   # or --vars <path>']
MUST_PASS = ['cat > /etc/x <<EOF', 'cat <<eof > /etc/x', 'cmd 2>&1 | tee log',
             '[ "$a" -lt 3 ] && echo ok', 'cat <<EOF >"$TMP"',
             'diff <(sort a) <(sort b)']
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
      f"(upper-, lower- and mixed-case <...> tokens; self-test OK)")
sys.exit(1 if hits else 0)
EOF
then :; else fail=1; fi

echo "== paste-atomicity lint (runbook bash blocks) =="
# B1 (ruled): a pasted block that fails mid-way must STOP, not run on with
# empty variables. The track's one idiom is a subshell:
#     ( set -euo pipefail
#       source "${CERBERUS_TRACK:?…}/site-vars.sh"
#       … )
# Bash reads the whole ( … ) before running any of it, and the first
# failing command — a guard, an unbound variable, a failed write — ends
# the subshell; the operator's shell survives (no `exit` kills it).
# This stage enforces it for the class it can see mechanically:
#   - any block that sources site-vars.sh, or calls cerberus_require_vars
#     / cerberus_require_secret (a guard outside the idiom does not gate),
#     must open with `(` + `set -euo pipefail` and close with `)`;
#   - no block may source site-vars.sh by a RELATIVE path (the
#     working-directory contract is $CERBERUS_TRACK, track README).
# A block that deliberately sets up the operator's own shell (exports for
# later blocks) carries `# paste: session` on its source/guard line.
if python3 - "$TRACK_DIR" "$TRACK_DIR"/README.md "$TRACK_DIR"/runbook/*/README.md <<'EOF'
import os, re, sys
SRC = re.compile(r'^\s*(?:source|\.)\s+(\S+)')
GUARD = re.compile(r'\bcerberus_require_(?:vars|secret)\b')
SESSION = re.compile(r'#\s*paste:\s*session\b')
def problems(block):
    lines = block.splitlines()
    code = [l for l in lines if l.strip() and not l.lstrip().startswith('#')]
    out, needs = [], False
    for l in lines:
        m = SRC.match(l)
        if m and 'site-vars.sh' in m.group(1):
            path = m.group(1).strip('"\'')
            if not (path.startswith('$') or path.startswith('/')):
                out.append(f"relative source path {m.group(1)} (use \"$CERBERUS_TRACK/site-vars.sh\")")
            if not SESSION.search(l):
                needs = True
        if GUARD.search(l) and not l.lstrip().startswith('#') and not SESSION.search(l):
            needs = True
    if needs:
        opens = bool(code) and re.match(r'^\(\s*(set -euo pipefail\b.*)?$', code[0].strip())
        has_set = any(re.match(r'^\(?\s*set -euo pipefail\b', l.strip()) for l in code[:2])
        closes = bool(code) and code[-1].strip() == ')'
        if not (opens and has_set and closes):
            out.append("state block not in the fail-closed idiom ( set -euo pipefail … )")
    return out
# Self-test: the stage must reject the B1 shapes and accept the idiom.
BAD = ['source ../../site-vars.sh\ncat > /etc/x <<EOF\n$A\nEOF\n',
       'cerberus_require_vars A B\ncp a b\n',
       '( set -euo pipefail\n  source ../../site-vars.sh\n  cp a b\n)\n']
GOOD = ['( set -euo pipefail\n  source "${CERBERUS_TRACK:?x}/site-vars.sh"\n  cp a b\n)\n',
        'export A=1   # nothing sourced\n',
        'source "$CERBERUS_TRACK/site-vars.sh"   # paste: session\n']
st = [b for b in BAD if not problems(b)] + [g for g in GOOD if problems(g)]
if st:
    print("PASTE-LINT SELF-TEST FAILED:", st)
    sys.exit(1)
root, files = sys.argv[1], sys.argv[2:]
hits = checked = 0
for fn in files:
    text = open(fn).read()
    for m in re.finditer(r'```(bash|sh)\n(.*?)```', text, re.S):
        line = text.count("\n", 0, m.start(2)) + 1
        p = problems(m.group(2))
        if SRC.search(m.group(2)) or GUARD.search(m.group(2)):
            checked += 1
        for msg in p:
            hits += 1
            print(f"PASTE-ATOMICITY {os.path.relpath(fn, root)}:{line} (block start): {msg}")
print(f"paste lint: {hits} hit(s) across {checked} state block(s) (self-test OK)")
sys.exit(1 if hits else 0)
EOF
then :; else fail=1; fi

echo "== dangling-reference lint (sections, sub-sections, files, ranges) =="
# Brief M6: every §NN / NN §M / §N.M / Step N reference, file name and
# section range in the track (and the front door's proxmox-ceph/ lines)
# must land on a real heading or file. Self-tested inside reflint.py.
if python3 "$TRACK_DIR/scripts/reflint.py"; then :; else fail=1; fi

echo "== customer design brief: .md / .docx / .pdf agree =="
# Brief M6: the .md is the master; docs/build-briefs.py regenerates the
# .docx/.pdf. Every paragraph, list item and table cell of the .md must
# appear in both derived formats — an .md edited without a rebuild fails
# here. Needs pdftotext (poppler); without it the stage says NOT RUN
# loudly and the gate does not claim the formats agree.
# Ruled 2026-11-17 (M7 #5): FAIL CLOSED without pdftotext — a gate that
# cannot check the formats does not pass them.
if command -v pdftotext >/dev/null 2>&1; then
    if python3 "$TRACK_DIR/docs/build-briefs.py" --verify; then :; else
        echo "FAIL brief formats disagree — run: python3 docs/build-briefs.py"; fail=1
    fi
else
    echo "FAIL pdftotext not installed — the .docx/.pdf agreement cannot be verified, so the gate fails (install poppler-utils)"
    fail=1
fi

echo "== python compile =="
for py in scripts/render.py scripts/reflint.py docs/build-briefs.py; do
    if python3 -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' \
            "$TRACK_DIR/$py"; then
        echo "OK   $py"
    else
        echo "FAIL $py"; fail=1
    fi
done

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
TEMPLATE="$TRACK_DIR/runbook/00-overview/variables.sh"
# M7 #7: render defaults to <track>/site-vars.sh and never falls back to
# the template. With no site file in the tree, a bare render must refuse.
if [ -e "$TRACK_DIR/site-vars.sh" ]; then
    echo "NOTE site-vars.sh present in the tree — no-site-file refusal test skipped"
else
    set +e
    "$TRACK_DIR/scripts/render.py" --out "$TMP/out-nosite" >"$TMP/nosite.out" 2>"$TMP/nosite.err"
    rc=$?
    set -e
    if [ "$rc" -eq 2 ] && [ ! -e "$TMP/out-nosite" ] \
       && grep -q 'REFUSED: no site variables file' "$TMP/nosite.err" \
       && grep -q 'cp .*variables.sh .*site-vars.sh' "$TMP/nosite.err"; then
        echo "OK   no site file: render refused (exit 2) with the cp line, nothing written"
    else
        echo "FAIL no site file: render did not refuse cleanly (rc=$rc)"; fail=1
    fi
fi
# The template only when named explicitly — and then with the NOTE.
if "$TRACK_DIR/scripts/render.py" --vars "$TEMPLATE" --out "$TMP/out" >"$TMP/render.log"; then
    if grep -q '^NOTE: reading the pristine TEMPLATE (named explicitly' "$TMP/render.log"; then
        echo "OK   template named explicitly: rendered with the publisher's NOTE"
    else
        echo "FAIL template render printed no NOTE"; fail=1
    fi
    # every section must have rendered
    for d in "$TRACK_DIR"/runbook/*/; do
        sec="$(basename "$d")"
        [ -f "$TMP/out/runbook/$sec/README.md" ] || { echo "FAIL missing $sec"; fail=1; }
    done
    # a defined variable must not survive unsubstituted (excluding shell ${V:-} forms)
    if grep -rhoE '\$\{?(CEPH_PUBLIC_VLAN|MGMT_NET|CLUSTER_NAME|PBS_VLAN_IP)\}?' "$TMP/out" | grep -qv ':-'; then
        echo "FAIL leftover defined variables in rendered output"; fail=1
    else
        echo "OK   render smoke test"
    fi
    # L2: the deliverable stands alone — no relative link may point outside
    # it, linked diagrams are copied in, library-only files become text.
    if python3 - "$TMP/out" <<'EOF'
import os, re, sys
out = sys.argv[1]
bad = 0
for d, _, fs in os.walk(out):
    for fn in fs:
        if not fn.endswith(".md"):
            continue
        p = os.path.join(d, fn)
        for m in re.finditer(r"\]\(([^)\s]+)\)", open(p).read()):
            t = m.group(1).partition("#")[0]
            if not t or re.match(r"^[a-z][a-z0-9+.-]*:|^/", t):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(d, t))):
                bad += 1
                print(f"BROKEN-LINK {os.path.relpath(p, out)}: {m.group(1)}")
sys.exit(1 if bad else 0)
EOF
    then
        if [ -f "$TMP/out/diagrams/cable-diagram.svg" ] \
           && grep -q 'library file `proxmox-ceph/docs/references.md`' "$TMP/out/runbook/01-cluster-install/README.md"; then
            echo "OK   rendered links resolve inside the deliverable (diagram copied, library files named)"
        else
            echo "FAIL rendered links: diagram not copied or library reference not rewritten"; fail=1
        fi
    else
        echo "FAIL rendered guide has links that point outside the deliverable"; fail=1
    fi
    # L16: helper-function locals (val, missing, ...) are not site variables.
    if python3 -I -B -c 'import sys, importlib.util as u
s = u.spec_from_file_location("render", sys.argv[1]); r = u.module_from_spec(s); s.loader.exec_module(r)
v = r.parse_variables(sys.argv[2])
local = {"val", "missing", "value", "name", "rc", "fp", "here", "tmpl", "dflt", "found", "auth", "v"}
sys.exit(1 if (local & set(v)) or "CLUSTER_NAME" not in v else 0)' \
            "$TRACK_DIR/scripts/render.py" "$TEMPLATE"; then
        echo "OK   parse_variables reads top-level assignments only (no helper locals)"
    else
        echo "FAIL parse_variables picked up helper-function locals"; fail=1
    fi
    # L2: --force replaces only a directory a previous render wrote.
    mkdir -p "$TMP/not-a-render" && : > "$TMP/not-a-render/keep.txt"
    set +e
    "$TRACK_DIR/scripts/render.py" --vars "$TEMPLATE" --out "$TMP/not-a-render" --force >/dev/null 2>"$TMP/force.err"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ] && [ -f "$TMP/not-a-render/keep.txt" ] \
       && "$TRACK_DIR/scripts/render.py" --vars "$TEMPLATE" --out "$TMP/out" --force >/dev/null; then
        echo "OK   --force refuses a foreign directory and replaces a previous render"
    else
        echo "FAIL --force guard (rc=$rc)"; fail=1
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

echo "== render credential class + tripwire (H6/I5) =="
LIVE="cerb-live-$$-Zq9"   # a made-up 'secret' — must never reach any output
# (a) template render: the kept-names line is printed, and the guide keeps
#     $PBS_PASSWORD as a reference.
if grep -q '^credential-class names kept as \$NAME, never substituted .*PBS_PASSWORD' "$TMP/render.log" \
   && grep -rq '\$PBS_PASSWORD' "$TMP/out/runbook/06-pbs/README.md"; then
    echo "OK   credential-class names reported and kept as \$NAME in the guide"
else
    echo "FAIL credential-class names not reported, or \$PBS_PASSWORD not kept in the rendered 06"; fail=1
fi
# (b) a LIVE secret in the variables FILE: render refuses (exit 3), writes
#     nothing, and never echoes the value.
cp "$TRACK_DIR/runbook/00-overview/variables.sh" "$TMP/live-file.sh"
printf 'PBS_PASSWORD="%s"\n' "$LIVE" >> "$TMP/live-file.sh"
set +e
"$TRACK_DIR/scripts/render.py" --vars "$TMP/live-file.sh" --out "$TMP/out-live" >"$TMP/live.out" 2>"$TMP/live.err"
rc=$?
set -e
if [ "$rc" -eq 3 ] && [ ! -e "$TMP/out-live" ] \
   && grep -q 'REFUSED: LIVE SECRETS IN THE VARIABLES FILE' "$TMP/live.err" \
   && grep -q 'PBS_PASSWORD: holds a real value' "$TMP/live.err" \
   && ! grep -q "$LIVE" "$TMP/live.out" "$TMP/live.err"; then
    echo "OK   live secret in the file: render refused (exit 3), nothing written, value never printed"
else
    echo "FAIL live secret in the file was not refused cleanly (rc=$rc)"; fail=1
fi
# (c) a LIVE secret only in the ENVIRONMENT (placeholder in the file):
#     warning naming it, render succeeds, value nowhere in the guide.
cp "$TRACK_DIR/runbook/00-overview/variables.sh" "$TMP/live-env.sh"
printf '%s\n' 'PBS_PASSWORD="${PBS_PASSWORD:-PLACEHOLDER}"' >> "$TMP/live-env.sh"
if PBS_PASSWORD="$LIVE" "$TRACK_DIR/scripts/render.py" --vars "$TMP/live-env.sh" \
        --out "$TMP/out-env" >"$TMP/env.out" 2>"$TMP/env.err" \
   && grep -q 'WARNING: LIVE SECRETS LOADED NEAR A DELIVERABLE' "$TMP/env.err" \
   && grep -q 'PBS_PASSWORD: holds a real value (environment)' "$TMP/env.err" \
   && ! grep -rq "$LIVE" "$TMP/out-env" "$TMP/env.out" "$TMP/env.err" \
   && grep -q '\$PBS_PASSWORD' "$TMP/out-env/runbook/06-pbs/README.md"; then
    echo "OK   live secret in the environment: warned by name, render OK, value absent from the guide"
else
    echo "FAIL environment-held secret: warning missing, render failed, or the value leaked"; fail=1
fi
# (d) template-default audit covers MAILTO and the pool sizing.
if grep -q '^  MAILTO=ops@example.com$' "$TMP/render.log" \
   && grep -q '^  POOL_VM_QUOTA_BYTES=' "$TMP/render.log" \
   && grep -q '^  POOL_VM_TARGET_SIZE=' "$TMP/render.log"; then
    echo "OK   template-default audit reports MAILTO, POOL_VM_TARGET_SIZE, POOL_VM_QUOTA_BYTES"
else
    echo "FAIL template-default audit does not report MAILTO / pool sizing"; fail=1
fi

if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "CHECKS FAILED"; exit 1; fi
