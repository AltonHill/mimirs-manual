#!/bin/bash
# check.sh — validate the whole template: bash syntax, placeholder-in-
# command lint, YAML parse, disksetup by-path guard, Python compile,
# template-variable sanity, Kubernetes CIDR guard, identifier leak scan
# (deny-list read from $MIMIRS_DENYLIST, default
# ~/.config/mimirs-manual/leak-denylist.txt — kept outside the repo).
# CHECK_VARS=<file> points the CIDR guard at a site copy of the
# variables (default: 00-variables.sh; a relative path is relative to
# bcm/); a networks.csv beside that file
# (or CHECK_NETWORKS=<file>) joins the overlap set.
#
# Two modes:
#   check.sh          PUBLISHER mode (default) — every stage, leak scan
#                     included. Exit: 0 all passed · 1 a check failed ·
#                     2 leak scan NOT RUN (deny-list absent) — never green
#                     without the deny-list.
#   check.sh --site   SITE mode — every stage EXCEPT the identifier leak
#                     scan (it needs the out-of-repo deny-list, which a
#                     teammate on site does not have). Exit: 0 all those
#                     stages passed · 1 a check failed. Makes no
#                     anonymization claim.
# Run after any edit, before any commit (publisher mode). Python 3.8+;
# the YAML stage needs PyYAML, everything else is stdlib. Writes nothing
# into the tree.
set -uo pipefail
site_mode=0
for arg in "$@"; do
    case "$arg" in
        --site) site_mode=1 ;;
        -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
        *) printf 'check.sh: unknown argument %s (use --site, or no argument)\n' "$arg" >&2; exit 1 ;;
    esac
done
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1

fail=0
say() { printf '%s\n' "$*"; }
if [ "$site_mode" -eq 1 ]; then
    say "### SITE MODE — all stages except the identifier leak scan (publisher-only)"
fi

say "== bash -n on all code blocks =="
python3 - <<'EOF'
import re, subprocess, tempfile, os, glob
fails = 0
for fn in sorted(glob.glob("*.md")):
    for m in re.finditer(r'```(bash|sh)\n(.*?)```', open(fn).read(), re.S):
        with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as t:
            t.write(m.group(2)); tname = t.name
        r = subprocess.run(['bash', '-n', tname], capture_output=True, text=True)
        os.unlink(tname)
        if r.returncode != 0:
            fails += 1
            print(f"BASH-FAIL {fn}: {r.stderr.strip()[:120]}")
print(f"bash: {fails} failures")
exit(1 if fails else 0)
EOF
[ $? -ne 0 ] && fail=1

say "== placeholder-in-command lint (bash blocks) =="
# A <placeholder> token inside a ```bash/```sh fence is a command that
# breaks when pasted as written: unquoted it is a redirect (`<ADMIN-NET>`
# reads a file named ADMIN-NET), quoted it is a literal string handed to
# a real tool (a password of "<BMC-PASSWORD>", a node named "<node>").
# Both cases are linted: <UPPER-CASE> tokens and lower-case <tokens>
# (`<node>`, `<bmc-ip>`, `<sha256 from §6.2>`). Use a $VARIABLE instead —
# for a per-node value, `${NAME:?how to set it}` so an unset value stops
# the command. A lower-case token may not have whitespace right before
# its '>' (so `cat <<eof > file` is not mistaken for one).
# Not linted: full-line comments (lines whose first non-blank is '#').
# ALLOWLIST CONVENTION — for a legitimate construct, end the line with
#     # lint: allow-placeholder <why>
# The reason is mandatory, and the marker is per line, never per block.
python3 - <<'EOF'
import re, glob
TOKEN = re.compile(r'<[A-Z][A-Z0-9_-]*>|<[a-z](?:[^<>\n]*[^<>\s])?>')
ALLOW = re.compile(r'#\s*lint:\s*allow-placeholder\s+\S')
# Self-test: the lint must fire on known placeholders and stay quiet on
# ordinary shell (heredocs, redirects, comparisons) — or the stage fails.
MUST_HIT = ['ufw allow from <ADMIN-NET> to any port 22',
            'cmsh -c "device ; use <node> ; commit"',
            "--fingerprint '<paste node-1 SHA256 fingerprint here>'"]
