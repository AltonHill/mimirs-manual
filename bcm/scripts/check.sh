#!/bin/bash
# check.sh — validate the whole template: bash syntax, placeholder-in-
# command lint, YAML parse, disksetup by-path guard, Python compile,
# template-variable sanity, dangling-reference lint (scripts/reflint.py),
# front door + docs/ lint (bash -n, placeholders, file references — M8
# R2), Kubernetes CIDR guard, cross-track IP guard (scripts/crosstrack.py:
# the templates, a self-test, and the site pair when both site files
# exist — M8 R1), identifier leak scan
# (deny-list read from $MIMIRS_DENYLIST, default
# ~/.config/mimirs-manual/leak-denylist.txt — kept outside the repo).
# CHECK_VARS=<file> points the CIDR guard at a site copy of the
# variables (default: site-vars.sh in --site mode when it exists, else
# the 00-variables.sh template; a relative path is relative to bcm/);
# a networks.csv beside that file
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

# bash_n_blocks FILE... — bash -n on every ```bash/```sh fence in FILEs.
# One function for the track's docs and (M8 R2) the front door + docs/.
bash_n_blocks() {
python3 - "$@" <<'EOF'
import re, subprocess, tempfile, os, sys
fails = 0
for fn in sys.argv[1:]:
    for m in re.finditer(r'```(bash|sh)\n(.*?)```', open(fn).read(), re.S):
        with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as t:
            t.write(m.group(2)); tname = t.name
        r = subprocess.run(['bash', '-n', tname], capture_output=True, text=True)
        os.unlink(tname)
        if r.returncode != 0:
            fails += 1
            print(f"BASH-FAIL {fn}: {r.stderr.strip()[:120]}")
print(f"bash: {fails} failures ({len(sys.argv) - 1} files)")
exit(1 if fails else 0)
EOF
}

say "== bash -n on all code blocks =="
bash_n_blocks *.md || fail=1

say "== placeholder-in-command lint (bash blocks) =="
# A <placeholder> token inside a ```bash/```sh fence is a command that
# breaks when pasted as written: unquoted it is a redirect (`<ADMIN-NET>`
# reads a file named ADMIN-NET), quoted it is a literal string handed to
# a real tool (a password of "<BMC-PASSWORD>", a node named "<node>").
# All cases are linted: <UPPER-CASE> tokens, lower-case <tokens>
# (`<node>`, `<bmc-ip>`, `<sha256 from §6.2>`) and mixed-case ones
# (`<Foo-Bar>`, `<Node IP>`). Use a $VARIABLE instead —
# for a per-node value, `${NAME:?how to set it}` so an unset value stops
# the command. A lower-case token may not have whitespace right before
# its '>' (so `cat <<eof > file` is not mistaken for one).
# Not linted: full-line comments (lines whose first non-blank is '#').
# ALLOWLIST CONVENTION — for a legitimate construct, end the line with
#     # lint: allow-placeholder <why>
# The reason is mandatory, and the marker is per line, never per block.
# placeholder_lint FILE... — one function for the track's docs and (M8 R2)
# the front door + docs/.
placeholder_lint() {
python3 - "$@" <<'EOF'
import re, sys
# Upper-case (<ADMIN-NET>), lower-case (<node>) and mixed-case (<Foo-Bar>,
# <Node IP>) tokens. The second alternative covers all three; the first
# is kept as the documented upper-case form.
TOKEN = re.compile(r'<[A-Z][A-Z0-9_-]*>|<[A-Za-z](?:[^<>\n]*[^<>\s])?>')
ALLOW = re.compile(r'#\s*lint:\s*allow-placeholder\s+\S')
# Self-test: the lint must fire on known placeholders and stay quiet on
# ordinary shell (heredocs, redirects, comparisons) — or the stage fails.
MUST_HIT = ['ufw allow from <ADMIN-NET> to any port 22',
            'cmsh -c "device ; use <node> ; commit"',
            "--fingerprint '<paste node-1 SHA256 fingerprint here>'",
            'ping -c 3 <Node-IP>', 'ssh root@<Foo-Bar> uptime', 'set mac <Mac Addr>']
MUST_PASS = ['cat > /etc/x <<EOF', 'cat <<eof > /etc/x', 'cmd 2>&1 | tee log',
             '[ "$a" -lt 3 ] && echo ok', 'chronyc tracking   # Stratum <= 4',
             'cat <<EOF >"$TMP"', 'diff <(sort a) <(sort b)']
bad = [t for t in MUST_HIT if not TOKEN.search(t)] + \
      [t for t in MUST_PASS if TOKEN.search(t)]
if bad:
    print("LINT SELF-TEST FAILED:", bad)
    exit(1)
hits, allowed = [], 0
for fn in sys.argv[1:]:
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
print(f"lint: {len(hits)} hit(s), {allowed} allowlisted line(s) in {len(sys.argv) - 1} files "
      f"(upper-, lower- and mixed-case <...> tokens; self-test OK)")
exit(1 if hits else 0)
EOF
}
placeholder_lint *.md || fail=1

