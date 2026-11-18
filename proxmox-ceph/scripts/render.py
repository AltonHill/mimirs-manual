#!/usr/bin/env python3
"""Cerberus — scripts/render.py.

Render the site-specific Markdown guide: copy every runbook/NN-name/README.md
into a fresh output directory with variables.sh values substituted.

Only variables DEFINED in runbook/00-overview/variables.sh are substituted
(${VAR} and $VAR forms). Shell loop/local variables used inside the docs
(e.g. ${NODE_NUM}, ${MGMT_IP} derived via eval) are left untouched — they are
resolved at deploy time on the node, not at render time. So are the
host-resolved names (BF3_P1, BF3_P2, MGMT_IF), which variables.sh leaves
undefined on purpose.

An empty string is NEVER substituted. A name that is defined but empty
passes through untouched (the rendered guide keeps the ${VAR} reference for
the node's shell) and is reported in the render output with its occurrence
count — a blank value in a deliverable is how a rendered config loses its
interface names.

Credentials are NEVER substituted (H6 doctrine, ported from the BCM
track's runbook.py — "credentials never appear in a deliverable,
placeholder or real"). Any name matching the credential class
(*PASSWORD*, *API_KEY*, *TOKEN*, *SECRET*, *PRODUCT_KEY*, *LICENSE_KEY*;
case-insensitive) keeps its $NAME / ${NAME} reference in the rendered
guide, for the operator's shell to expand at run time, and nested
expansion never resolves one either. Every render prints the kept names.
Tripwire (ruled 2026-11-16, M4 slate #4):
  - a credential-class name that holds a LIVE value (not empty, not
    PLACEHOLDER, no <...> token, not pure $-indirection) in the variables
    FILE -> render REFUSES (exit 3) before writing anything: a live secret
    sits in the file the deliverable is built from;
  - a live value held only in the ENVIRONMENT (the sanctioned pattern —
    `read -rs PBS_PASSWORD; export PBS_PASSWORD`) -> loud warning, names
    only, render continues (the guide never sees the value either way).
The live case today is the PBS token secret (PBS_PASSWORD, 06.5): it
belongs in the site's secrets vault, never in site-vars.sh.

Usage:
    scripts/render.py --out site-docs/proxmox-ceph/          # reads <track>/site-vars.sh
    scripts/render.py --vars /path/to/site-vars.sh --out site-docs/proxmox-ceph/ [--force]
    scripts/render.py --vars runbook/00-overview/variables.sh --out DIR   # publisher's check

Site file (ruled 2026-11-17, M7 #7 — aligned with BCM's runbook.py): with
no --vars, render reads <track>/site-vars.sh and REFUSES (exit 2, with the
cp line to run) when it does not exist. It never falls back to the
template. The pristine template is accepted only when named explicitly
with --vars, and then prints a NOTE: that is the publisher's check, not a
site render.

The template docs are never modified; render always writes a fresh
directory. An existing --out is refused; --force replaces it only if a
previous render wrote it (the .cerberus-render marker) — never an
arbitrary directory. Relative links are rewritten so the deliverable
stands alone (L2): a linked diagram is copied into <out>/diagrams/; a link
to a rendered section keeps working; a link to a library file that is not
part of the deliverable (docs/references.md, a script, the variables
template) becomes plain text naming the library path.
The render report lists: values still literally PLACEHOLDER; defined-but-
empty names (passed through, never blanked); the credential-class names kept
as references; and every audited value (networks, gateways, IPs, PBS host,
DNS, NTP, the mail target, the pool sizing, the XCC login name, the PBS
datastore path) still identical to the shipped template default in
runbook/00-overview/variables.sh.

Python 3.8+, stdlib only (scripts/check.sh proves the grammar floor).
"""

import argparse
import os
import re
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRACK_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_VARS = os.path.join(TRACK_DIR, "runbook", "00-overview", "variables.sh")  # the TEMPLATE
SITE_VARS = os.path.join(TRACK_DIR, "site-vars.sh")
RUNBOOK_DIR = os.path.join(TRACK_DIR, "runbook")
REPO_DIR = os.path.dirname(TRACK_DIR)
MARKER = ".cerberus-render"
IMAGE_EXT = (".svg", ".png", ".jpg", ".jpeg", ".gif")
LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")


