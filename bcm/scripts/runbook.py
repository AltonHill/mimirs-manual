#!/usr/bin/env python3
"""runbook.py — subroutine passes for the B300 deployment runbook.

Each pass performs one runbook job, driven by the site variables file (and CSV
inventories). Passes are small, reviewable, and re-runnable — the end
goal is one pass per runbook section, all driven by data files.

Site file (H5 / Fable B3 — the Cerberus pattern): the tracked template
00-variables.sh is never edited. Copy it to bcm/site-vars.sh (gitignored),
fill that in, and every pass reads it:
    cp 00-variables.sh site-vars.sh
--vars FILE names another site file, before or after the pass name. With
no --vars, bcm/site-vars.sh is used; if it does not exist the pass exits 2.
The pristine template is refused (exit 2) by every pass except `vars` and
`render`, which accept it only when named explicitly (--vars
00-variables.sh) and say so — that is the publisher's validation path,
never a site's.

Usage:
    scripts/runbook.py vars [--vars site-vars.sh] [--networks networks.csv]
    scripts/runbook.py networks [--networks networks.csv] [--dry-run|--exec]
    scripts/runbook.py images [--vars site-vars.sh] [--dry-run|--exec]
    scripts/runbook.py disklayouts [--type T --category C] [--node N] [--dry-run|--exec]
    scripts/runbook.py layoutgate --category C [--category C2 ...] [--device D ...]
    scripts/runbook.py nodes --inventory inventory.csv [--dry-run|--exec]
      (disklayouts / layoutgate / nodes also take the logged override:
       --allow-non-by-path --reason "<why by-path fails on this host type>")
    scripts/runbook.py storage --plan 7a|7b [--dry-run|--exec]
    scripts/runbook.py users --accounts accounts.csv --passwords FILE [--dry-run|--exec]
    scripts/runbook.py render --out site-docs/bcm/

--dry-run (default) prints exactly what would run. --exec asks for
confirmation, then runs it. cmsh passes must run on the BCM head node
as root, after section 02 has installed BCM — under --exec they refuse
to start if `cmsh` is not on PATH.

Re-runnable: every create step is add-or-verify. The pass checks
whether the object already exists; if it does, the create is skipped
with a NOTE and the align/set steps that follow converge its values.
A create that still reports "already exists" is success-with-note,
never an abort. Any other failure stops the pass.

Python 3.12 stock on Ubuntu 24.04 / DGX OS — stdlib only.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REQUIRED_VARS = [
    "DOMAIN", "CLUSTER_NAME",
    "NET_PROVISION", "NET_STORAGE", "NET_USER", "NET_OOB",
    "GW_PROVISION", "GW_USER", "GW_OOB",
    "BCM_HEADNODE", "K8S_CTRL", "GPU_WORKERS",
    "IP_KGATEWAY", "IP_RUNAI", "IP_INFERENCE",
    "IMG_CP", "IMG_WRK", "IMG_GPU",
    "CAT_CP", "CAT_WRK", "CAT_GPU",
    "KERNEL_VER", "NFS_SERVER", "NFS_SHARE", "NFS_MOUNT",
]


def load_vars(path):
    """Parse `export KEY="value"` lines from a variables.sh file.

    Quote-aware: a trailing `# comment` is stripped only when it is
    outside the quoted value.
    """
    with open(path) as f:
        return parse_vars_text(f.read())


def parse_vars_text(text):
    """load_vars() on a string (e.g. the committed template from git)."""
    vars_ = {}
    for line in text.splitlines():
        m = re.match(r'\s*export\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
        if not m:
            continue
        key, rest = m.group(1), m.group(2).strip()
        if rest.startswith('"'):
            end = rest.find('"', 1)
            val = rest[1:end] if end != -1 else rest[1:]
        elif rest.startswith("'"):
            end = rest.find("'", 1)
            val = rest[1:end] if end != -1 else rest[1:]
        else:
            val = rest.split("#", 1)[0].strip()
        vars_[key] = val
    return vars_


def load_script_module(filename):
    """Import a hyphenated script filename (generate-nodes.py) as a module."""
    import importlib.util
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(
        filename.replace("-", "_").replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def confirm(n, what):
    ans = input(f"About to {what} ({n} commands). Type YES to proceed: ")
    if ans.strip() != "YES":
        print("aborted.")
        sys.exit(1)


# "already exists" is success-with-note, never an abort. The exact cmsh
# wording is not pinned down from the repo — this pattern is deliberately
# loose, and the existence check below is the primary mechanism.
ALREADY_EXISTS_RE = re.compile(r"already exists|exists already|duplicate", re.I)


class Step:
    """One command in a pass.

    exists: optional (list_cmd, name) — under --exec the runner runs
        list_cmd first; if `name` appears as the first field of a line of
        its output, the object is already there and `cmd` is skipped with
        a NOTE (add-or-verify). Following set/align steps still run, so the
        object converges to the plan either way.
    tolerate_exists: a non-zero exit whose output matches
        ALREADY_EXISTS_RE is success-with-note.
    """

    def __init__(self, cmd, exists=None, tolerate_exists=False, what=None):
        self.cmd = cmd
        self.exists = exists
        self.tolerate_exists = tolerate_exists
        self.what = what or cmd

    def shell(self):
        """Shell rendering for --dry-run (copy-pasteable equivalent)."""
        if not self.exists:
            return self.cmd
        list_cmd, name = self.exists
        return (f"{list_cmd} | awk '{{print $1}}' | grep -qx '{name}' "
                f"&& echo 'NOTE: {name} already exists — verifying' || {self.cmd}")


def _as_step(c):
    return c if isinstance(c, Step) else Step(c)


def _listed(output, name):
    for line in output.splitlines():
        f = line.split()
        if f and f[0] == name:
            return True
    return False


def _run_capture(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _require_tool(steps, tool="cmsh"):
    if any(tool in s.cmd for s in steps) and not shutil.which(tool):
        print(f"REFUSED: `{tool}` is not on PATH. This pass runs on the BCM head "
              f"node, after section 02 has installed BCM — run --dry-run "
              f"anywhere, --exec only there.", file=sys.stderr)
        sys.exit(2)


def run_commands(cmds, dry_run, what):
    steps = [_as_step(c) for c in cmds]
    if dry_run:
        for st in steps:
            print(st.shell())
        print(f"# dry-run: {len(steps)} commands (use --exec to run)", file=sys.stderr)
        return
    _require_tool(steps)
    confirm(len(steps), what)
    notes = 0
    for st in steps:
        if st.exists:
            list_cmd, name = st.exists
            rc, out = _run_capture(list_cmd)
            if rc != 0:
                print(f"FAILED (rc={rc}) checking for {name}: {list_cmd}", file=sys.stderr)
                sys.exit(1)
            if _listed(out, name):
                print(f"NOTE: {name} already exists — create skipped; "
                      f"the align/set steps that follow verify it", flush=True)
                notes += 1
                continue
        print(f"+ {st.cmd}", flush=True)
        rc, out = _run_capture(st.cmd)
        if rc != 0:
            if st.tolerate_exists and ALREADY_EXISTS_RE.search(out):
                print(f"NOTE: {st.what}: already exists (rc={rc}) — "
                      f"success-with-note, continuing", flush=True)
                notes += 1
                continue
            print(f"FAILED (rc={rc}): {st.cmd}", file=sys.stderr)
            sys.exit(1)
    print(f"pass complete ({notes} already-present note(s)).")


# ---------------------------------------------------------------- passes

# Credential class (H6). Any variable whose NAME contains one of these
# (case-insensitive) is a credential:
#     *PASSWORD*  *API_KEY*  *TOKEN*  *SECRET*  *PRODUCT_KEY*  *LICENSE_KEY*
# Policy: credentials never appear in a deliverable, placeholder or real.
#   - `vars` does not fail on them (they stay placeholders in the template);
#   - `render` never substitutes them: the rendered guide keeps `$NAME`
#     for the operator's shell to expand at run time;
#   - `render` REFUSES (exit 3) if one holds a real value in the
#     variables file, and warns loudly if one is live only in the
#     environment — names only, never values.
CREDENTIAL_PATTERNS = ("PASSWORD", "API_KEY", "TOKEN", "SECRET",
                       "PRODUCT_KEY", "LICENSE_KEY")
CREDENTIAL_CLASS_RE = re.compile("|".join(CREDENTIAL_PATTERNS), re.I)
CREDENTIAL_CLASS_TEXT = "/".join(f"*{p}*" for p in CREDENTIAL_PATTERNS)


def is_credential_class(name):
    return bool(CREDENTIAL_CLASS_RE.search(name))


SHELL_DEFAULT_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:?[-=](.*)\}$")


def is_placeholder_value(value):
    """True if a value is NOT a live secret: empty, a <PLACEHOLDER>
    (anywhere in the value, e.g. ${X:-<X>}), or pure shell indirection
    ($VAR, ${VAR}, $(cmd)) resolved by the operator's shell at run time.
    A shell default form ${X:-value} is judged by its DEFAULT: the
    template's ${X:-<X>} is a placeholder, but ${X:-hunter2} carries a
    live value in the file and is not."""
    v = (value or "").strip()
    m = SHELL_DEFAULT_RE.match(v)
    if m:
        return is_placeholder_value(m.group(1))
    return (not v) or bool(re.search(r"<[^<>]+>", v)) or v.startswith("$")


TRACK_DIR = os.path.dirname(HERE)
TEMPLATE_VARS = os.path.join(TRACK_DIR, "00-variables.sh")
SITE_VARS = os.path.join(TRACK_DIR, "site-vars.sh")
# Passes that may read the pristine template — only when it is named
# explicitly, and with a NOTE. Everything else refuses it (fail closed).
TEMPLATE_OK_PASSES = ("vars", "render")


def is_template(path):
    return (os.path.exists(path) and os.path.exists(TEMPLATE_VARS)
            and os.path.samefile(path, TEMPLATE_VARS))


def resolve_vars(given, pass_name):
    """The site variables file for this run, or exit 2 (fail closed).

    --vars FILE (absolute, cwd-relative, or relative to bcm/) wins; with
    no --vars, bcm/site-vars.sh. Never falls back to the template: its
    shipped defaults describe the lab build, and a pass run against them
    acts on the wrong site."""
    copy_hint = f"cp {TEMPLATE_VARS} {SITE_VARS}   # then fill in the site's values"
    if given is None:
        if not os.path.isfile(SITE_VARS):
            print("REFUSED: no site variables file. Create the default, or pass --vars FILE:",
                  file=sys.stderr)
            print(f"         {copy_hint}", file=sys.stderr)
            print("         Refusing to run against the template defaults "
                  "(00-variables.sh is the pristine template; never edit it).",
                  file=sys.stderr)
            sys.exit(2)
        path = SITE_VARS
    else:
        path = given
        if not os.path.exists(path):
            alt = os.path.join(TRACK_DIR, given)
            if os.path.exists(alt):
                path = alt
        if not os.path.isfile(path):
            print(f"REFUSED: --vars file not found: {given}", file=sys.stderr)
            sys.exit(2)
    if is_template(path):
        if pass_name in TEMPLATE_OK_PASSES and given is not None:
            print(f"# NOTE: reading the pristine TEMPLATE ({path}) — the publisher's "
                  f"check, not a site run; every value is a lab default.", file=sys.stderr)
        else:
            print(f"REFUSED: {path} is the pristine template, not a site file.",
                  file=sys.stderr)
            print(f"         {copy_hint}", file=sys.stderr)
            print("         and pass that (or leave --vars off: site-vars.sh is the default).",
                  file=sys.stderr)
            sys.exit(2)
    return path

# Template-default audit (Cluster A / review M2 — the M3 method built for
# Cerberus). The template's defaults are plausible-looking values, not
# <placeholders>, so a placeholder test passes a file nobody edited.
# `vars` therefore REPORTS every audited value still identical to the
# shipped template's default. Reporting, not failing: a site may
# legitimately keep one (say NFS_MOUNT=/mnt/data) — confirm each.
# Audited: identity, every network / gateway / DNS / admin-source range,
# node and service IPs, the K8s ranges (00a: verify against the
# customer's estate), storage, kernel, the BMC login name. Not audited:
# role names, image /
# category names, versions (shared conventions a site may keep), values
# derived from another variable (FQDN_*, DNS_IP, RUNAI_ADMIN_USER), and
# the credential class (excluded by policy, H6).
AUDITED_VARS = (
    "DOMAIN", "CLUSTER_NAME", "CLIENT_ORG",
    "NET_PROVISION", "GW_PROVISION", "NET_STORAGE", "NET_USER", "GW_USER",
    "NET_OOB", "GW_OOB", "NET_DNS1", "NET_DNS2",
    "NET_ADMIN_SRC_A", "NET_ADMIN_SRC_B",
    "K8S_SERVICE_NET", "K8S_POD_NET",
    "BCM_HEADNODE_IP", "K8S_CTRL_IPS", "GPU_WORKER_IPS",
    "IP_KGATEWAY", "IP_RUNAI", "IP_INFERENCE",
    "KERNEL_VER", "NFS_SERVER", "NFS_SHARE", "NFS_MOUNT",
    "BMC_USER",
)


def _committed_template():
    """The template as committed at git HEAD, or None (not a git tree,
    git missing, file untracked). Used when the variables file IS the
    template, so an edit-in-place is still audited against what shipped."""
    try:
        r = subprocess.run(
            ["git", "-C", os.path.dirname(TEMPLATE_VARS), "show",
             "HEAD:./" + os.path.basename(TEMPLATE_VARS)],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def template_defaults(vars_path):
    """(defaults dict, note) for the template-default audit, or (None, why)."""
    same_file = is_template(vars_path)
    committed = _committed_template()
    if not same_file:
        # A site file is compared against what SHIPPED: the committed
        # template when git can show it (so an edit-in-place of the
        # tracked template cannot hide a default), else the file on disk.
        if committed is not None:
            note = "compared against the committed template (git HEAD)"
            if os.path.isfile(TEMPLATE_VARS):
                with open(TEMPLATE_VARS) as f:
                    if parse_vars_text(f.read()) != parse_vars_text(committed):
                        note += (" — NOTE: the tracked 00-variables.sh has been edited "
                                 "in place; site values belong in site-vars.sh only")
            return parse_vars_text(committed), note
        if not os.path.isfile(TEMPLATE_VARS):
            return None, f"shipped template not found at {TEMPLATE_VARS}"
        return load_vars(TEMPLATE_VARS), f"compared against {TEMPLATE_VARS}"
    with open(TEMPLATE_VARS) as f:
        on_disk = f.read()
    if committed is None:
        return parse_vars_text(on_disk), (
            "auditing the TEMPLATE itself and git HEAD is unavailable — if you "
            "edited it in place, this audit cannot tell your values from the "
            "shipped ones")
    if parse_vars_text(committed) == parse_vars_text(on_disk):  # values, not comments
        return parse_vars_text(committed), (
            "auditing the pristine TEMPLATE itself — every audited value below "
            "is a shipped default by definition")
    return parse_vars_text(committed), (
        "the template was edited in place — compared against the committed "
        "template (git HEAD); site values belong in a site copy, not a "
        "tracked file")


def template_default_audit(vars_, defaults):
    """Audited names whose loaded value is identical to the shipped default."""
    return [k for k in AUDITED_VARS
            if k in defaults and vars_.get(k, "") != "" and vars_.get(k) == defaults[k]]


def cidr_guard(vals, path, networks_path=None, explicit=False):
    """Kubernetes CIDR guard (C1). Returns (errors, summary).

    Each K8s net/mask must be a valid network address, and neither K8s
    range may overlap a site network, a DNS host, the admin source
    networks (14), or the other K8s range. A networks CSV (the
    `networks --networks` input) joins the overlap set: `networks_path`,
    else networks.csv beside the variables file if present. `explicit`
    makes a missing networks_path an error. Sees only this plan — the
    customer's existing networks are an intake question (00a).
    Shared by `runbook.py vars` and scripts/check.sh."""
    import ipaddress
    errs = []

    def get(name):
        if not vals.get(name):
            errs.append(f"{name} is missing or empty in {path}")
        return vals.get(name, "")

    k8s = {}
    for label, net, mask in (("service", "K8S_SERVICE_NET", "K8S_SERVICE_MASK"),
                             ("pod", "K8S_POD_NET", "K8S_POD_MASK")):
        spec = f"{get(net)}/{get(mask)}"
        try:
            k8s[label] = ipaddress.ip_network(spec, strict=True)
        except ValueError as e:
            errs.append(f"K8s {label} range {spec} ({net}/{mask}) is not a valid "
                        f"network address: {e}")
            try:  # still check what the operator would actually get
                k8s[label] = ipaddress.ip_network(spec, strict=False)
                errs.append(f"  (as entered it resolves to {k8s[label]})")
            except ValueError:
                pass
    site = {}
    for name in ("NET_PROVISION", "NET_STORAGE", "NET_USER", "NET_OOB"):
        try:
            site[name] = ipaddress.ip_network(get(name), strict=True)
        except ValueError as e:
            errs.append(f"site network {name}={vals.get(name, '')} is invalid: {e}")
    for name in ("NET_DNS1", "NET_DNS2"):
        try:
            site[name] = ipaddress.ip_network(f"{get(name)}/32", strict=True)
        except ValueError as e:
            errs.append(f"DNS host {name}={vals.get(name, '')} is invalid: {e}")
    # admin source networks (14.1): A required, B optional (empty = unused)
    for name, required in (("NET_ADMIN_SRC_A", True), ("NET_ADMIN_SRC_B", False)):
        v = get(name) if required else vals.get(name, "")
        if not v:
            continue
        try:
            site[name] = ipaddress.ip_network(v, strict=True)
        except ValueError as e:
            errs.append(f"admin source network {name}={v} is invalid: {e}")
    csv_path = networks_path or os.path.join(
        os.path.dirname(os.path.abspath(path)), "networks.csv")
    csv_note = f"no networks.csv beside {path}"
    if os.path.isfile(csv_path):
        rows = _load_network_rows(csv_path)      # exits non-zero on a bad CSV
        for r in rows:
            site[f"networks.csv:{r['name']}"] = ipaddress.ip_network(r["cidr"], strict=False)
        csv_note = f"{len(rows)} networks from {csv_path}"
    elif explicit:
        errs.append(f"networks file {csv_path} does not exist")
    for label, kn in k8s.items():
        for name, sn in site.items():
            if kn.overlaps(sn):
                errs.append(f"K8s {label} range {kn} overlaps {name} ({sn})")
    if len(k8s) == 2 and k8s["service"].overlaps(k8s["pod"]):
        errs.append(f"K8s service range {k8s['service']} overlaps pod range {k8s['pod']}")
    if errs:
        return errs, f"cidr guard: FAILED ({path})"
    return errs, (f"cidr guard: service {k8s['service']}, pod {k8s['pod']} clear of "
                  f"{len(site)} site networks/hosts and each other ({path}; {csv_note})")


def pass_vars(vars_, args):
    """Runbook 00: validate the variables file.

    FAILS (exit 1) on: a missing required variable; an unfilled <...>
    placeholder (credential class excluded by policy, H6); a Kubernetes
    CIDR guard failure (C1 — same guard as check.sh).
    REPORTS (never fails): audited values still identical to the shipped
    template's defaults — the template-default audit."""
    missing = [k for k in REQUIRED_VARS if not vars_.get(k)]
    placeholders = {k: v for k, v in vars_.items() if "<" in v and ">" in v}
    excluded = sorted(k for k in placeholders if is_credential_class(k))
    placeholders = {k: v for k, v in placeholders.items() if k not in excluded}
    if missing:
        print("missing variables:", ", ".join(missing))
    if placeholders:
        print("unfilled placeholders:")
        for k, v in placeholders.items():
            print(f"  {k}={v}")
    print(f"credential-class placeholders excluded by policy: {len(excluded)}"
          + (f" ({', '.join(excluded)})" if excluded else "")
          + f" — names matching {CREDENTIAL_CLASS_TEXT} stay"
            " placeholders; the shell supplies them at run time")

    # Kubernetes CIDR guard — fails closed, like check.sh's stage.
    cidr_errs, cidr_summary = cidr_guard(
        vars_, args.vars, networks_path=getattr(args, "networks", None),
        explicit=bool(getattr(args, "networks", None)))
    for e in cidr_errs:
        print(f"CIDR-FAIL {e}")
    print(cidr_summary)

    # Template-default audit — reporting only.
    defaults, note = template_defaults(args.vars)
    if defaults is None:
        print(f"template-default audit: NOT RUN — {note}")
    else:
        same = template_default_audit(vars_, defaults)
        if same:
            print(f"TEMPLATE DEFAULTS still in place ({len(same)} of "
                  f"{len(AUDITED_VARS)} audited values; {note}) — "
                  "reported, not failed: confirm each is really the site's value:")
            for k in same:
                print(f"  {k}={vars_[k]}")
        else:
            print(f"template-default audit: no audited value matches a shipped "
                  f"default ({len(AUDITED_VARS)} audited; {note})")

    if missing or placeholders or cidr_errs:
        sys.exit(1)
    print(f"OK: {len(vars_)} variables loaded, {len(REQUIRED_VARS)} required all set, "
          f"no unfilled placeholders, CIDR guard clean.")