say "== YAML parse on all code blocks =="
# L3: PyYAML is the one non-stdlib dependency of this gate. Without it the
# stage FAILS with a message naming the package (fail closed — the same
# rule as the Cerberus gate's pdftotext stage), never a bare traceback.
python3 - <<'EOF'
import re, glob, sys
try:
    import yaml
except ImportError:
    print("FAIL PyYAML not installed — the YAML blocks cannot be verified, so the "
          "gate fails (install python3-yaml / pip install pyyaml)")
    sys.exit(1)
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

say "== dangling-reference lint (sections, files, passes, ranges) =="
# Fails closed (Brief M6 / review M27): every section number (06.7),
# file name (12-disk-layouts.md, scripts/…), pass name (runbook.py X, and
# any list of passes) and section range ("01 → 16") in the docs, the
# variables template, the scripts and the front door must land on
# something that exists. Self-tested inside scripts/reflint.py.
python3 scripts/reflint.py || fail=1

say "== front door + docs/ lint (bash -n, placeholders, file references) =="
# Ruled M8 R2: the library's front door (README.md at the repo root) and
# everything under docs/ belong to neither track, so neither track's doc
# stages saw them. This gate takes them: the same bash -n and placeholder
# lint as the track's docs (the functions above), and reflint.py --docs
# for file names, pass names and pass lists (section numbers stay with
# each track's own scan — a front-door line may cite either track).
FRONT=(../README.md)
while IFS= read -r f; do FRONT+=("$f"); done < <(find ../docs -name '*.md' | sort)
bash_n_blocks "${FRONT[@]}" || fail=1
placeholder_lint "${FRONT[@]}" || fail=1
python3 scripts/reflint.py --docs "${FRONT[@]}" || fail=1

say "== Kubernetes CIDR guard =="
# Fails closed (C1): each K8s net/mask must be a valid network address,
# and neither K8s range may overlap a site network, a DNS host, the admin
# source networks (14), or the other K8s range. When a site networks CSV
# (the `runbook.py networks --networks` input) sits beside the variables
# file as networks.csv — or CHECK_NETWORKS=<file> names one — its
# networks join the overlap set. This sees only the plan in these files —
# the customer's existing networks are an intake question (00a).
# Which file: CHECK_VARS if set; in --site mode the site file
# (site-vars.sh) when it exists; otherwise the template itself.
if [ -z "${CHECK_VARS:-}" ]; then
    if [ "$site_mode" -eq 1 ] && [ -f site-vars.sh ]; then CHECK_VARS=site-vars.sh; else CHECK_VARS=00-variables.sh; fi
fi
CHECK_VARS="$CHECK_VARS" CHECK_NETWORKS="${CHECK_NETWORKS:-}" python3 - <<'EOF'
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