MUST_PASS = ['cat > /etc/x <<EOF', 'cat <<eof > /etc/x', 'cmd 2>&1 | tee log',
             '[ "$a" -lt 3 ] && echo ok', 'chronyc tracking   # Stratum <= 4']
bad = [t for t in MUST_HIT if not TOKEN.search(t)] + \
      [t for t in MUST_PASS if TOKEN.search(t)]
if bad:
    print("LINT SELF-TEST FAILED:", bad)
    exit(1)
hits, allowed = [], 0
for fn in sorted(glob.glob("*.md")):
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
            hits.append(f"{fn}:{first + i}: {' '.join(toks)}  |  {line.strip()[:110]}")
for h in hits:
    print(f"PLACEHOLDER-IN-COMMAND {h}")
print(f"lint: {len(hits)} hit(s), {allowed} allowlisted line(s) "
      f"(upper- and lower-case <...> tokens; self-test OK)")
exit(1 if hits else 0)
EOF
[ $? -ne 0 ] && fail=1

say "== YAML parse on all code blocks =="
python3 - <<'EOF'
import re, glob, yaml
fails = 0
for fn in sorted(glob.glob("*.md")):
    for m in re.finditer(r'```yaml\n(.*?)```', open(fn).read(), re.S):
        try:
            list(yaml.safe_load_all(m.group(1)))
        except Exception as e:
            fails += 1
            print(f"YAML-FAIL {fn}: {str(e)[:120]}")
print(f"yaml: {fails} failures")
exit(1 if fails else 0)
EOF
[ $? -ne 0 ] && fail=1

say "== disksetup XML guard (by-path only) =="
# Fails closed (H1): every shipped disksetup XML — any *.xml file in the
# track, and every ```xml fence containing <diskSetup> in the docs — must
# name devices by /dev/disk/by-path/ only. Kernel names (/dev/nvmeXnY,
# /dev/sdX) reorder across boots; by-id/uuid change on drive replacement.
# Same validator the disklayouts pass runs before it applies a layout.
# Self-tested: the validator must reject a kernel-name XML first.
python3 - <<'EOF'
import glob, re, sys
sys.path.insert(0, "scripts")
from runbook import disksetup_violations
bad = ('<diskSetup><device><blockdev>/dev/nvme0n1</blockdev>'
       '<partition id="a"/></device></diskSetup>')
if not disksetup_violations(bad, "self-test"):
    print("DISKSETUP SELF-TEST FAILED: kernel-name XML not rejected"); sys.exit(1)
targets = []
for fn in sorted(glob.glob("**/*.xml", recursive=True)):
    targets.append((fn, open(fn).read()))
for fn in sorted(glob.glob("*.md")):
    text = open(fn).read()
    for m in re.finditer(r'```xml\n(.*?)```', text, re.S):
        if "<diskSetup" in m.group(1):
            line = text.count("\n", 0, m.start(1)) + 1
            targets.append((f"{fn}:{line} (xml fence)", m.group(1)))
errs = [e for label, text in targets for e in disksetup_violations(text, label)]
for e in errs:
    print(f"DISKSETUP-FAIL {e}")
if errs:
    print(f"disksetup guard: FAILED ({len(errs)} problem(s) in {len(targets)} XML(s))")
    sys.exit(1)
print(f"disksetup guard: {len(targets)} shipped disksetup XML(s), all by-path "
      f"(self-test OK)")
EOF
[ $? -ne 0 ] && fail=1

say "== python compile =="
# In memory — no __pycache__ written into the tree under validation.
python3 - <<'EOF' && say "compile: OK" || fail=1
import glob, sys
bad = 0
for fn in sorted(glob.glob("scripts/*.py")):
    try:
        compile(open(fn).read(), fn, "exec")
    except SyntaxError as e:
        bad += 1
        print(f"COMPILE-FAIL {fn}: {e}")
sys.exit(1 if bad else 0)
EOF