NET_CSV_COLUMNS = ["name", "cidr", "gateway", "mgmt_allowed", "action", "notes"]
NET_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _default_network_rows(vars_):
    """The stock 4-network plan, built from the site variables file."""
    return [
        {"name": "internalnet", "cidr": vars_["NET_PROVISION"],
         "gateway": vars_["GW_PROVISION"], "mgmt_allowed": "no",
         "action": "align", "notes": "BCM provisioning"},
        {"name": "storagenet", "cidr": vars_["NET_STORAGE"],
         "gateway": "", "mgmt_allowed": "no",
         "action": "add", "notes": "NFS storage"},
        {"name": "usernet", "cidr": vars_["NET_USER"],
         "gateway": vars_["GW_USER"], "mgmt_allowed": "no",
         "action": "add", "notes": "User/service access"},
        {"name": "mgmtnet", "cidr": vars_["NET_OOB"],
         "gateway": vars_["GW_OOB"], "mgmt_allowed": "yes",
         "action": "add", "notes": "Hardware/BMC management"},
    ]


def _load_network_rows(path):
    """Load networks.csv (skips # comment lines)."""
    import csv as _csv
    import ipaddress as _ip
    rows = []
    with open(path, newline="") as f:
        reader = _csv.DictReader(
            ln for ln in f
            if ln.strip() and not ln.lstrip().startswith("#")
        )
        if reader.fieldnames != NET_CSV_COLUMNS:
            print(f"ERROR: {path}: expected header exactly: "
                  f"{','.join(NET_CSV_COLUMNS)}", file=sys.stderr)
            sys.exit(1)
        extra = []
        for i, r in enumerate(reader, start=2):
            if not (r.get("name") or "").strip():
                continue
            if r.get(None):
                # L4: an unquoted comma in a field (usually notes) makes
                # extra columns; csv would silently drop them.
                extra.append(f"networks.csv:{r['name'].strip()}: "
                             f"{len(NET_CSV_COLUMNS) + len(r[None])} fields, expected "
                             f"{len(NET_CSV_COLUMNS)} — quote a field that contains a comma")
            rows.append({k: (r.get(k) or "").strip() for k in NET_CSV_COLUMNS})
    errors, seen = list(extra), set()
    for r in rows:
        tag = f"networks.csv:{r['name'] or '?'}"
        if not NET_NAME_RE.match(r["name"]):
            errors.append(f"{tag}: bad network name '{r['name']}'")
        if r["name"] in seen:
            errors.append(f"{tag}: duplicate network name")
        seen.add(r["name"])
        try:
            _ip.ip_network(r["cidr"], strict=False)
        except ValueError:
            errors.append(f"{tag}: bad CIDR '{r['cidr']}'")
        if r["gateway"]:
            try:
                _ip.ip_address(r["gateway"])
            except ValueError:
                errors.append(f"{tag}: bad gateway '{r['gateway']}'")
        if r["mgmt_allowed"] not in ("yes", "no"):
            errors.append(f"{tag}: mgmt_allowed must be yes|no")
        if r["action"] not in ("add", "align"):
            errors.append(f"{tag}: action must be add|align")
    if errors:
        print(f"{len(errors)} problem(s) in networks.csv:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    return rows


def pass_networks(vars_, args):
    """Runbook 01: create/align the BCM networks.

    Data-driven: --networks networks.csv (copy scripts/networks-template.csv).
    Without --networks, falls back to the stock 4-network plan from
    the site variables file. Adding a fabric (GPU east-west, IB, IPMI) is a new
    CSV row — no code changes.
    """
    if args.networks:
        rows = _load_network_rows(args.networks)
        print(f"# networks pass: {len(rows)} networks from {args.networks}",
              file=sys.stderr)
        # L4: the four stock networks live in the site file too (NET_*,
        # GW_*). The CSV must agree with it, or the two sources drift and
        # the pass builds a plan the rest of the runbook does not use.
        stock = {r["name"]: r for r in _default_network_rows(vars_)}
        drift = []
        for r in rows:
            s = stock.get(r["name"])
            if not s:
                continue
            for key in ("cidr", "gateway"):
                if r[key] != s[key]:
                    drift.append(f"{r['name']} {key}: CSV {r[key] or '(none)'} vs "
                                 f"site file {s[key] or '(none)'}")
        if drift:
            print("REFUSED: networks.csv disagrees with the site variables file "
                  "on a stock network — fix one so both say the same:", file=sys.stderr)
            for d in drift:
                print(f"  - {d}", file=sys.stderr)
            sys.exit(1)
    else:
        rows = _default_network_rows(vars_)
        print("# networks pass: stock 4-network plan from the variables file "
              "(--networks networks.csv for extras)", file=sys.stderr)
    import ipaddress
    cmds = []
    for r in rows:
        name = r["name"]
        # A BCM network object has no "network" property: the address is
        # baseaddress + netmaskbits (BCM 11 Administrator Manual, 3.2.3.3:
        # "set baseaddress 192.168.1.0", "set netmaskbits 24") — verified
        # 2026-11-17, register item 7 (the pass used to `set network CIDR`).
        net = ipaddress.ip_network(r["cidr"], strict=False)
        addr = (f'set baseaddress {net.network_address} ; '
                f'set netmaskbits {net.prefixlen}')
        if r["action"] == "align":
            # created by the BCM installer; align it to the plan
            c = f'cmsh -c "network; use {name} ; {addr}'
            if r["gateway"]:
                c += f' ; set gateway {r["gateway"]}'
            cmds.append(c + ' ; commit"')
        else:
            # add-or-verify: create only if absent; the sets below converge it.
            # The manual: "a new network can be added from within network
            # mode using the add or clone commands" — add takes the name.
            # [unverified] the exact `add NAME` form, the `managementallowed`
            # property name, and the `network; list` first-column format the
            # existence check reads — first-contact items (verification
            # checklist, docs/verification-checklist.md).
            cmds.append(Step(f'cmsh -c "network; add {name} ; commit"',
                             exists=('cmsh -c "network; list"', name),
                             tolerate_exists=True, what=f"network {name}"))
            cmds.append(f'cmsh -c "network; use {name} ; {addr} ; commit"')
            if r["gateway"]:
                cmds.append(f'cmsh -c "network; use {name} ; set gateway {r["gateway"]} ; commit"')
            if r["mgmt_allowed"] == "yes":
                cmds.append(f'cmsh -c "network; use {name} ; set managementallowed yes ; commit"')
    cmds.append('cmsh -c "network; list"')
    run_commands(cmds, args.dry_run, f"create/align {len(rows)} BCM networks")


def pass_images(vars_, args):
    cp, wrk, gpu = vars_["CAT_CP"], vars_["CAT_WRK"], vars_["CAT_GPU"]
    icp, iwrk, igpu = vars_["IMG_CP"], vars_["IMG_WRK"], vars_["IMG_GPU"]
    # DGX node types clone the dgx base image; HGX/UCS/OEM clone default
    gpu_base = "dgx-image" if vars_.get("GPU_NODE_TYPE", "").startswith("dgx") else "default-image"
    cat_list, img_list = 'cmsh -c "category; list"', 'cmsh -c "softwareimage; list"'
    cmds = [
        Step(f'cmsh -c "category; add {c} ; commit"', exists=(cat_list, c),
             tolerate_exists=True, what=f"category {c}") for c in (cp, wrk, gpu)
    ] + [
        Step(f'cmsh -c "softwareimage; clone {base} {img} ; commit"',
             exists=(img_list, img), tolerate_exists=True, what=f"image {img}")
        for base, img in (("default-image", icp), ("default-image", iwrk),
                          (gpu_base, igpu))
    ] + [
        f'cmsh -c "category; use {cp} ; set softwareimage {icp} ; commit"',
        f'cmsh -c "category; use {wrk} ; set softwareimage {iwrk} ; commit"',
        f'cmsh -c "category; use {gpu} ; set softwareimage {igpu} ; commit"',
    ]
    print(f"# images pass: GPU base image = {gpu_base} (GPU_NODE_TYPE={vars_.get('GPU_NODE_TYPE')})",
          file=sys.stderr)
    run_commands(cmds, args.dry_run, "create categories and images")


def _storage_yaml_7a(server, share):
    return f"""apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-csi
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: nfs.csi.k8s.io
parameters:
  server: {server}
  share: {share}
reclaimPolicy: Retain
volumeBindingMode: Immediate
allowVolumeExpansion: true
mountOptions:
  - nfsvers=4.1
  - hard
"""


def _storage_yaml_7b(server, share):
    docs = []
    for name, sub, reclaim in (("nfs-data", "data", "Retain"),
                               ("nfs-scratch", "scratch", "Delete"),
                               ("nfs-models", "models", "Retain")):
        docs.append(f"""apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: {name}
provisioner: nfs.csi.k8s.io
parameters:
  server: {server}
  share: {share}/{sub}
reclaimPolicy: {reclaim}
volumeBindingMode: Immediate
allowVolumeExpansion: true
mountOptions:
  - nfsvers=4.1
  - hard
""")
    return "---\n".join(docs)


# 7A end state, both paths: exactly one default StorageClass (nfs-csi).
# local-path (if the cluster has it) is un-defaulted; any OTHER default
# makes the verification fail loudly — the operator decides, the pass
# does not guess.
UNDEFAULT_LOCAL_PATH = (
    "kubectl get storageclass local-path >/dev/null 2>&1 "
    "&& kubectl patch storageclass local-path -p "
    "'{\"metadata\":{\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"false\"}}}' "
    "|| echo 'NOTE: no local-path StorageClass — nothing to un-default'")
VERIFY_ONE_DEFAULT = (
    "test \"$(kubectl get storageclass --no-headers | grep -c '(default)')\" -eq 1 "
    "&& kubectl get storageclass --no-headers | grep '(default)' | grep -q '^nfs-csi ' "
    "&& echo 'OK: exactly one default StorageClass (nfs-csi)' "
    "|| { echo 'FAIL: default StorageClass is not exactly nfs-csi'; kubectl get storageclass; false; }")


def pass_storage(vars_, args):
    """Runbook 07: NFS CSI driver + StorageClasses.

    --plan 7a (default): one default StorageClass (nfs-csi); un-defaults
              local-path and verifies exactly one default remains.
    --plan 7b: data (Retain) / scratch (Delete) / models (Retain).
    Exports are derived from the variables: $NFS_SHARE (7a) and
    $NFS_SHARE/{data,scratch,models} (7b) on $NFS_SERVER — the canonical
    form; section 07's manual YAML uses the same variables. Re-runnable:
    `helm upgrade --install` converges an existing release and
    `kubectl apply` converges existing classes.
    """
    server, share = vars_["NFS_SERVER"], vars_["NFS_SHARE"]
    csi_ver = vars_["NFS_CSI_VERSION"]
    plan = args.plan
    yaml_text = _storage_yaml_7a(server, share) if plan == "7a" \
        else _storage_yaml_7b(server, share)
    fname = f"nfs-storage-{plan}.yaml"
    helm_cmds = [
        "helm repo add csi-driver-nfs "
        "https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts "
        "--force-update",
        f"helm upgrade --install csi-driver-nfs csi-driver-nfs/csi-driver-nfs "
        f"--namespace kube-system --version {csi_ver} "
        f"--set externalSnapshotter.enabled=true "
        f"--set controller.runOnControlPlane=true",
    ]
    post_cmds = [UNDEFAULT_LOCAL_PATH, VERIFY_ONE_DEFAULT] if plan == "7a" \
        else ["kubectl get storageclass"]
    if args.dry_run:
        print(f"# storage pass (plan {plan}): files + commands", file=sys.stderr)
        print(f"# --- file: {fname} ---")
        print(yaml_text, end="")
        for c in helm_cmds:
            print(c)
        print(f"kubectl apply -f {fname}")
        for c in post_cmds:
            print(c)
        print(f"# dry-run: review the YAML above (use --exec to run)",
              file=sys.stderr)
        return
    _require_tool([Step(c) for c in helm_cmds], "helm")
    confirm(3 + len(post_cmds), f"install the NFS CSI driver and apply plan {plan}")
    for c in helm_cmds:
        print(f"+ {c}", flush=True)
        r = subprocess.run(c, shell=True)
        if r.returncode != 0:
            print(f"FAILED (rc={r.returncode}): {c}", file=sys.stderr)
            sys.exit(1)
    with open(fname, "w") as f:
        f.write(yaml_text)
    print(f"wrote {fname}")
    r = subprocess.run(["kubectl", "apply", "-f", fname])
    if r.returncode != 0:
        sys.exit(r.returncode)
    for c in post_cmds:
        print(f"+ {c}", flush=True)
        r = subprocess.run(c, shell=True)
        if r.returncode != 0:
            print(f"FAILED (rc={r.returncode}): {c}", file=sys.stderr)
            sys.exit(1)
    print("pass complete.")


# Disk layouts (H1). By-path is the standard: slot-consistent, survives
# drive swaps. Kernel names (/dev/nvme0n1, /dev/sda) reorder across boots;
# by-id / by-uuid change when a drive is replaced, and BCM's by-id alias
# failed to translate at install time in the field. Shared by this pass
# (refuses to apply a bad XML) and check.sh (fails on any shipped one).
KERNEL_DEV_RE = re.compile(
    r"/dev/(nvme\d+n\d+(p\d+)?|sd[a-z]+\d*|vd[a-z]+\d*|hd[a-z]+\d*|xvd[a-z]+\d*"
    r"|cciss/\S+|mmcblk\d+\S*)$")
BY_PATH = "/dev/disk/by-path/"


def disksetup_violations(text, label):
    """Every reason `text` (a BCM disksetup XML) is not by-path-only."""
    fatal, non_by_path = disksetup_findings(text, label)
    return fatal + non_by_path


def disksetup_findings(text, label):
    """(fatal, non_by_path): the reasons `text` is not a by-path-only
    disksetup XML, split by whether the logged override (D3) may waive
    them. fatal — not XML, no <blockdev> at all — is never waivable;
    non_by_path — a device named other than by /dev/disk/by-path/ — is."""
    import xml.etree.ElementTree as ET
    fatal, errs = [], []
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError as e:
        return [f"{label}: not well-formed XML ({e})"], []
    blockdevs = [(b.text or "").strip() for b in root.iter("blockdev")]
    if not blockdevs:
        fatal.append(f"{label}: no <blockdev> elements — not a disksetup XML?")
    for b in blockdevs:
        if not b.startswith(BY_PATH):
            errs.append(f"{label}: <blockdev>{b}</blockdev> is not a {BY_PATH} value")
    for ln, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r"/dev/[^\s<>\"']+", line):
            path = m.group(0)
            if path.startswith(BY_PATH):
                continue
            if KERNEL_DEV_RE.match(path):
                kind = "kernel device name (reorders across boots)"
            elif path.startswith("/dev/disk/by-"):
                kind = "by-id/uuid/label alias (changes on drive replacement)"
            else:
                kind = "unrecognized device path"
            errs.append(f"{label}:{ln}: {path} — {kind}; use {BY_PATH} values only")
    return fatal, errs