def resolve_vars(given):
    """The site variables file, or exit 2 (fail closed). Mirrors BCM's
    runbook.py resolve_vars: --vars FILE (cwd- or track-relative) wins;
    with no --vars, <track>/site-vars.sh; never the template by default."""
    hint = (f"cp {os.path.relpath(DEFAULT_VARS)} {os.path.relpath(SITE_VARS)}"
            "   # then fill in the site's values")
    if given is None:
        if not os.path.isfile(SITE_VARS):
            print("REFUSED: no site variables file. Create the default, or pass --vars FILE:",
                  file=sys.stderr)
            print(f"         {hint}", file=sys.stderr)
            print("         Refusing to render the template defaults unknowingly "
                  "(runbook/00-overview/variables.sh is the pristine template).",
                  file=sys.stderr)
            sys.exit(2)
        return SITE_VARS
    path = given
    if not os.path.exists(path) and os.path.exists(os.path.join(TRACK_DIR, given)):
        path = os.path.join(TRACK_DIR, given)
    if not os.path.isfile(path):
        print(f"REFUSED: --vars file not found: {given}", file=sys.stderr)
        sys.exit(2)
    return path


def rewrite_links(text, src_path, dst_path, out_root, rendered, copies):
    """L2: make a rendered doc's relative links work inside the deliverable.

    src_path / dst_path: the doc's source and output paths. rendered: the
    set of source paths the render writes (abs) -> output path. copies:
    dict filled with source image -> output path to copy afterwards."""
    src_dir, dst_dir = os.path.dirname(src_path), os.path.dirname(dst_path)

    def repl(m):
        bang, label, target = m.groups()
        if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^/", target):
            return m.group(0)                      # URL, anchor, absolute: untouched
        path, _, frag = target.partition("#")
        src = os.path.normpath(os.path.join(src_dir, path))
        if src in rendered:
            new = os.path.relpath(rendered[src], dst_dir)
            return f"{bang}[{label}]({new}{'#' + frag if frag else ''})"
        if os.path.isfile(src) and src.lower().endswith(IMAGE_EXT):
            out = os.path.join(out_root, "diagrams", os.path.basename(src))
            copies[src] = out
            return f"{bang}[{label}]({os.path.relpath(out, dst_dir)})"
        if os.path.exists(src):
            lib = os.path.relpath(src, REPO_DIR)
            return f"{label} (library file `{lib}`)" if label else f"library file `{lib}`"
        return m.group(0)                          # unresolvable: reflint's job, not ours
    return LINK_RE.sub(repl, text)


# H6 / I5: the credential class, shared with the BCM track's runbook.py
# (same patterns, same case-insensitive match — a match only ever keeps a
# $NAME reference, so over-matching fails safe).
CREDENTIAL_PATTERNS = ("PASSWORD", "API_KEY", "TOKEN", "SECRET",
                       "PRODUCT_KEY", "LICENSE_KEY")
CREDENTIAL_CLASS_RE = re.compile("|".join(CREDENTIAL_PATTERNS), re.I)
CREDENTIAL_CLASS_TEXT = "/".join(f"*{p}*" for p in CREDENTIAL_PATTERNS)
REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def is_credential_class(name):
    return bool(CREDENTIAL_CLASS_RE.search(name))


def is_placeholder_value(value):
    """True if a value is NOT a live secret: empty, the literal PLACEHOLDER,
    a <PLACEHOLDER> token anywhere in it, or pure shell indirection ($VAR,
    ${VAR...}, $(cmd)) resolved by the operator's shell at run time."""
    v = (value or "").strip()
    return (not v) or v == "PLACEHOLDER" or bool(re.search(r"<[^<>]+>", v)) \
        or v.startswith("$")


def parse_variables(path):
    """Parse NAME="${NAME:-default}" and NAME="value" assignments.

    Returns (values dict, placeholder_names list).
    Later assignments win. Environment overrides are NOT applied —
    the file is the single source of truth (matches the BCM track).
    """
    values = {}
    # ${NAME:-default} form (Cerberus convention)
    pat_default = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"\$\{\1:-(.*)\}"\s*(?:#.*)?$')
    # plain "value" / 'value' / bare forms
    pat_plain = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|(\S+))\s*(?:#.*)?$")
    # L16: only top-level assignments are site variables. Lines inside a
    # function body (the cerberus_* helpers' `val=...`, `missing=1`) and
    # indented lines are skipped.
    func_start = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{\s*$")
    in_func = False
    with open(path) as f:
        for raw in f:
            if in_func:
                if raw.rstrip() == "}":
                    in_func = False
                continue
            if func_start.match(raw.rstrip()):
                in_func = True
                continue
            if raw[:1].isspace():
                continue
            line = raw.strip()
            m = pat_default.match(line)
            if m:
                values[m.group(1)] = m.group(2)
                continue
            m = pat_plain.match(line)
            if m:
                values[m.group(1)] = m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))
    # Expand nested references (e.g. FOO="$BAR/baz"), stable sort, max 10 rounds
    for _ in range(10):
        changed = False
        for k, v in list(values.items()):
            def repl(m, _v=values):
                name = m.group(1) or m.group(2)
                # never resolve a credential reference (FOO="$PBS_PASSWORD"
                # must not smuggle a secret into the render), and never
                # expand to an empty string (same rules as substitute)
                if is_credential_class(name):
                    return m.group(0)
                return _v.get(name) or m.group(0)
            new = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", repl, v)
            if new != v:
                values[k] = new
                changed = True
        if not changed:
            break
    return values