say "== template variable sanity =="
# A $VAR is OK if it is exported by 00-variables.sh OR assigned locally
# in the same doc (VAR=... / export VAR=...), e.g. BMC_IP, PG_POD.
python3 - <<'EOF'
import re, glob
fails = 0
defined = set(re.findall(r'^export ([A-Za-z_][A-Za-z0-9_]*)', open('00-variables.sh').read(), re.M))
undefined = []
for fn in sorted(glob.glob("*.md")):
    text = open(fn).read()
    local = set(re.findall(r'^[ \t]*(?:export[ \t]+)?([A-Z][A-Z0-9_]{2,})=', text, re.M))
    # multi-assignments on one export line: export A=x B=y
    for line in re.findall(r'^[ \t]*export[ \t]+(.*)$', text, re.M):
        local.update(re.findall(r'([A-Z][A-Z0-9_]{2,})=', line))
    for v in set(re.findall(r'\$([A-Z][A-Z0-9_]{2,})', text)):
        if v not in defined and v not in local and v != "ALL":
            undefined.append(f"{fn}: ${v}")
if undefined:
    fails = 1
    print("UNDEFINED VARS:"); [print(f"  {u}") for u in sorted(undefined)]
else:
    print("vars: all $UPPER_CASE vars are defined (variables.sh or local assignment)")
exit(fails)
EOF
[ $? -ne 0 ] && fail=1

say "== Kubernetes CIDR guard =="
# Fails closed (C1): each K8s net/mask must be a valid network address,
# and neither K8s range may overlap a site network, a DNS host, the admin
# source networks (14), or the other K8s range. When a site networks CSV
# (the `runbook.py networks --networks` input) sits beside the variables
# file as networks.csv — or CHECK_NETWORKS=<file> names one — its
# networks join the overlap set. This sees only the plan in these files —
# the customer's existing networks are an intake question (00a).
CHECK_VARS="${CHECK_VARS:-00-variables.sh}" CHECK_NETWORKS="${CHECK_NETWORKS:-}" python3 - <<'EOF'
import os, sys
sys.path.insert(0, "scripts")
from runbook import load_vars, cidr_guard   # one guard: check.sh and `runbook.py vars`
path = os.environ["CHECK_VARS"]
if not os.path.isfile(path):
    print(f"CIDR-FAIL variables file {path} not found"); sys.exit(1)
explicit = os.environ.get("CHECK_NETWORKS") or None
errs, summary = cidr_guard(load_vars(path), path, networks_path=explicit,
                           explicit=bool(explicit))
for e in errs:
    print(f"CIDR-FAIL {e}")
print(summary)
sys.exit(1 if errs else 0)
EOF
[ $? -ne 0 ] && fail=1

leak_notrun=0
if [ "$site_mode" -eq 1 ]; then
say "== identifier leak scan: SKIPPED (site mode — publisher-only stage) =="
else
say "== identifier leak scan (out-of-repo deny-list) =="
# The deny-list lives OUTSIDE the repo so the library never ships the
# identifiers it hunts. One pattern per line (Python regex, matched
# case-insensitively); blank lines and full-line '#' comments ignored.
# Scope: the whole repo (both tracks, docs, READMEs, companion), .git
# excluded. A missing or empty list is NOT RUN, never a pass.
DENYLIST="${MIMIRS_DENYLIST:-$HOME/.config/mimirs-manual/leak-denylist.txt}"
REPO_ROOT="$(cd .. && pwd)"
DENYLIST="$DENYLIST" REPO_ROOT="$REPO_ROOT" python3 - <<'EOF'
import os, re, sys, zipfile, zlib

denylist, root = os.environ["DENYLIST"], os.environ["REPO_ROOT"]

def not_run(why):
    bar = "!" * 72
    print(bar)
    print(f"!! LEAK SCAN: NOT RUN — {why}")
    print(f"!! deny-list path: {denylist}")
    print("!! (set MIMIRS_DENYLIST, or create the default file outside the repo)")
    print("!! No anonymization claim can be made from this run.")
    print(bar)
    sys.exit(3)

if not os.path.isfile(denylist) or not os.access(denylist, os.R_OK):
    not_run("deny-list file not found or unreadable")
if os.path.realpath(denylist).startswith(os.path.realpath(root) + os.sep):
    print(f"LEAK-SCAN: deny-list is inside the repo ({denylist}) — move it out")
    sys.exit(1)