# The logged, named override (D3). For a host type where by-path
# genuinely fails in the node-installer, the operator may apply a
# non-by-path layout THROUGH the tooling (instead of raw cmsh, outside
# every guard) — only with --allow-non-by-path AND a non-empty --reason,
# and with a loud warning naming the reason and every non-by-path value,
# every time. It never waives a malformed XML, an XML with no
# <blockdev>, or a category with no layout. check.sh has no override:
# the shipped tree stays strict.
def override_requested(args):
    """The override reason if --allow-non-by-path was given with a real
    --reason; exits 1 on a half-given override."""
    allow = getattr(args, "allow_non_by_path", False)
    reason = (getattr(args, "reason", None) or "").strip()
    if allow and not reason:
        print("REFUSED: --allow-non-by-path needs --reason \"<why by-path fails on "
              "this host type>\" — the override is logged and named, never silent",
              file=sys.stderr)
        sys.exit(1)
    if reason and not allow:
        print("REFUSED: --reason is only meaningful with --allow-non-by-path",
              file=sys.stderr)
        sys.exit(1)
    return reason if allow else None


def override_warning(reason, where, values):
    bar = "!" * 72
    print(bar, file=sys.stderr)
    print(f"!! BY-PATH OVERRIDE IN USE (--allow-non-by-path) — {where}", file=sys.stderr)
    print(f"!! reason given: {reason}", file=sys.stderr)
    print("!! non-by-path values accepted:", file=sys.stderr)
    for v in values:
        print(f"!!   - {v}", file=sys.stderr)
    print("!! These names can move across boots (kernel) or on a drive swap",
          file=sys.stderr)
    print("!! (by-id/uuid). Verify per 03.4 step 6, and log the deviation (10).",
          file=sys.stderr)
    print(bar, file=sys.stderr)