def substitute(text, values, passed=None):
    """Substitute ${VAR} / $VAR for known, NON-EMPTY vars only.

    ${VAR:-...} / ${VAR:=...} shell-default forms are left alone — they are
    runtime shell, resolved when the doc's commands run on the node.

    A defined name whose value is empty is never substituted: the reference
    passes through untouched, and each occurrence is counted into `passed`
    (a dict name -> count) so the caller can report it.
    """
    if passed is None:
        passed = {}
    subst = {k: v for k, v in values.items()
             if v != "" and not is_credential_class(k)}
    empty = {k for k, v in values.items()
             if v == "" and not is_credential_class(k)}

    def keep(name, original):
        passed[name] = passed.get(name, 0) + 1
        return original

    # ${VAR} not followed by :- or :=
    def braced(m):
        name = m.group(1)
        if is_credential_class(name):
            return m.group(0)          # credentials: never substituted
        if name in empty:
            return keep(name, m.group(0))
        return subst.get(name, m.group(0))
    text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}(?![:-])", braced, text)

    # $VAR (bare) — word boundary, not followed by identifier chars
    names = sorted((n for n in values if not is_credential_class(n)),
                   key=len, reverse=True)
    if names:
        pat = re.compile(r"\$(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9_])")

        def bare(m):
            name = m.group(1)
            if name in empty:
                return keep(name, m.group(0))
            return subst[name]
        text = pat.sub(bare, text)
    return text


# M2: the audit compares values against the shipped template defaults,
# because the template's defaults are plausible-looking addresses, not the
# string PLACEHOLDER — a site file that never changed them would otherwise
# pass as "filled". Audited classes: networks, gateways, IPs, PBS host,
# DNS, NTP — plus (M5) the mail target, the pool sizing pair and the XCC
# login name, whose shipped values are just as plausible-looking:
# ops@example.com, 3T / 3.3 TB (correct only for the reference drives),
# USERID (Lenovo's factory login). M8 R4b: the PBS datastore path, whose
# shipped /mnt/backup-cerberus is a lab path (its guard in
# cerberus_require_vars is unchanged — this is audit coverage only).
AUDITED_RE = re.compile(r"(_NET|_GW|_IP|_IPS)$|^(PBS_HOST_IP|DNS_SERVERS|NTP_SERVERS"
                        r"|MAILTO|POOL_VM_TARGET_SIZE|POOL_VM_QUOTA_BYTES|XCC_USER"
                        r"|PBS_DATASTORE_PATH)$")


def audit_against_template(values, template_values):
    """Names in the audited classes whose site value equals the template's."""
    return sorted(n for n, v in values.items()
                  if AUDITED_RE.search(n) and n in template_values
                  and v != "" and v == template_values[n])


def credential_tripwire(values, doc_refs, environ):
    """(file_live, env_live): credential-class names holding a live value
    in the variables file / in the environment. Names only."""
    file_live = sorted(n for n, v in values.items()
                       if is_credential_class(n) and not is_placeholder_value(v))
    candidates = {n for n in values if is_credential_class(n)} | set(doc_refs)
    env_live = sorted(n for n in candidates
                      if n in environ and not is_placeholder_value(environ[n]))
    return file_live, env_live