patterns, bad = [], 0
with open(denylist, encoding="utf-8", errors="replace") as fh:
    for n, line in enumerate(fh, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line, re.I))
        except re.error as e:
            bad += 1
            print(f"LEAK-SCAN: deny-list line {n}: invalid pattern ({e})")
if bad:
    sys.exit(1)
if not patterns:
    not_run("deny-list contains no patterns")

def texts(path, data):
    """Yield (label, text) views of a file that a pattern could match."""
    if path.endswith((".docx", ".xlsx", ".pptx", ".odt")) and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith((".xml", ".rels")):
                    raw = z.read(name).decode("utf-8", "replace")
                    yield f"{name}", raw
                    yield f"{name} (text)", re.sub(r"<[^>]+>", "", raw)
        return
    if path.endswith(".pdf"):
        yield "raw", data.decode("latin-1")
        # metadata strings (Author/Producer/Title) are often UTF-16 hex
        yield "metadata", "\n".join(
            bytes.fromhex(h.decode()).decode("utf-16-be", "replace")
            for h in re.findall(rb"<FEFF((?:[0-9A-Fa-f]{2})+)>", data))
        for i, m in enumerate(re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S)):
            try:
                yield f"stream {i}", zlib.decompress(m.group(1)).decode("latin-1")
            except zlib.error:
                pass
        return
    yield "", data.decode("utf-8", "replace") if b"\0" not in data else data.decode("latin-1")

hits, scanned, partial = 0, 0, []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = sorted(d for d in dirnames if d != ".git")
    for fn in sorted(filenames):
        path = os.path.join(dirpath, fn)
        rel = os.path.relpath(path, root)
        with open(path, "rb") as fh:
            data = fh.read()
        scanned += 1
        if fn.endswith(".pdf"):
            partial.append(f"{rel} (glyph-encoded text may not be visible)")
        elif b"\0" in data and not fn.endswith((".docx", ".xlsx", ".pptx", ".odt")):
            partial.append(f"{rel} (binary: embedded strings/metadata only)")
        for label, text in texts(path, data):
            for ln, line in enumerate(text.splitlines(), 1):
                for p in patterns:
                    if p.search(line):
                        hits += 1
                        where = f"{rel}[{label}]" if label else rel
                        print(f"LEAK {where}:{ln}: {line.strip()[:160]}")
                        break

print(f"leak scan: {len(patterns)} patterns, {scanned} files under {root} (.git excluded)")
for p in partial:
    print(f"  partial coverage: {p}")
if hits:
    print(f"LEAK-SCAN: {hits} hit(s) found")
    sys.exit(1)
print("leak scan: clean")
EOF
case $? in
    0) ;;
    3) leak_notrun=1 ;;
    *) fail=1 ;;
esac
fi

say "== pass smoke test =="
smoke=0
sfail() { say "FAIL $*"; smoke=1; }
SMOKE="$(mktemp -d)"
trap 'rm -rf "$SMOKE"' EXIT
CRED_RE='(PASSWORD|API_KEY|TOKEN|SECRET|PRODUCT_KEY|LICENSE_KEY)'

# vars: credential-class names (*PASSWORD* *API_KEY* *TOKEN* *SECRET*
# *PRODUCT_KEY* *LICENSE_KEY*) stay placeholders by policy, so the pass
# must PASS on the honest template — and report the exclusion.
vars_out="$(./scripts/runbook.py --vars 00-variables.sh vars 2>&1)" \
    || sfail "vars pass: exits non-zero on the template: $(printf '%s' "$vars_out" | head -3)"
grep -q '^credential-class placeholders excluded by policy: [1-9]' <<<"$vars_out" \
    || sfail "vars pass: credential-class exclusion not reported"
grep -Eq "^  [A-Z0-9_]*${CRED_RE}[A-Z0-9_]*=" <<<"$vars_out" \
    && sfail "vars pass: a credential-class name was reported as unfilled"
# vars: template-default audit (B2) — reports, never fails. The template
# lists audited values (DOMAIN among them); a copy with DOMAIN changed
# drops DOMAIN from the list and keeps the rest.
grep -q '^TEMPLATE DEFAULTS still in place' <<<"$vars_out" \
    || sfail "vars pass: template-default audit missing on the template"
