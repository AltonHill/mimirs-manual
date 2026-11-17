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

Usage:
    scripts/render.py --out site-docs/
    scripts/render.py --vars /path/to/variables.sh --out site-docs/ [--force]

The template docs are never modified; render always writes a fresh directory.
The render report lists: values still literally PLACEHOLDER; defined-but-
empty names (passed through, never blanked); and every audited value
(networks, gateways, IPs, PBS host, DNS, NTP) still identical to the shipped
template default in runbook/00-overview/variables.sh.
"""

import argparse
import os
import re
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRACK_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_VARS = os.path.join(TRACK_DIR, "runbook", "00-overview", "variables.sh")
RUNBOOK_DIR = os.path.join(TRACK_DIR, "runbook")


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
    with open(path) as f:
        for line in f:
            line = line.strip()
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
                # never expand to an empty string (same rule as substitute)
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
    subst = {k: v for k, v in values.items() if v != ""}
    empty = {k for k, v in values.items() if v == ""}

    def keep(name, original):
        passed[name] = passed.get(name, 0) + 1
        return original

    # ${VAR} not followed by :- or :=
    def braced(m):
        name = m.group(1)
        if name in empty:
            return keep(name, m.group(0))
        return subst.get(name, m.group(0))
    text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}(?![:-])", braced, text)

    # $VAR (bare) — word boundary, not followed by identifier chars
    names = sorted(values, key=len, reverse=True)
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
# DNS, NTP.
AUDITED_RE = re.compile(r"(_NET|_GW|_IP|_IPS)$|^(PBS_HOST_IP|DNS_SERVERS|NTP_SERVERS)$")


def audit_against_template(values, template_values):
    """Names in the audited classes whose site value equals the template's."""
    return sorted(n for n, v in values.items()
                  if AUDITED_RE.search(n) and n in template_values
                  and v != "" and v == template_values[n])


def main():
    ap = argparse.ArgumentParser(description="Render the site-specific Cerberus guide.")
    ap.add_argument("--vars", default=DEFAULT_VARS, help="variables.sh to read")
    ap.add_argument("--out", required=True, help="fresh output directory")
    ap.add_argument("--force", action="store_true", help="overwrite existing output dir")
    args = ap.parse_args()

    if not os.path.isfile(args.vars):
        sys.exit(f"variables file not found: {args.vars}")
    values = parse_variables(args.vars)

    if os.path.exists(args.out):
        if not args.force:
            sys.exit(f"refusing to overwrite existing {args.out} (use --force)")
        shutil.rmtree(args.out)
    os.makedirs(args.out)

    sections = sorted(d for d in os.listdir(RUNBOOK_DIR)
                      if os.path.isdir(os.path.join(RUNBOOK_DIR, d)))
    rendered = 0
    passed = {}
    for section in sections:
        src = os.path.join(RUNBOOK_DIR, section, "README.md")
        if not os.path.isfile(src):
            continue
        with open(src) as f:
            text = f.read()
        out_dir = os.path.join(args.out, "runbook", section)
        os.makedirs(out_dir)
        with open(os.path.join(out_dir, "README.md"), "w") as f:
            f.write(substitute(text, values, passed))
        rendered += 1

    # Report: literal PLACEHOLDER, defined-but-empty (passed through), and
    # audited values still identical to the shipped template defaults.
    placeholder = sorted(n for n, v in values.items() if v == "PLACEHOLDER")
    empty = sorted(n for n, v in values.items() if v == "")
    template = parse_variables(DEFAULT_VARS)
    same = audit_against_template(values, template)
    is_template = os.path.realpath(args.vars) == os.path.realpath(DEFAULT_VARS)
    print(f"rendered {rendered} sections -> {args.out}/")
    print(f"variables read: {len(values)} from {args.vars}")
    if is_template:
        print("NOTE: rendering the TEMPLATE itself (no site file) — every audited "
              "value below is a template default by definition.")
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