def non_by_path_values(text):
    """The distinct device names in a disksetup XML that are not
    /dev/disk/by-path/ values (what an override actually waives)."""
    import xml.etree.ElementTree as ET
    vals = []
    try:
        vals += [(b.text or "").strip() for b in ET.fromstring(text.strip()).iter("blockdev")]
    except ET.ParseError:
        pass
    vals += re.findall(r"/dev/[^\s<>\"']+", text)
    out = []
    for v in vals:
        if v and not v.startswith(BY_PATH) and v not in out:
            out.append(v)
    return out


def override_log_path(vars_path):
    """disk-layouts/OVERRIDES.log beside the variables file's layouts."""
    return os.path.join(os.path.dirname(os.path.abspath(vars_path)),
                        "disk-layouts", "OVERRIDES.log")


def record_override(vars_path, pass_name, dry_run, scope, reason, values):
    """Durable record of one override use (Q6): append one line to
    disk-layouts/OVERRIDES.log — date, pass + mode, scope, reason, every
    waived value. Fails closed: an override that can't be recorded is
    refused."""
    import datetime
    path = override_log_path(vars_path)
    clean = " ".join(reason.split())
    line = (f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')}"
            f" | pass={pass_name} mode={'dry-run' if dry_run else 'exec'}"
            f" | scope={scope} | reason={clean} | waived: {', '.join(values)}\n")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(line)
    except OSError as e:
        print(f"REFUSED: the override could not be recorded in {path} ({e}) — "
              f"an unrecorded override is not allowed", file=sys.stderr)
        sys.exit(1)
    print(f"# override recorded: {path}", file=sys.stderr)