grep -q '^  DOMAIN=' <<<"$vars_out" || sfail "vars pass: audit does not list DOMAIN on the template"
grep -q '^cidr guard: service' <<<"$vars_out" || sfail "vars pass: CIDR guard not run"
sed 's|^export DOMAIN=.*|export DOMAIN="smoke-site.test"|' 00-variables.sh > "$SMOKE/dom-vars.sh"
dom_out="$(./scripts/runbook.py --vars "$SMOKE/dom-vars.sh" vars 2>&1)" || sfail "vars pass: a DOMAIN-only edit fails"
grep -q '^  DOMAIN=' <<<"$dom_out" && sfail "vars pass: audit lists a value that differs from the template"
grep -q '^  NFS_SERVER=' <<<"$dom_out" || sfail "vars pass: audit lost an unchanged value"
# vars: a planted K8s overlap fails the pass (C1 guard inside vars, B4).
sed 's|^export K8S_POD_NET=.*|export K8S_POD_NET="10.10.0.0"|' 00-variables.sh > "$SMOKE/overlap-vars.sh"
./scripts/runbook.py --vars "$SMOKE/overlap-vars.sh" vars >/dev/null 2>&1 \
    && sfail "vars pass: planted K8s CIDR overlap not refused"

./scripts/runbook.py --vars 00-variables.sh networks --dry-run >/dev/null 2>&1 || sfail "networks --dry-run"
./scripts/runbook.py --vars 00-variables.sh networks --networks scripts/networks-template.csv --dry-run >/dev/null 2>&1 || sfail "networks --networks --dry-run"
grep -q "grep -qx 'mgmtnet'" <<<"$(./scripts/runbook.py --vars 00-variables.sh networks --dry-run 2>/dev/null)" \
    || sfail "networks pass: add-or-verify check missing from the dry-run"
./scripts/runbook.py --vars 00-variables.sh images --dry-run >/dev/null 2>&1 || sfail "images --dry-run"
grep -q 'patch storageclass local-path' <<<"$(./scripts/runbook.py --vars 00-variables.sh storage --plan 7a --dry-run 2>/dev/null)" \
    || sfail "storage 7a: local-path un-default step missing"
./scripts/runbook.py --vars 00-variables.sh storage --plan 7b --dry-run >/dev/null 2>&1 || sfail "storage 7b --dry-run"

# disklayouts: no XML -> fails with the procedure; bad XML -> refused;
# by-path XML -> dry-run OK (one node, then category).
grep -q 'ls -l /dev/disk/by-path' <<<"$(./scripts/runbook.py --vars 00-variables.sh disklayouts 2>&1)" \
    || sfail "disklayouts: collection procedure not printed"
printf '<diskSetup><device><blockdev>/dev/nvme0n1</blockdev></device></diskSetup>\n' > "$SMOKE/bad.xml"
printf '<diskSetup><device><blockdev>/dev/disk/by-path/pci-0000:11:00.0-nvme-1</blockdev></device></diskSetup>\n' > "$SMOKE/good.xml"
grep -q '^REFUSED' <<<"$(./scripts/runbook.py --vars 00-variables.sh disklayouts --layout "$SMOKE/bad.xml" 2>&1)" \
    || sfail "disklayouts: kernel-name XML not refused"
./scripts/runbook.py --vars 00-variables.sh disklayouts --layout "$SMOKE/good.xml" --node gpu-worker-1 >/dev/null 2>&1 \
    || sfail "disklayouts: by-path XML rejected"
# D3 override: needs a reason; with one, proceeds, names reason + values,
# and appends its durable record to disk-layouts/OVERRIDES.log beside the
# variables file. The override runs against a scratch copy of the
# variables, so the record lands in $SMOKE, never in the tree.
ovl_before="$(stat -c '%s %Y' disk-layouts/OVERRIDES.log 2>/dev/null || echo absent)"
mkdir -p "$SMOKE/ovr"; cp 00-variables.sh "$SMOKE/ovr/00-variables.sh"
./scripts/runbook.py --vars "$SMOKE/ovr/00-variables.sh" disklayouts --layout "$SMOKE/bad.xml" --allow-non-by-path >/dev/null 2>&1 \
    && sfail "disklayouts: override accepted without --reason"