say "== cross-track IP guard (BCM <-> Cerberus) =="
# Ruled M8 R1: one site's two plans must not collide. scripts/crosstrack.py
# (the M7 comparison, now in the repo) reads each file with its own
# track's parser and fails on any overlap between the BCM networks, admin
# ranges, K8s ranges and DNS hosts and the Cerberus MGMT / Ceph / PBS
# networks, host addresses and DNS servers (one resolver shared by both
# tracks is not a collision). Three runs, each fail-closed:
#   1. the shipped templates — must be clean;
#   2. self-test — a planted overlap (Cerberus MGMT_NET = the BCM
#      template's NET_PROVISION, written as a plain NAME="value") must fire;
#   3. the SITE pair — only when BOTH site files exist (bcm/site-vars.sh,
#      proxmox-ceph/site-vars.sh; CROSSTRACK_BCM / CROSSTRACK_CERBERUS name
#      others). Either one absent: SKIPPED, said so, not failed.
CERB_TEMPLATE=../proxmox-ceph/runbook/00-overview/variables.sh
python3 scripts/crosstrack.py 00-variables.sh "$CERB_TEMPLATE" || fail=1
XT="$(mktemp -d)"
prov="$(sed -n 's/^export NET_PROVISION="\([^"]*\)".*/\1/p' 00-variables.sh)"
{ cat "$CERB_TEMPLATE"; printf 'MGMT_NET="%s"\n' "$prov"; } > "$XT/planted.sh"
xt_out="$(python3 scripts/crosstrack.py 00-variables.sh "$XT/planted.sh" 2>&1)"; rc=$?
rm -rf "$XT"
if [ "$rc" -eq 1 ] && grep -q "^CROSSTRACK-OVERLAP BCM NET_PROVISION $prov  <->  Cerberus MGMT_NET" <<<"$xt_out"; then
    say "crosstrack self-test: planted overlap (MGMT_NET = NET_PROVISION $prov) caught"
else
    say "CROSSTRACK SELF-TEST FAILED: a planted overlap was not caught (rc=$rc)"; fail=1
fi
XT_BCM="${CROSSTRACK_BCM:-site-vars.sh}"
XT_CERB="${CROSSTRACK_CERBERUS:-../proxmox-ceph/site-vars.sh}"
if [ -f "$XT_BCM" ] && [ -f "$XT_CERB" ]; then
    python3 scripts/crosstrack.py "$XT_BCM" "$XT_CERB" || fail=1
else
    missing_xt=""
    [ -f "$XT_BCM" ] || missing_xt="$missing_xt $XT_BCM"
    [ -f "$XT_CERB" ] || missing_xt="$missing_xt $XT_CERB"
    say "crosstrack site pair: SKIPPED — not both site files present (absent, relative to bcm/:$missing_xt); the templates are checked above"
fi

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
    # Scope (Loki, 2026-11-18): veil/ is excluded at the root — the
    # anonymizer's worked examples ARE the deny-list's subject matter
    # (our own lab names, carried as examples). Veil is policed by
    # its own assertGone suite; every other path stays swept.
    if os.path.relpath(dirpath, root) == ".":
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "veil"))
    else:
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

print(f"leak scan: {len(patterns)} patterns, {scanned} files under {root} (.git and veil/ excluded)")
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
# R4a (M8): DHCP_POOL is the 01 sign-off. On the template (the publisher's
# check) it is a NOTE; a site file with it empty or unset FAILS, naming
# it; filled, the same file passes.
grep -q '^NOTE: DHCP_POOL is empty in the template by design' <<<"$vars_out" \
    || sfail "vars pass R4a: no DHCP_POOL note on the template"
cp 00-variables.sh "$SMOKE/dhcp-empty.sh"
grep -v '^export DHCP_POOL=' 00-variables.sh > "$SMOKE/dhcp-unset.sh"
for f in dhcp-empty dhcp-unset; do
    d_out="$(./scripts/runbook.py --vars "$SMOKE/$f.sh" vars 2>&1)"; rc=$?
    { [ "$rc" -eq 1 ] && grep -q '^missing variables: DHCP_POOL$' <<<"$d_out" \
        && grep -q '^DHCP_POOL is required for the site' <<<"$d_out"; } \
        || sfail "vars pass R4a: a site file with DHCP_POOL ${f#dhcp-} was not failed by name (rc=$rc)"
done
sed 's|^export DHCP_POOL=.*|export DHCP_POOL="10.10.15.200-10.10.15.250"|' 00-variables.sh > "$SMOKE/dhcp-filled.sh"
./scripts/runbook.py --vars "$SMOKE/dhcp-filled.sh" vars >/dev/null 2>&1 \
    || sfail "vars pass R4a: a site file with DHCP_POOL filled was failed"