def check_layout(text, label, reason, record=None):
    """Validate one layout; returns (ok, problems). With an override
    reason, non-by-path findings are waived with the loud warning, and
    record(values) (if given) writes the durable OVERRIDES.log line."""
    fatal, non_by_path = disksetup_findings(text, label)
    if fatal:
        return False, fatal + non_by_path
    if non_by_path and reason:
        override_warning(reason, label, non_by_path)
        if record:
            record(non_by_path_values(text))
        return True, []
    return not non_by_path, non_by_path


def pass_disklayouts(vars_, args):
    """Runbook 03.4 (reference: 12): apply a verified by-path disk layout.

    Looks for <vars dir>/disk-layouts/<type>-by-path.xml (or --layout).
    If it is not there, the pass stops and prints the collection
    procedure — the XML can only be built from live hardware of that type.
    The XML is validated first: it must be well-formed and every device
    must be a /dev/disk/by-path/ value — kernel names and by-id/uuid are
    refused (fail closed).

    --node N   apply to ONE device (the collection node) — the 03.4 test
    (default) apply at category level — only after the collection node
               has been reprovisioned on the XML and verified (03.4 gate).
    --allow-non-by-path --reason TEXT   the logged override (D3): accept
               non-by-path device names for a host type where by-path
               fails in the installer; warns loudly, every time, and
               appends a line to disk-layouts/OVERRIDES.log.
    """
    reason = override_requested(args)
    ntype = args.type or vars_.get("GPU_NODE_TYPE", "")
    cat = args.category or vars_["CAT_GPU"]
    docs_dir = os.path.dirname(os.path.abspath(args.vars))
    layout = args.layout or os.path.join(docs_dir, "disk-layouts",
                                         f"{ntype}-by-path.xml")
    if not os.path.exists(layout):
        print(f"# disklayouts pass: no layout for node type '{ntype}'",
              file=sys.stderr)
        print(f"# expected: {layout}", file=sys.stderr)
        for line in (
                "#",
                "# The XML can only be built from live hardware. On ONE node of",
                "# this host type (the collection node, section 03.4):",
                "#   ls -l /dev/disk/by-path",
                "# build the by-path map, copy BCM's default XML for the model",
                "# from /cm/local/apps/cmd/etc/htdocs/disk-setup/, and replace",
                "# every kernel device path with its by-path value (never",
                "# /dev/nvmeXnY, never by-id/uuid). Procedure: 03.4; reference: 12."):
            print(line, file=sys.stderr)
        sys.exit(1)
    scope = (f"device {args.node}" if args.node else f"category {cat}") + f" ({os.path.abspath(layout)})"
    with open(layout) as f:
        ok, errs = check_layout(
            f.read(), layout, reason,
            record=lambda vals: record_override(args.vars, "disklayouts", args.dry_run,
                                                scope, reason, vals))
    if not ok:
        print(f"REFUSED: {layout} is not a by-path-only disksetup XML:",
              file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    abs_layout = os.path.abspath(layout)
    if args.node:
        print(f"# disklayouts pass: {ntype} layout -> ONE node ({args.node}); "
              f"reprovision it and verify (03.4) before the category step",
              file=sys.stderr)
        cmds = [f'cmsh -c "device ; use {args.node} ; set disksetup {abs_layout} ; commit"']
        what = f"apply the {ntype} disk layout to {args.node}"
    else:
        print(f"# disklayouts pass: {ntype} layout -> category {cat} "
              f"(only after the 03.4 gate passed on a collection node)",
              file=sys.stderr)
        cmds = [
            f'cmsh -c "category ; use {cat} ; set disksetup {abs_layout} ; commit"',
            'cmsh -c "disksetup list"',
        ]
        what = f"apply the {ntype} disk layout to category {cat}"
    run_commands(cmds, args.dry_run, what)


def _keep_or_sub(vals):
    """Regex callback: substitute a known variable, but never a
    credential-class one (H6) — those keep their $NAME reference."""
    def cb(m):
        name = m.group(1)
        if is_credential_class(name):
            return m.group(0)
        return vals.get(name, m.group(0))
    return cb


def _expand_vars(vars_):
    """Expand nested $REFs inside variable values (e.g. DNS_IP="$NET_DNS1").
    A reference to a credential-class name is never expanded, so a
    non-credential variable can't smuggle a secret into the render."""
    vals = dict(vars_)
    for _ in range(5):
        changed = False
        cb = _keep_or_sub(vals)
        for k, v in vals.items():
            nv = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', cb, v)
            nv = re.sub(r'\$([A-Za-z_][A-Za-z0-9_]*)', cb, nv)
            if nv != v:
                vals[k] = nv
                changed = True
        if not changed:
            break
    return vals


def _sub_vars(text, vals):
    cb = _keep_or_sub(vals)
    text = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', cb, text)
    text = re.sub(r'\$([A-Za-z_][A-Za-z0-9_]*)', cb, text)
    return text


def credential_tripwire(vars_, environ):
    """Names (never values) of credential-class variables that hold a
    live value — in the variables file, or exported in the environment
    the render runs in. Only names defined in the variables file are
    considered, so unrelated shell secrets don't raise noise."""
    live = []
    for name in sorted(vars_):
        if not is_credential_class(name):
            continue
        where = []
        if not is_placeholder_value(vars_[name]):
            where.append("variables file")
        if name in environ and not is_placeholder_value(environ[name]):
            where.append("environment")
        if where:
            live.append((name, where))
    return live


RENDER_MARKER = ".bcm-render"
_IMAGE_EXT = (".svg", ".png", ".jpg", ".jpeg", ".gif")
_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")


def _rewrite_links(text, src_path, dst_path, out_root, rendered, copies):
    """L2: make a rendered doc's relative links work inside the deliverable
    (same rule as Cerberus render.py): a link to another rendered doc is
    kept; a linked diagram is copied into <out>/diagrams/; a link to a
    library file that is not part of the deliverable (the variables
    template, 99-role-mapping.md) becomes plain text naming the library
    path. URLs, anchors and absolute paths are untouched."""
    repo = os.path.dirname(TRACK_DIR)
    src_dir, dst_dir = os.path.dirname(src_path), os.path.dirname(dst_path)

    def repl(m):
        bang, label, target = m.groups()
        if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^/", target):
            return m.group(0)
        path, _, frag = target.partition("#")
        src = os.path.normpath(os.path.join(src_dir, path))
        if src in rendered:
            new = os.path.relpath(rendered[src], dst_dir)
            return f"{bang}[{label}]({new}{'#' + frag if frag else ''})"
        if os.path.isfile(src) and src.lower().endswith(_IMAGE_EXT):
            dst = os.path.join(out_root, "diagrams", os.path.basename(src))
            copies[src] = dst
            return f"{bang}[{label}]({os.path.relpath(dst, dst_dir)})"
        if os.path.exists(src):
            lib = os.path.relpath(src, repo)
            return f"{label} (library file `{lib}`)" if label else f"library file `{lib}`"
        return m.group(0)
    return _LINK_RE.sub(repl, text)


def pass_render(vars_, args):
    """Build the site-specific MD guide: copy every doc, substitute the
    $VARS from the variables file — except credentials.

    CREDENTIAL POLICY (H6): any variable whose name matches
    *PASSWORD* *API_KEY* *TOKEN* *SECRET* *PRODUCT_KEY* *LICENSE_KEY*
    (case-insensitive) is never substituted. The rendered guide keeps the
    $NAME reference; the operator's shell supplies the value at run time.
    Credentials never appear in a deliverable, placeholder or real.
    TRIPWIRE (M4 slate #4, parity with Cerberus render.py since M5):
      - a credential-class name holding a LIVE value in the variables
        FILE (including a live default inside ${NAME:-...}) -> render
        REFUSES, exit 3, before anything is written: a deliverable is
        being built from a file that holds a secret;
      - a live value held only in the ENVIRONMENT (the sanctioned
        `read -rs NAME; export NAME`) -> loud warning, render continues.
    Names only, never values, in every message.

    Three workflows, one guarantee — the rendered guide always exists:
      client walkthrough:  render -> hand the MDs over, copy/paste together
      team review:         render -> review -> run the passes with --exec
      run then document:   run the passes with --exec -> render as the deliverable

    Never modifies the template docs; writes into --out.
    99-role-mapping.md is merge documentation, not deployment docs — skipped.
    """
    # The docs are the track's own (bcm/), wherever the site file lives.
    docs_dir = TRACK_DIR
    out = args.out
    live = credential_tripwire(vars_, os.environ)
    file_live = [n for n, where in live if "variables file" in where]
    env_live = [n for n, where in live if "variables file" not in where]
    bar = "!" * 72
    if file_live:
        print(bar, file=sys.stderr)
        print("!! REFUSED: LIVE SECRETS IN THE VARIABLES FILE", file=sys.stderr)
        for name in file_live:
            print(f"!!   {name}: holds a real value in {args.vars}", file=sys.stderr)
        print("!! A deliverable is built from this file. Put the placeholder back", file=sys.stderr)
        print("!! and supply the value at run time only (read -rs NAME; export", file=sys.stderr)
        print("!! NAME). Nothing was written.", file=sys.stderr)
        print(bar, file=sys.stderr)
        sys.exit(3)
    # L2: a fresh directory every time. An existing --out is refused;
    # --force replaces it only when a previous render wrote it (marker),
    # never an arbitrary directory.
    if os.path.exists(out):
        if not args.force:
            print(f"REFUSED: {out} exists — render writes a fresh directory "
                  f"(--force replaces a previous render's output)", file=sys.stderr)
            sys.exit(2)
        if not os.path.isfile(os.path.join(out, RENDER_MARKER)):
            print(f"REFUSED: {out} was not written by runbook.py render (no "
                  f"{RENDER_MARKER} marker) — choose a fresh --out", file=sys.stderr)
            sys.exit(2)
        shutil.rmtree(out)
    os.makedirs(out)
    with open(os.path.join(out, RENDER_MARKER), "w") as f:
        f.write("written by bcm/scripts/runbook.py render; --force may replace this directory\n")
    vals = _expand_vars(vars_)
    skip = {"99-role-mapping.md"}
    docs = [fn for fn in sorted(os.listdir(docs_dir))
            if fn.endswith(".md") and fn not in skip]
    targets = {os.path.join(docs_dir, fn): os.path.join(os.path.abspath(out), fn)
               for fn in docs}
    copies = {}
    n = 0
    for fn in docs:
        src = os.path.join(docs_dir, fn)
        with open(src) as f:
            text = f.read()
        text = _rewrite_links(_sub_vars(text, vals), src, targets[src],
                              os.path.abspath(out), targets, copies)
        with open(targets[src], "w") as f:
            f.write(text)
        n += 1
    for src, dst in sorted(copies.items()):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    creds = sorted(k for k in vars_ if is_credential_class(k))
    unfilled = sorted(k for k, v in vals.items()
                      if "<" in v and ">" in v and k not in creds)
    print(f"# render: {n} docs -> {out}/ (skipped: {', '.join(sorted(skip))})"
          + (f"; {len(copies)} diagram(s) copied into diagrams/" if copies else ""),
          file=sys.stderr)
    print(f"# render: credential-class names kept as $NAME, never substituted "
          f"({CREDENTIAL_CLASS_TEXT}): {', '.join(creds) or 'none'}",
          file=sys.stderr)
    if unfilled:
        print(f"# warning: {len(unfilled)} placeholders unfilled: "
              f"{', '.join(unfilled)}", file=sys.stderr)
        print("# run `runbook.py vars` for the full list", file=sys.stderr)
    else:
        print("# render: all non-credential variables filled", file=sys.stderr)
    if env_live:
        print(bar, file=sys.stderr)
        print("!! WARNING: LIVE SECRETS LOADED NEAR A DELIVERABLE (environment)",
              file=sys.stderr)
        for name in env_live:
            print(f"!!   {name}: holds a real value (environment)", file=sys.stderr)
        print("!! The rendered guide was NOT given these values — it keeps the",
              file=sys.stderr)
        print("!! $NAME references. Unset them in this shell when you are done.",
              file=sys.stderr)
        print(bar, file=sys.stderr)


def layout_gate(categories, reason=None, devices=(), vars_path=None,
                pass_name="layoutgate", dry_run=False):
    """The 03.4 fleet gate, mechanical (H1 / I2 / Q7). Reads from BCM
    each category's disksetup, and the device-level disksetup of every
    named device that already exists (a device-level layout overrides
    its category, so provisioning would honor it). Refuses (exit 1,
    naming the category or device and the offending values) unless
    every layout is by-path only. A category with no layout set is a
    refusal: nothing verified = not gated. A device with no device-level
    layout, or not yet defined in BCM, inherits its category's (checked
    above). Read-only; needs cmsh (the head node). The D3 override waives
    non-by-path values only, loudly, and records each use."""
    if not shutil.which("cmsh"):
        print("REFUSED: `cmsh` is not on PATH — the fleet layout gate reads each "
              "category's disksetup from BCM, so it runs on the head node only.",
              file=sys.stderr)
        sys.exit(2)
    problems = []

    def verdict(label, text):
        _, non_by_path = disksetup_findings(text, label)
        ok, errs = check_layout(
            text, label, reason,
            record=(lambda vals: record_override(vars_path, pass_name, dry_run,
                                                 label, reason, vals))
            if vars_path else None)
        if ok:
            print(f"# layout gate: {label}: "
                  + ("ACCEPTED UNDER OVERRIDE (non-by-path values, see warning)"
                     if non_by_path else "by-path layout verified"),
                  file=sys.stderr)
        else:
            problems.extend(errs)

    for cat in sorted(set(categories)):
        cmd = f'cmsh -c "category ; use {cat} ; get disksetup"'
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        out = r.stdout or ""
        if r.returncode != 0:
            problems.append(f"category {cat}: could not read its disksetup "
                            f"(rc={r.returncode}): {(r.stderr or out).strip()[:160]}")
            continue
        if not out.strip():
            problems.append(f"category {cat}: no disk layout set — nothing verified "
                            f"= not gated (build and verify it in 03.4)")
            continue
        verdict(f"category {cat}", out)

    devices = sorted(set(devices))
    if devices:
        r = subprocess.run('cmsh -c "device ; list"', shell=True,
                           capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f"could not list devices to check device-level layouts "
                            f"(rc={r.returncode}): {(r.stderr or r.stdout).strip()[:160]}")
            devices = []
        existing = {tok for line in (r.stdout or "").splitlines() for tok in line.split()}
        inherit = 0
        for d in devices:
            if d not in existing:
                inherit += 1          # not defined yet: it will get its category's
                continue
            cmd = f'cmsh -c "device ; use {d} ; get disksetup"'
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            out = r.stdout or ""
            if r.returncode != 0:
                problems.append(f"device {d}: could not read its disksetup "
                                f"(rc={r.returncode}): {(r.stderr or out).strip()[:160]}")
                continue
            if not out.strip():
                inherit += 1          # no device-level layout: inherits its category's
                continue
            verdict(f"device {d}", out)
        if devices:
            print(f"# layout gate: {len(devices) - inherit} device-level layout(s) "
                  f"checked; {inherit} device(s) inherit their category's",
                  file=sys.stderr)

    if problems:
        print("REFUSED: fleet provisioning is gated on a verified by-path disk "
              "layout per category and per device-level override (03.4). "
              "Not provisioning:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)


def pass_layoutgate(vars_, args):
    """The fleet gate on its own (the generated provision-nodes.sh runs
    this before its first add)."""
    reason = override_requested(args)
    layout_gate(args.category, reason, devices=args.device or (),
                vars_path=args.vars, pass_name="layoutgate", dry_run=False)
    n, m = len(set(args.category)), len(set(args.device or ()))
    print(f"layout gate: {n} {'category' if n == 1 else 'categories'}"
          + (f" and {m} device(s)" if m else "") + " passed.")


def pass_nodes(vars_, args):
    reason = override_requested(args)
    gn = load_script_module("generate-nodes.py")
    rows = gn.load(args.inventory)
    errors = gn.check(rows)
    if errors:
        print(f"{len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"# nodes pass: {len(rows)} nodes validated", file=sys.stderr)
    categories = sorted({r["category"].strip() for r in rows})
    hosts = sorted({r["hostname"].strip() for r in rows})
    script = gn.generate(rows, gate_reason=reason, vars_path=os.path.abspath(args.vars))
    if args.dry_run:
        print(script)
        print(f"# nodes pass: under --exec the 03.4 layout gate runs first and "
              f"refuses unless every category ({', '.join(categories)}) and every "
              f"device-level layout carries a by-path layout; the script above "
              f"repeats it before its first add",
              file=sys.stderr)
        return
    _require_tool([Step("cmsh")])
    layout_gate(categories, reason, devices=hosts, vars_path=args.vars,
                pass_name="nodes", dry_run=False)   # refuses before anything is asked or run
    confirm(len(rows), f"provision {len(rows)} nodes")
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as t:
        t.write(script)
        tname = t.name
    r = subprocess.run(["bash", tname])
    os.unlink(tname)
    sys.exit(r.returncode)


def pass_users(vars_, args):
    """Runbook 15: BCM users. Fails closed while any temporary password
    is missing or still a placeholder (generate-users.py --passwords);
    passwords reach cmsh on stdin at run time, never via argv, and the
    generated script never contains them."""
    gu = load_script_module("generate-users.py")
    rows = gu.load(args.accounts)
    errors = gu.check(rows)
    if not errors:
        errors = gu.check_passwords(rows, args.passwords)
    if errors:
        print(f"{len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    gen = [sys.executable, os.path.join(HERE, "generate-users.py"),
           args.accounts, "--passwords", args.passwords]
    if args.dry_run:
        r = subprocess.run(gen)
        sys.exit(r.returncode)
    _require_tool([Step("cmsh")])
    confirm(len(rows), f"create {len(rows)} BCM users (add-or-verify)")
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as t:
        tname = t.name
    with open(tname, "w") as out:
        subprocess.run(gen, stdout=out, check=True)
    r = subprocess.run(["bash", tname, args.passwords])
    os.unlink(tname)
    sys.exit(r.returncode)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="B300 runbook subroutine passes.")
    vars_help = ("site variables file (default: bcm/site-vars.sh, a copy of the "
                 "00-variables.sh template; the template itself is refused)")
    ap.add_argument("--vars", default=None, help=vars_help)
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                    help="print what would run (default)")
    ap.add_argument("--exec", dest="dry_run", action="store_false",
                    help="actually execute (asks for confirmation)")
    # also accept --vars/--dry-run/--exec after the subcommand name.
    # All three use SUPPRESS in the shared parent so the subparser never
    # overwrites a flag given BEFORE the pass name with its own default
    # (L1: `runbook.py --exec networks` used to come back dry_run=True).
    # Given on both sides, the one after the pass name wins.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vars", default=argparse.SUPPRESS, help=vars_help)
    common.add_argument("--dry-run", dest="dry_run", action="store_true",
                        default=argparse.SUPPRESS)
    common.add_argument("--exec", dest="dry_run", action="store_false",
                        default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="pass_", required=True)

    # D3: the logged, named by-path override (disklayouts, nodes, layoutgate)
    override = argparse.ArgumentParser(add_help=False)
    override.add_argument("--allow-non-by-path", dest="allow_non_by_path",
                          action="store_true",
                          help="accept non-by-path device names (needs --reason; "
                               "warns loudly every time)")
    override.add_argument("--reason", default=None,
                          help="why by-path fails in the installer on this host type")

    p = sub.add_parser("vars", parents=[common],
                       help="validate the variables file (placeholders, CIDR guard, "
                            "template-default audit)")
    p.add_argument("--networks", default=None,
                   help="networks CSV for the CIDR guard (default: networks.csv "
                        "beside the variables file, if present)")
    p = sub.add_parser("networks", parents=[common],
                       help="runbook 01: create/align the BCM networks")
    p.add_argument("--networks", default=None,
                   help="networks CSV (default: stock plan from the variables file)")
    sub.add_parser("images", parents=[common],
                   help="runbook 03: categories + software images")
    p = sub.add_parser("nodes", parents=[common, override],
                       help="runbook 03.5: provision nodes from CSV (gated on 03.4)")
    p.add_argument("--inventory", required=True)
    p = sub.add_parser("layoutgate", parents=[common, override],
                       help="runbook 03.4: refuse unless every named category "
                            "carries a by-path disk layout (read-only)")
    p.add_argument("--category", action="append", required=True,
                   help="BCM category to check (repeatable)")
    p.add_argument("--device", action="append", default=[],
                   help="device whose device-level disksetup, if any, is checked too "
                        "(repeatable)")
    p = sub.add_parser("disklayouts", parents=[common, override],
                       help="runbook 03.4/12: apply a verified by-path disk layout")
    p.add_argument("--type", default=None,
                   help="host type (default: $GPU_NODE_TYPE)")
    p.add_argument("--category", default=None,
                   help="BCM category (default: $CAT_GPU)")
    p.add_argument("--layout", default=None,
                   help="XML path (default: <vars dir>/disk-layouts/<type>-by-path.xml)")
    p.add_argument("--node", default=None,
                   help="apply to this one device (the 03.4 collection node) instead of the category")
    p = sub.add_parser("storage", parents=[common],
                       help="runbook 07: NFS CSI driver + StorageClasses")
    p.add_argument("--plan", choices=["7a", "7b"], default="7a",
                   help="7a: one default class; 7b: data/scratch/models")
    p = sub.add_parser("users", parents=[common],
                       help="runbook 15: create BCM users from CSV")
    p.add_argument("--accounts", required=True)
    p.add_argument("--passwords", required=True,
                   help="username,password file, mode 0600 (never commit it)")
    p = sub.add_parser("render", parents=[common],
                       help="build the site-specific MD guide (variable substitution; "
                            "credentials are never substituted)")
    p.add_argument("--out", default="site-docs/bcm",
                   help="output directory (default: site-docs/bcm — one directory "
                        "per track, so the Cerberus render never collides with it)")
    p.add_argument("--force", action="store_true",
                   help="replace an existing --out that a previous render wrote "
                        "(never an arbitrary directory)")

    args = ap.parse_args()
    args.vars = resolve_vars(args.vars, args.pass_)
    vars_ = load_vars(args.vars)
    {"vars": pass_vars, "networks": pass_networks, "images": pass_images,
     "nodes": pass_nodes, "layoutgate": pass_layoutgate,
     "disklayouts": pass_disklayouts,
     "storage": pass_storage, "users": pass_users,
     "render": pass_render}[args.pass_](vars_, args)


if __name__ == "__main__":
    main()