ovr="$(./scripts/runbook.py --vars "$SMOKE/ovr/00-variables.sh" disklayouts --layout "$SMOKE/bad.xml" --node gpu-worker-1 \
        --allow-non-by-path --reason "smoke-override-reason" 2>&1)" || sfail "disklayouts: override with --reason refused"
grep -q 'BY-PATH OVERRIDE IN USE' <<<"$ovr" && grep -q 'smoke-override-reason' <<<"$ovr" && grep -q '/dev/nvme0n1' <<<"$ovr" \
    || sfail "disklayouts: override warning does not name the reason and the non-by-path value"
grep -q 'pass=disklayouts .*scope=device gpu-worker-1 .*reason=smoke-override-reason | waived: /dev/nvme0n1$' \
    "$SMOKE/ovr/disk-layouts/OVERRIDES.log" 2>/dev/null \
    || sfail "disklayouts: override not recorded in disk-layouts/OVERRIDES.log (date, scope, reason, values)"
# an override whose record can't be written is refused (skipped as root,
# who can write a read-only file)
chmod a-w "$SMOKE/ovr/disk-layouts/OVERRIDES.log" 2>/dev/null
if [ -f "$SMOKE/ovr/disk-layouts/OVERRIDES.log" ] && [ ! -w "$SMOKE/ovr/disk-layouts/OVERRIDES.log" ]; then
  ./scripts/runbook.py --vars "$SMOKE/ovr/00-variables.sh" disklayouts --layout "$SMOKE/bad.xml" --node gpu-worker-1 \
      --allow-non-by-path --reason "smoke" >/dev/null 2>&1 && sfail "disklayouts: an override that could not be recorded was allowed"
fi
chmod u+w "$SMOKE/ovr/disk-layouts/OVERRIDES.log" 2>/dev/null
printf '<diskSetup><device></device></diskSetup>\n' > "$SMOKE/empty.xml"
./scripts/runbook.py --vars "$SMOKE/ovr/00-variables.sh" disklayouts --layout "$SMOKE/empty.xml" --node gpu-worker-1 \
    --allow-non-by-path --reason "smoke" >/dev/null 2>&1 && sfail "disklayouts: override waived a layout with no <blockdev>"

# generate-nodes: the skeleton must NOT validate; a filled copy must.
./scripts/generate-nodes.py --skeleton --ctrl 1 --workers 1 --gpus 2 > "$SMOKE/skel.csv" 2>/dev/null || sfail "generate-nodes --skeleton"
grep -q 'SKELETON' <<<"$(./scripts/generate-nodes.py --check "$SMOKE/skel.csv" 2>&1)" \
    || sfail "generate-nodes --check: skeleton MACs not refused"
sed 's/00:50:56:b3:/02:5e:10:aa:/' "$SMOKE/skel.csv" > "$SMOKE/filled.csv"
./scripts/generate-nodes.py --check "$SMOKE/filled.csv" >/dev/null 2>&1 || sfail "generate-nodes --check: filled inventory rejected"
grep -q '^gpu-worker-1,' "$SMOKE/skel.csv" || sfail "generate-nodes: GPU hostnames don't follow \$GPU_WORKERS"
./scripts/generate-nodes.py "$SMOKE/filled.csv" > "$SMOKE/prov.sh" 2>/dev/null && bash -n "$SMOKE/prov.sh" \
    || sfail "generate-nodes: generated script fails bash -n"
./scripts/generate-nodes.py --skeleton --gpus 200 >/dev/null 2>&1 && sfail "generate-nodes: BMC overflow not refused"
grep -q 'layoutgate --category' "$SMOKE/prov.sh" || sfail "generate-nodes: provision script has no 03.4 layout gate"

# nodes: the 03.4 fleet gate (I2, widened to devices — Q7), against a stub
# cmsh that serves each category's disksetup from $SMOKE/cats/<cat>.xml,
# `device ; list` from $SMOKE/devices.txt, each device-level disksetup
# from $SMOKE/devs/<host>.xml, and logs every call.
mkdir -p "$SMOKE/bin" "$SMOKE/cats" "$SMOKE/devs"; : > "$SMOKE/devices.txt"
cat > "$SMOKE/bin/cmsh" <<'STUB'
#!/bin/bash
printf '%s\n' "$2" >> "$STUB_LOG"
n="$(sed -n 's/.*use \([^ ;]*\).*/\1/p' <<<"$2")"
case "$2" in
  category*"get disksetup"*) [ -f "$STUB_CATS/$n.xml" ] && cat "$STUB_CATS/$n.xml" ;;
  device*"get disksetup"*)   [ -f "$STUB_DEVS/$n.xml" ] && cat "$STUB_DEVS/$n.xml" ;;
  "device ; list"|"device; list") cat "$STUB_DEVICES" ;;