# R4b / R5 (M8): DHCP_POOL, BCM_HA_VIP, HELM_VERSION, RUNAI_UTILS_COMMIT are
# template variables and join the template-default audit (each ships
# empty, so the audit fires once a default ships — proven here on a
# value equal to its "default"); the blocks no longer set the pins locally.
python3 -c 'import sys; sys.path.insert(0, "scripts"); import runbook as r
names = ("DHCP_POOL", "BCM_HA_VIP", "HELM_VERSION", "RUNAI_UTILS_COMMIT")
tmpl = r.load_vars("00-variables.sh"); v = {n: "same-as-default" for n in names}
sys.exit(0 if all(n in tmpl for n in names) and set(r.template_default_audit(v, v)) == set(names) else 1)' \
    || sfail "vars pass R4b/R5: a new variable is missing from the template or the template-default audit"
grep -nE '^[[:space:]]*(HELM_VERSION|RUNAI_UTILS_COMMIT)=' ./*.md \
    && sfail "R5: a block still sets HELM_VERSION / RUNAI_UTILS_COMMIT locally"
sed -e 's|^export DOMAIN=.*|export DOMAIN="smoke-site.test"|' \
    -e 's|^export DHCP_POOL=.*|export DHCP_POOL="10.10.15.200-10.10.15.250"|' 00-variables.sh > "$SMOKE/dom-vars.sh"
dom_out="$(./scripts/runbook.py --vars "$SMOKE/dom-vars.sh" vars 2>&1)" || sfail "vars pass: a DOMAIN-only edit fails"
grep -q '^  DOMAIN=' <<<"$dom_out" && sfail "vars pass: audit lists a value that differs from the template"
grep -q '^  NFS_SERVER=' <<<"$dom_out" || sfail "vars pass: audit lost an unchanged value"
# vars: a planted K8s overlap fails the pass (C1 guard inside vars, B4).
sed 's|^export K8S_POD_NET=.*|export K8S_POD_NET="10.10.0.0"|' 00-variables.sh > "$SMOKE/overlap-vars.sh"
./scripts/runbook.py --vars "$SMOKE/overlap-vars.sh" vars >/dev/null 2>&1 \
    && sfail "vars pass: planted K8s CIDR overlap not refused"

# Site-file pattern (H5 / Fable B3): passes read bcm/site-vars.sh (or
# --vars FILE, before or after the pass name); the pristine template is
# refused by every pass but `vars`/`render` named explicitly; no site
# file at all is exit 2. The smoke runs against a scratch site copy.
mkdir -p "$SMOKE/site"; cp 00-variables.sh "$SMOKE/site/site-vars.sh"
SITEV="$SMOKE/site/site-vars.sh"
./scripts/runbook.py --vars 00-variables.sh images --dry-run >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] || sfail "site file: the pristine template was not refused by images (rc=$rc)"
./scripts/runbook.py images --vars 00-variables.sh --dry-run >/dev/null 2>&1 \
    && sfail "site file: template refused only when --vars precedes the pass"
if [ ! -e site-vars.sh ]; then
    ./scripts/runbook.py images --dry-run >/dev/null 2>&1; rc=$?
    [ "$rc" -eq 2 ] || sfail "site file: no site-vars.sh and no --vars did not exit 2 (rc=$rc)"
    ./scripts/generate-nodes.py --skeleton --gpus 1 >/dev/null 2>&1 \
        && sfail "site file: generate-nodes --skeleton ran without a site file"
else
    say "NOTE site-vars.sh exists in the tree — the no-site-file refusal test is skipped"
fi
./scripts/generate-nodes.py --skeleton --vars 00-variables.sh --gpus 1 >/dev/null 2>&1 \
    && sfail "site file: generate-nodes --skeleton accepted the pristine template"
./scripts/runbook.py images --vars "$SITEV" --dry-run >/dev/null 2>&1 \
    || sfail "site file: --vars after the pass name not honored"
# L1: --exec BEFORE the pass name must reach the pass (it used to be
# overwritten back to dry-run). Off a head node, exec refuses for want of
# cmsh — that refusal is the proof the flag arrived. --dry-run after the
# pass name wins over an earlier --exec.
if ! command -v cmsh >/dev/null 2>&1; then
    l1="$(./scripts/runbook.py --vars "$SITEV" --exec images </dev/null 2>&1 >/dev/null)"; rc=$?
    { [ "$rc" -ne 0 ] && grep -q 'cmsh` is not on PATH' <<<"$l1"; } \
        || sfail "L1: --exec before the pass name did not reach the pass (rc=$rc)"
    ./scripts/runbook.py --vars "$SITEV" --exec images --dry-run >/dev/null 2>&1 \
        || sfail "L1: --dry-run after the pass name did not win over an earlier --exec"
fi

./scripts/runbook.py --vars "$SITEV" networks --dry-run >/dev/null 2>&1 || sfail "networks --dry-run"
./scripts/runbook.py --vars "$SITEV" networks --networks scripts/networks-template.csv --dry-run >/dev/null 2>&1 || sfail "networks --networks --dry-run"
# L4: an unquoted comma (extra field) and a CSV that disagrees with the
# site file on a stock network are both refused.
sed 's|^usernet,.*|usernet,10.10.13.0/24,10.10.13.1,no,add,User access (MetalLB, ingress)|' \
    scripts/networks-template.csv > "$SMOKE/comma.csv"
grep -q 'quote a field that contains a comma' <<<"$(./scripts/runbook.py --vars "$SITEV" networks --networks "$SMOKE/comma.csv" --dry-run 2>&1)" \
    || sfail "networks L4: an unquoted comma in notes was not refused"
sed 's|^storagenet,10.10.12.0/24,|storagenet,10.10.99.0/24,|' scripts/networks-template.csv > "$SMOKE/drift.csv"
grep -q 'REFUSED: networks.csv disagrees with the site variables file' \
        <<<"$(./scripts/runbook.py --vars "$SITEV" networks --networks "$SMOKE/drift.csv" --dry-run 2>&1)" \
    || sfail "networks L4: CSV/site-file drift on a stock network was not refused"
grep -q "grep -qx 'mgmtnet'" <<<"$(./scripts/runbook.py --vars "$SITEV" networks --dry-run 2>/dev/null)" \
    || sfail "networks pass: add-or-verify check missing from the dry-run"
./scripts/runbook.py --vars "$SITEV" images --dry-run >/dev/null 2>&1 || sfail "images --dry-run"
grep -q 'patch storageclass local-path' <<<"$(./scripts/runbook.py --vars "$SITEV" storage --plan 7a --dry-run 2>/dev/null)" \
    || sfail "storage 7a: local-path un-default step missing"
./scripts/runbook.py --vars "$SITEV" storage --plan 7b --dry-run >/dev/null 2>&1 || sfail "storage 7b --dry-run"

# disklayouts: no XML -> fails with the procedure; bad XML -> refused;
# by-path XML -> dry-run OK (one node, then category).
grep -q 'ls -l /dev/disk/by-path' <<<"$(./scripts/runbook.py --vars "$SITEV" disklayouts 2>&1)" \
    || sfail "disklayouts: collection procedure not printed"
printf '<diskSetup><device><blockdev>/dev/nvme0n1</blockdev></device></diskSetup>\n' > "$SMOKE/bad.xml"
printf '<diskSetup><device><blockdev>/dev/disk/by-path/pci-0000:11:00.0-nvme-1</blockdev></device></diskSetup>\n' > "$SMOKE/good.xml"
grep -q '^REFUSED' <<<"$(./scripts/runbook.py --vars "$SITEV" disklayouts --layout "$SMOKE/bad.xml" 2>&1)" \
    || sfail "disklayouts: kernel-name XML not refused"
./scripts/runbook.py --vars "$SITEV" disklayouts --layout "$SMOKE/good.xml" --node gpu-worker-1 >/dev/null 2>&1 \
    || sfail "disklayouts: by-path XML rejected"
# D3 override: needs a reason; with one, proceeds, names reason + values,
# and appends its durable record to disk-layouts/OVERRIDES.log beside the
# variables file. The override runs against a scratch copy of the
# variables, so the record lands in $SMOKE, never in the tree.
ovl_before="$(stat -c '%s %Y' disk-layouts/OVERRIDES.log 2>/dev/null || echo absent)"
mkdir -p "$SMOKE/ovr"; cp 00-variables.sh "$SMOKE/ovr/site-vars.sh"
./scripts/runbook.py --vars "$SMOKE/ovr/site-vars.sh" disklayouts --layout "$SMOKE/bad.xml" --allow-non-by-path >/dev/null 2>&1 \
    && sfail "disklayouts: override accepted without --reason"
ovr="$(./scripts/runbook.py --vars "$SMOKE/ovr/site-vars.sh" disklayouts --layout "$SMOKE/bad.xml" --node gpu-worker-1 \
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
  ./scripts/runbook.py --vars "$SMOKE/ovr/site-vars.sh" disklayouts --layout "$SMOKE/bad.xml" --node gpu-worker-1 \
      --allow-non-by-path --reason "smoke" >/dev/null 2>&1 && sfail "disklayouts: an override that could not be recorded was allowed"
fi
chmod u+w "$SMOKE/ovr/disk-layouts/OVERRIDES.log" 2>/dev/null
printf '<diskSetup><device></device></diskSetup>\n' > "$SMOKE/empty.xml"
./scripts/runbook.py --vars "$SMOKE/ovr/site-vars.sh" disklayouts --layout "$SMOKE/empty.xml" --node gpu-worker-1 \
    --allow-non-by-path --reason "smoke" >/dev/null 2>&1 && sfail "disklayouts: override waived a layout with no <blockdev>"

# generate-nodes: the skeleton must NOT validate; a filled copy must.
./scripts/generate-nodes.py --skeleton --vars "$SITEV" --ctrl 1 --workers 1 --gpus 2 > "$SMOKE/skel.csv" 2>/dev/null || sfail "generate-nodes --skeleton"
grep -q ',00:00:5e:00:53:[0-9a-f][0-9a-f],' "$SMOKE/skel.csv" || sfail "generate-nodes: skeleton MACs are not RFC 7042 documentation MACs"
grep -q '00:50:56:b3:' "$SMOKE/skel.csv" && sfail "generate-nodes: skeleton still uses the pre-M6 VMware-OUI pattern"
sed 's/00:00:5e:00:53:/00:50:56:b3:00:/' "$SMOKE/skel.csv" > "$SMOKE/legacy.csv"
grep -q 'SKELETON' <<<"$(./scripts/generate-nodes.py --check "$SMOKE/legacy.csv" 2>&1)" \
    || sfail "generate-nodes --check: pre-M6 skeleton MACs (00:50:56:b3:) not refused"
./scripts/generate-nodes.py --skeleton --vars "$SITEV" --ctrl 3 --gpus 150 --workers 110 >/dev/null 2>&1 \
    && sfail "generate-nodes: a skeleton past the 255 documentation MACs was not refused"
grep -q 'SKELETON' <<<"$(./scripts/generate-nodes.py --check "$SMOKE/skel.csv" 2>&1)" \
    || sfail "generate-nodes --check: skeleton MACs not refused"
sed 's/00:00:5e:00:53:/02:5e:10:aa:00:/' "$SMOKE/skel.csv" > "$SMOKE/filled.csv"
./scripts/generate-nodes.py --check "$SMOKE/filled.csv" >/dev/null 2>&1 || sfail "generate-nodes --check: filled inventory rejected"
grep -q '^gpu-worker-1,' "$SMOKE/skel.csv" || sfail "generate-nodes: GPU hostnames don't follow \$GPU_WORKERS"
./scripts/generate-nodes.py --vars "$SITEV" "$SMOKE/filled.csv" > "$SMOKE/prov.sh" 2>/dev/null && bash -n "$SMOKE/prov.sh" \
    || sfail "generate-nodes: generated script fails bash -n"
./scripts/generate-nodes.py --skeleton --vars "$SITEV" --gpus 200 >/dev/null 2>&1 && sfail "generate-nodes: BMC overflow not refused"
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
g="$(gate ./scripts/runbook.py --vars "$SITEV" nodes --inventory "$SMOKE/filled.csv" --exec </dev/null 2>&1)" \
    && sfail "nodes gate: kernel-path category layout not refused"
grep -q "category $gpu_cat" <<<"$g" && grep -q '/dev/nvme0n1' <<<"$g" \
    || sfail "nodes gate: refusal does not name the category and the offending value"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" && sfail "nodes gate: an add command ran despite the refusal"
rm "$SMOKE/cats/$gpu_cat.xml"
gate ./scripts/runbook.py --vars "$SITEV" nodes --inventory "$SMOKE/filled.csv" --exec </dev/null >/dev/null 2>&1 \
    && sfail "nodes gate: category with no layout not refused"
cp "$SMOKE/good.xml" "$SMOKE/cats/$gpu_cat.xml"
: > "$SMOKE/cmsh.log"
echo YES | gate ./scripts/runbook.py --vars "$SITEV" nodes --inventory "$SMOKE/filled.csv" --exec >/dev/null 2>&1 \
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
g="$(gate ./scripts/runbook.py --vars "$SITEV" nodes --inventory "$SMOKE/filled.csv" --exec </dev/null 2>&1)" \
    && sfail "nodes gate: device-level kernel-path layout not refused"
grep -q 'device gpu-worker-1' <<<"$g" && grep -q '/dev/nvme0n1' <<<"$g" \
    || sfail "nodes gate: device-level refusal does not name the device and the offending value"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" && sfail "nodes gate: an add ran despite a device-level refusal"
: > "$SMOKE/cmsh.log"
gate bash "$SMOKE/prov.sh" >/dev/null 2>&1 && sfail "provision-nodes.sh: ran past a device-level kernel-path layout"
grep -q 'add physicalnode' "$SMOKE/cmsh.log" && sfail "provision-nodes.sh: an add ran despite a device-level refusal"
cp "$SMOKE/good.xml" "$SMOKE/devs/gpu-worker-1.xml"
: > "$SMOKE/cmsh.log"
echo YES | gate ./scripts/runbook.py --vars "$SITEV" nodes --inventory "$SMOKE/filled.csv" --exec >/dev/null 2>&1 \
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
grep -q '"\$BMC_PASSWORD" | cmsh -q' "$SMOKE/render/03-images-categories-nodes.md" \
    || sfail "render: \$BMC_PASSWORD did not survive as a reference"
grep -qF -- 'printf '\''%s'\'' "$NGC_API_KEY"' "$SMOKE/render/09-nim-operator.md" \
    || sfail "render: \$NGC_API_KEY did not survive as a reference"
grep -rqE '<(BMC-PASSWORD|NGC-API-KEY|BCM-PRODUCT-KEY)>' "$SMOKE/render" \
    && sfail "render: a credential placeholder was substituted into the guide"
# L2: fresh directory every time; --force replaces only a previous render;
# links resolve inside the deliverable (diagrams copied in, library-only
# files named as text).
./scripts/runbook.py --vars 00-variables.sh render --out "$SMOKE/render" >/dev/null 2>&1 \
    && sfail "render L2: an existing --out was overwritten without --force"
./scripts/runbook.py --vars 00-variables.sh render --out "$SMOKE/render" --force >/dev/null 2>&1 \
    || sfail "render L2: --force did not replace a previous render"
mkdir -p "$SMOKE/foreign" && : > "$SMOKE/foreign/keep.txt"
./scripts/runbook.py --vars 00-variables.sh render --out "$SMOKE/foreign" --force >/dev/null 2>&1 \
    && sfail "render L2: --force replaced a directory render did not write"
[ -f "$SMOKE/foreign/keep.txt" ] || sfail "render L2: a foreign directory lost its contents"
cp -r "$SMOKE/render" "$SMOKE/render-foreign" && : > "$SMOKE/render-foreign/keep2.txt"
./scripts/runbook.py --vars 00-variables.sh render --out "$SMOKE/render-foreign" --force >/dev/null 2>&1 \
    && sfail "render L2: --force replaced a render containing a foreign file"
[ -f "$SMOKE/render-foreign/keep2.txt" ] || sfail "render L2: a foreign file inside a render was deleted"
mkdir -p "$SMOKE/legacy" && : > "$SMOKE/legacy/.bcm-render" && : > "$SMOKE/legacy/keep.txt"
./scripts/runbook.py --vars 00-variables.sh render --out "$SMOKE/legacy" --force >/dev/null 2>&1 \
    && sfail "render L2: --force replaced a legacy marker-only directory"
[ -f "$SMOKE/legacy/keep.txt" ] || sfail "render L2: a legacy directory lost its contents"
[ -f "$SMOKE/render/diagrams/cluster-128.svg" ] || sfail "render L2: README's diagram not copied into the deliverable"
grep -q 'library file `bcm/00-variables.sh`' "$SMOKE/render/README.md" \
    || sfail "render L2: link to the variables template not rewritten as a library reference"
broken="$(python3 -I - "$SMOKE/render" <<'PYEOF'
import os, re, sys
out = sys.argv[1]
for fn in sorted(os.listdir(out)):
    if fn.endswith(".md"):
        for m in re.finditer(r"\]\(([^)\s]+)\)", open(os.path.join(out, fn)).read()):
            t = m.group(1).partition("#")[0]
            if t and not re.match(r"^[a-z][a-z0-9+.-]*:|^/", t) \
                    and not os.path.exists(os.path.join(out, t)):
                print(f"{fn}: {m.group(1)}")
PYEOF
)"
[ -z "$broken" ] || sfail "render L2: links pointing outside the deliverable: $broken"
# Tripwire, fail-closed (M4 slate #4; Cerberus parity since M5):
# (a) a live secret in the variables FILE -> render refuses, exit 3,
#     nothing written, value never printed — both the plain form and a
#     live default hidden inside ${NAME:-...};
for form in 'export BMC_PASSWORD="smoke-LiveValue-93xq"' \
            'export BMC_PASSWORD="${BMC_PASSWORD:-smoke-LiveValue-93xq}"'; do
    awk -v f="$form" '/^export BMC_PASSWORD=/{print f; next} {print}' 00-variables.sh > "$SMOKE/live-vars.sh"
    rm -rf "$SMOKE/render-live"
    trip="$(./scripts/runbook.py --vars "$SMOKE/live-vars.sh" render --out "$SMOKE/render-live" 2>&1 >/dev/null)"; rc=$?
    [ "$rc" -eq 3 ] || sfail "render tripwire: live secret in the file did not refuse with exit 3 (rc=$rc; ${form%%=*})"
    [ ! -e "$SMOKE/render-live" ] || sfail "render tripwire: a refused render still wrote output"
    grep -q 'REFUSED: LIVE SECRETS IN THE VARIABLES FILE' <<<"$trip" || sfail "render tripwire did not fire"
    grep -q 'BMC_PASSWORD' <<<"$trip" || sfail "render tripwire did not name BMC_PASSWORD"
    grep -q 'smoke-LiveValue-93xq' <<<"$trip" && sfail "render: the live credential VALUE leaked into the refusal"
done
# (b) a live secret only in the ENVIRONMENT (placeholder in the file) ->
#     warned by name, render succeeds, value absent from guide and output.
trip="$(BMC_PASSWORD="smoke-LiveValue-93xq" ./scripts/runbook.py --vars "$SITEV" render --out "$SMOKE/render-env" 2>&1 >/dev/null)" \
    || sfail "render: an environment-held secret made the render fail (must warn only)"
grep -q 'WARNING: LIVE SECRETS LOADED NEAR A DELIVERABLE (environment)' <<<"$trip" || sfail "render: environment-held secret not warned"
grep -q 'BMC_PASSWORD: holds a real value (environment)' <<<"$trip" || sfail "render: environment warning does not name BMC_PASSWORD"
grep -q 'smoke-LiveValue-93xq' <<<"$trip" && sfail "render: the live credential VALUE leaked into the warning"
grep -rq 'smoke-LiveValue-93xq' "$SMOKE/render-env" && sfail "render: the live credential VALUE leaked into the guide"
grep -q '"\$BMC_PASSWORD" | cmsh -q' "$SMOKE/render-env/03-images-categories-nodes.md" \
    || sfail "render: \$BMC_PASSWORD not kept as a reference with an environment-held secret"

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