def main():
    ap = argparse.ArgumentParser(description="Render the site-specific Cerberus guide.")
    ap.add_argument("--vars", default=None,
                    help="site variables file (default: <track>/site-vars.sh, a copy of "
                         "runbook/00-overview/variables.sh; the template only when named)")
    ap.add_argument("--out", required=True, help="fresh output directory")
    ap.add_argument("--force", action="store_true",
                    help="replace an existing output dir that a previous render wrote")
    args = ap.parse_args()

    args.vars = resolve_vars(args.vars)
    values = parse_variables(args.vars)

    sections = sorted(d for d in os.listdir(RUNBOOK_DIR)
                      if os.path.isdir(os.path.join(RUNBOOK_DIR, d)))
    texts = {}
    for section in sections:
        src = os.path.join(RUNBOOK_DIR, section, "README.md")
        if os.path.isfile(src):
            with open(src) as f:
                texts[section] = f.read()
    doc_refs = sorted({(m.group(1) or m.group(2)) for t in texts.values()
                       for m in REF_RE.finditer(t)
                       if is_credential_class(m.group(1) or m.group(2))})
    kept = sorted(set(doc_refs) | {n for n in values if is_credential_class(n)})

    # Tripwire BEFORE anything is written.
    file_live, env_live = credential_tripwire(values, doc_refs, os.environ)
    if file_live:
        bar = "!" * 72
        print(bar, file=sys.stderr)
        print("!! REFUSED: LIVE SECRETS IN THE VARIABLES FILE", file=sys.stderr)
        for n in file_live:
            print(f"!!   {n}: holds a real value in {args.vars}", file=sys.stderr)
        print("!! A deliverable is built from this file. Move the secret to the", file=sys.stderr)
        print("!! site's secrets vault (read it at run time: read -rs NAME; export", file=sys.stderr)
        print("!! NAME) and leave the name out of the file. Nothing was written.", file=sys.stderr)
        print(bar, file=sys.stderr)
        return 3
    if env_live:
        bar = "!" * 72
        print(bar, file=sys.stderr)
        print("!! WARNING: LIVE SECRETS LOADED NEAR A DELIVERABLE (environment)", file=sys.stderr)
        for n in env_live:
            print(f"!!   {n}: holds a real value (environment)", file=sys.stderr)
        print("!! The rendered guide was NOT given these values — it keeps the", file=sys.stderr)
        print("!! $NAME references. Unset them in this shell when you are done.", file=sys.stderr)
        print(bar, file=sys.stderr)

    if os.path.exists(args.out):
        if not args.force:
            sys.exit(f"refusing to overwrite existing {args.out} (use --force)")
        if not os.path.isfile(os.path.join(args.out, MARKER)):
            sys.exit(f"refusing to replace {args.out}: it was not written by render.py "
                     f"(no {MARKER} marker) — choose a fresh --out")
        shutil.rmtree(args.out)
    os.makedirs(args.out)
    with open(os.path.join(args.out, MARKER), "w") as f:
        f.write("written by proxmox-ceph/scripts/render.py; --force may replace this directory\n")

    out_abs = os.path.abspath(args.out)
    targets = {os.path.join(RUNBOOK_DIR, s, "README.md"):
               os.path.join(out_abs, "runbook", s, "README.md") for s in texts}
    rendered = 0
    passed = {}
    copies = {}
    for section in sections:
        if section not in texts:
            continue
        src = os.path.join(RUNBOOK_DIR, section, "README.md")
        text = rewrite_links(substitute(texts[section], values, passed),
                             src, targets[src], out_abs, targets, copies)
        os.makedirs(os.path.dirname(targets[src]))
        with open(targets[src], "w") as f:
            f.write(text)
        rendered += 1
    for src, dst in sorted(copies.items()):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)

    # Report: literal PLACEHOLDER, defined-but-empty (passed through), and
    # audited values still identical to the shipped template defaults.
    placeholder = sorted(n for n, v in values.items()
                         if v == "PLACEHOLDER" and not is_credential_class(n))
    empty = sorted(n for n, v in values.items()
                   if v == "" and not is_credential_class(n))
    template = parse_variables(DEFAULT_VARS)
    same = audit_against_template(values, template)
    is_template = os.path.realpath(args.vars) == os.path.realpath(DEFAULT_VARS)
    print(f"rendered {rendered} sections -> {args.out}/"
          + (f" (+{len(copies)} diagram(s) copied into diagrams/)" if copies else ""))
    print(f"variables read: {len(values)} from {args.vars}")
    print(f"credential-class names kept as $NAME, never substituted "
          f"({CREDENTIAL_CLASS_TEXT}): {', '.join(kept) if kept else '(none)'}")
    if is_template:
        print("NOTE: reading the pristine TEMPLATE (named explicitly with --vars) — "
              "the publisher's check, not a site render; every audited value below "
              "is a template default by definition.")
    if placeholder:
        print("UNFILLED variables (still PLACEHOLDER) — resolve before deploy:")
        for n in placeholder:
            print(f"  {n}")
    if empty:
        print("PASSED THROUGH (defined but EMPTY — never substituted; the "
              "rendered guide keeps the $VAR reference):")
        for n in empty:
            print(f"  {n}  ({passed.get(n, 0)} occurrence(s) left as-is)")
    if same:
        print(f"TEMPLATE DEFAULTS still in place ({len(same)} audited value(s) "
              f"identical to {os.path.relpath(DEFAULT_VARS, TRACK_DIR)}) — "
              "confirm each is really the site's value:")
        for n in same:
            print(f"  {n}={values[n]}")
    if not (placeholder or empty or same):
        print("all variables have site values; no audited value matches a template default.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