esac
exit 0
STUB
chmod +x "$SMOKE/bin/cmsh"
gate() { STUB_LOG="$SMOKE/cmsh.log" STUB_CATS="$SMOKE/cats" STUB_DEVS="$SMOKE/devs" \
         STUB_DEVICES="$SMOKE/devices.txt" PATH="$SMOKE/bin:$PATH" "$@"; }
for c in $(tail -n +2 "$SMOKE/filled.csv" | cut -d, -f5 | sort -u); do cp "$SMOKE/good.xml" "$SMOKE/cats/$c.xml"; done
gpu_cat="$(grep ',gpu,' "$SMOKE/filled.csv" | head -1 | cut -d, -f5)"
cp "$SMOKE/bad.xml" "$SMOKE/cats/$gpu_cat.xml"
: > "$SMOKE/cmsh.log"
g="$(gate ./scripts/runbook.py --vars 00-variables.sh nodes --inventory "$SMOKE/filled.csv" --exec </dev/null 2>&1)" \
    && sfail "nodes gate: kernel-path category layout not refused"
grep -q "category $gpu_cat" <<<"$g" && grep -q '/dev/nvme0n1' <<<"$g" \
    || sfail "nodes gate: refusal does not name the category and the offending value"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" && sfail "nodes gate: an add command ran despite the refusal"
rm "$SMOKE/cats/$gpu_cat.xml"
gate ./scripts/runbook.py --vars 00-variables.sh nodes --inventory "$SMOKE/filled.csv" --exec </dev/null >/dev/null 2>&1 \
    && sfail "nodes gate: category with no layout not refused"
cp "$SMOKE/good.xml" "$SMOKE/cats/$gpu_cat.xml"
: > "$SMOKE/cmsh.log"
echo YES | gate ./scripts/runbook.py --vars 00-variables.sh nodes --inventory "$SMOKE/filled.csv" --exec >/dev/null 2>&1 \
    || sfail "nodes gate: by-path inventory refused"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" || sfail "nodes gate: by-path inventory did not provision"
cp "$SMOKE/bad.xml" "$SMOKE/cats/$gpu_cat.xml"
: > "$SMOKE/cmsh.log"
gate bash "$SMOKE/prov.sh" >/dev/null 2>&1 && sfail "provision-nodes.sh: ran past a kernel-path category layout"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" && sfail "provision-nodes.sh: an add command ran despite the gate"
# device level: gpu-worker-1 already exists in BCM and carries its own layout.
cp "$SMOKE/good.xml" "$SMOKE/cats/$gpu_cat.xml"
printf 'PhysicalNode  gpu-worker-1  02:5e:10:aa:00:02  %s  10.10.15.53\n' "$gpu_cat" > "$SMOKE/devices.txt"
cp "$SMOKE/bad.xml" "$SMOKE/devs/gpu-worker-1.xml"
: > "$SMOKE/cmsh.log"
g="$(gate ./scripts/runbook.py --vars 00-variables.sh nodes --inventory "$SMOKE/filled.csv" --exec </dev/null 2>&1)" \
    && sfail "nodes gate: device-level kernel-path layout not refused"
grep -q 'device gpu-worker-1' <<<"$g" && grep -q '/dev/nvme0n1' <<<"$g" \
    || sfail "nodes gate: device-level refusal does not name the device and the offending value"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" && sfail "nodes gate: an add ran despite a device-level refusal"
: > "$SMOKE/cmsh.log"
gate bash "$SMOKE/prov.sh" >/dev/null 2>&1 && sfail "provision-nodes.sh: ran past a device-level kernel-path layout"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" && sfail "provision-nodes.sh: an add ran despite a device-level refusal"
cp "$SMOKE/good.xml" "$SMOKE/devs/gpu-worker-1.xml"
: > "$SMOKE/cmsh.log"
echo YES | gate ./scripts/runbook.py --vars 00-variables.sh nodes --inventory "$SMOKE/filled.csv" --exec >/dev/null 2>&1 \
    || sfail "nodes gate: device-level by-path layout refused"
grep -q 'device ; use gpu-worker-1 ; get disksetup' "$SMOKE/cmsh.log" || sfail "nodes gate: device-level layout not read"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" || sfail "nodes gate: device-level by-path inventory did not provision"
ovl_after="$(stat -c '%s %Y' disk-layouts/OVERRIDES.log 2>/dev/null || echo absent)"
[ "$ovl_before" = "$ovl_after" ] || sfail "smoke test wrote disk-layouts/OVERRIDES.log into the tree"

# generate-users: accounts validate; placeholder passwords are refused.
./scripts/generate-users.py --check scripts/accounts-template.csv >/dev/null 2>&1 || sfail "generate-users --check"
./scripts/generate-users.py scripts/accounts-template.csv >/dev/null 2>&1 && sfail "generate-users: ran without --passwords"
( umask 077; { echo username,password; for u in bcm-admin ops-01 field-01 vendor-01; do echo "$u,<TEMP-$u>"; done; } > "$SMOKE/pw.csv" )
./scripts/generate-users.py scripts/accounts-template.csv --passwords "$SMOKE/pw.csv" >/dev/null 2>&1 \
    && sfail "generate-users: placeholder passwords not refused"

# render: credentials survive as $NAME; the tripwire fires on a live value
# (name only — the value must appear nowhere: not in the render, not in
# the warning).
./scripts/runbook.py --vars 00-variables.sh render --out "$SMOKE/render" >/dev/null 2>&1 || sfail "render"
[ -f "$SMOKE/render/README.md" ] && [ -f "$SMOKE/render/16-infiniband.md" ] || sfail "render: docs missing"
[ ! -f "$SMOKE/render/99-role-mapping.md" ] || sfail "render: 99-role-mapping.md not skipped"
grep -q 'set password \$BMC_PASSWORD' "$SMOKE/render/03-images-categories-nodes.md" \
    || sfail "render: \$BMC_PASSWORD did not survive as a reference"
grep -q -- '--docker-password="\$NGC_API_KEY"' "$SMOKE/render/09-nim-operator.md" \
    || sfail "render: \$NGC_API_KEY did not survive as a reference"
grep -rqE '<(BMC-PASSWORD|NGC-API-KEY|BCM-PRODUCT-KEY)>' "$SMOKE/render" \
    && sfail "render: a credential placeholder was substituted into the guide"
sed 's|^export BMC_PASSWORD=.*|export BMC_PASSWORD="smoke-LiveValue-93xq"|' 00-variables.sh > "$SMOKE/live-vars.sh"
cp ./*.md "$SMOKE/" 2>/dev/null
trip="$(./scripts/runbook.py --vars "$SMOKE/live-vars.sh" render --out "$SMOKE/render-live" 2>&1 >/dev/null)"
grep -q 'LIVE SECRETS LOADED NEAR A DELIVERABLE' <<<"$trip" || sfail "render tripwire did not fire"
grep -q 'BMC_PASSWORD' <<<"$trip" || sfail "render tripwire did not name BMC_PASSWORD"
grep -q 'smoke-LiveValue-93xq' <<<"$trip" && sfail "render: the live credential VALUE leaked into the warning"
grep -rq 'smoke-LiveValue-93xq' "$SMOKE/render-live" && sfail "render: the live credential VALUE leaked into the guide"

if [ "$smoke" -eq 0 ]; then say "passes: OK"; else fail=1; say "passes: FAILED"; fi

if [ "$fail" -ne 0 ]; then
    say "CHECKS FAILED"
    exit 1
elif [ "$site_mode" -eq 1 ]; then
    say "SITE CHECKS PASSED — every stage except the leak scan (no anonymization claim)"
    exit 0
elif [ "$leak_notrun" -ne 0 ]; then
    say "CHECKS INCOMPLETE — leak scan NOT RUN (no deny-list); other stages passed"
    exit 2
fi
say "ALL CHECKS PASSED"
exit 0
