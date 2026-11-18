#!/usr/bin/env python3
"""reflint.py — dangling-reference lint for the BCM track (Brief M6, M27).

Every internal reference in the track must land on something real — with
one deliberate, heuristic exception (Brief M6.1): the VERSION_CTX
heuristic suppresses section-shaped matches in a version context (a line
naming CUDA, a driver, Run:ai, kernel, Helm, "Python 3", ...), so
version strings like "k8s 1.34" or "CUDA 13.0" are not read as section
references. The suppression is also the heuristic's blind spot: a
genuinely dangling section reference on such a line is suppressed with
them and can pass. What is checked:

  1. section numbers   `06.7`, `3.4`, `§16.5` -> a `## 6.7 —` heading in
                       the section file of that number (06-*.md). Version
                       strings (k8s 1.34, Python 3.12, CUDA 13.0,
                       nfsvers=4.1, xml version="1.0") are not references.
  2. file names        `12-disk-layouts.md`, `scripts/check.sh`,
                       `../docs/diagrams/x.svg` -> a file that exists
                       (relative to the referencing file, the track, the
                       repo root, or scripts/). Files the operator creates
                       on site (inventory.csv, site-vars.sh, ...) are an
                       explicit, visible allowlist below.
  3. pass names        `runbook.py <pass>` -> a pass runbook.py defines; a
                       parenthesized list of 3+ pass names (one line or
                       wrapped across lines) must name every primary pass
                       (the M27 "4 of 8 passes" rot).
  4. section ranges    "sections 01 → 15" -> a range that starts at 01 must
                       end at the track's last section (16); no range may
                       run past it.

Scope: bcm/*.md (CHANGELOG.md excluded — it is history and quotes old
names on purpose), bcm/00-variables.sh, bcm/scripts/*.py (this file
excluded: its self-test plants bad references), and the front-door
README.md for its bcm/ paths and pass names.

Self-tested: before scanning, the lint must fire on planted dangling
references and stay quiet on the version strings above, or it fails.
A line may opt out with the marker `reflint: allow <reason>` (reason
mandatory). Exit 0 clean, 1 dangling references (or self-test failure).
Python 3.8+ stdlib only.
"""

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.dirname(HERE)
REPO = os.path.dirname(TRACK)

# Files an operator creates on site — referenced by name, never shipped.
SITE_FILES = {
    "site-vars.sh", "inventory.csv", "accounts.csv", "networks.csv",
    "passwords.csv", "provision-nodes.sh", "create-users.sh", "burn.py",
    "gpu-burnin.yaml", "vitals-hosts.csv",
    "technitium-install.sh",   # 14.2: the vendor installer, downloaded and read before running (L10)
}
# Files shipped elsewhere (the separate anonymizer pack).
EXTERNAL_FILES = {"anonymize.py"}
# Files that live on the BCM head node (BCM's own stock disk layouts).
HEAD_NODE_FILE_RE = re.compile(r"^(disklayout|x86_64-slave-[\w.-]+)\.xml$")
PRIMARY_PASSES = ("vars", "networks", "images", "disklayouts", "nodes",
                  "storage", "users", "render")
ALLOW = re.compile(r"reflint:\s*allow\s+\S")

SEC_RE = re.compile(r"(?<![\w.:/=\"'-])(§\s?)?(\d{1,2})\.(\d{1,2}[a-z]?)(?![\w]|\.\d)")
VERSION_CTX = re.compile(
    r"(CUDA|nfsvers|[Pp]ython ?3?|[Vv]ersion|k8s|[Kk]ubernetes|Ubuntu|K8S_VERSION"
    r"|DGX OS|OFED|[Hh]elm|kernel|[Dd]river|Run:ai|GPU Operator|CSI|PyTorch"
    r"|library|pytorch:|dialog|capability|dgx-vitals)\b|\bv\s*$|\bBCM ?$")
FILE_RE = re.compile(
    r"(?<![\w/.$<>-])((?:\.\.?/)*(?:[\w.-]+/)*\w[\w.-]*\.(?:md|sh|py|csv|svg|png|xml|docx|pdf))\b")
# A pass invocation: the script named as a command (scripts/, ./, python3).
PASS_RE = re.compile(r"(?:scripts/|\./|python3\s+\S*)runbook\.py\s+(?:--vars[ =]\S+\s+)?([a-z][a-z0-9]*)(?=\s+-|\s*$|\s*[`\"'|;)#])")
LIST_RE = re.compile(r"\(((?:\s*`[a-z]+`\s*,?)+)\)")
# LIST_RE is one-line only: a pass list wrapped across lines (the 8-pass
# list in 00-overview.md spans three) never matches it. WRAP_RE catches a
# paren group a line break splits, so wrapped lists are checked too.
WRAP_RE = re.compile(r"\([^()\n]*\n[^()]*\)")
RANGE_RE = re.compile(
    r"[Ss]ections?\s+\**\s*(\d{2})\s*\**\s*(?:→|->|–|—|-|to|through)\s*\**\s*(\d{2})\b")


def section_headings(track):
    heads = set()
    files = {}
    for fn in glob.glob(os.path.join(track, "[0-9][0-9]-*.md")):
        num = int(os.path.basename(fn)[:2])
        files[num] = fn
        for line in open(fn, encoding="utf-8"):
            m = re.match(r"^#{2,4}\s+(\d+)\.(\d+[a-z]?)\b", line)
            if m and int(m.group(1)) == num:
                heads.add((num, m.group(2).lower()))
    return heads, files


def passes_defined(track):
    src = open(os.path.join(track, "scripts", "runbook.py"), encoding="utf-8").read()
    return set(re.findall(r'sub\.add_parser\(\s*"([a-z]+)"', src))


def last_section(files):
    return max(n for n in files if n < 99)


def list_problems(text, where, ctx):
    """Pass lists in a text blob — one line, or a paren group a line
    break splits (WRAP_RE; LIST_RE matches with a line break in them are
    the wrapped case): 3+ named passes must name every primary pass."""
    out = []
    spans = [m.group(1) for m in LIST_RE.finditer(text) if "\n" not in m.group(1)]
    spans += [m.group(0) for m in WRAP_RE.finditer(text)]
    for span in spans:
        names = re.findall(r"`([a-z]+)`", span)
        named = [n for n in names if n in ctx["passes"]]
        if len(named) >= 3:
            missing = [p for p in PRIMARY_PASSES if p not in names]
            if missing:
                out.append(f"{where}: pass list ({', '.join(names)}) omits "
                           f"{', '.join(missing)}")
    return out


def check_line(line, where, ctx):
    """Problems on one line; ctx = dict(heads, passes, last, base_dirs, md)."""
    out = []
    if ALLOW.search(line):
        return out
    if ctx["md"] and re.match(r"^#{1,4}\s", line):
        return out                    # a heading defines, it doesn't refer
    for m in SEC_RE.finditer(line):
        major = int(m.group(2))
        if not 1 <= major <= ctx["last"]:
            continue
        if not m.group(1) and VERSION_CTX.search(line[max(0, m.start() - 60):m.start()]):
            continue
        if (major, m.group(3).lower()) not in ctx["heads"]:
            out.append(f"{where}: section {m.group(0).strip()} — no heading "
                       f"'## {major}.{m.group(3)}' in section {major:02d}")
    for m in FILE_RE.finditer(line):
        ref = m.group(1)
        pre = line[:m.start()]
        if pre.endswith("//") or re.search(r"https?://\S*$", pre) or pre.endswith("$"):
            continue
        base = os.path.basename(ref)
        if base in SITE_FILES or base in EXTERNAL_FILES or HEAD_NODE_FILE_RE.match(base):
            continue
        if ctx.get("py") and line[m.end():m.end() + 1] in ("):,[}") and "/" not in ref:
            continue                  # a Python attribute (a.csv), not a file name
        if "/" not in ref and base in ctx["repo_names"]:
            continue                  # a bare name that exists somewhere in the repo
        if not any(os.path.exists(os.path.normpath(os.path.join(d, ref)))
                   for d in ctx["base_dirs"]):
            out.append(f"{where}: file {ref} — not found in the repo (and not a "
                       f"listed site-created file)")
    for m in PASS_RE.finditer(line):
        if m.group(1) not in ctx["passes"]:
            out.append(f"{where}: pass `{m.group(1)}` — runbook.py defines no such pass")
    out += list_problems(line, where, ctx)
    for m in RANGE_RE.finditer(line):
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi > ctx["last"] or lo > hi or (lo == 1 and hi != ctx["last"]):
            out.append(f"{where}: section range {m.group(1)}–{m.group(2)} — the "
                       f"track runs 01 → {ctx['last']:02d}")
    return out


def self_test(ctx):
    must_hit = [
        "versions: see 12-baseline.md",                  # dangling file
        "the PG dump pattern is in 06.9",                # no such heading
        "StorageClasses are safe to switch (07.3).",     # the M27 original
        "run (`vars`, `images`, `nodes`, `users`)",      # 4 of 8 passes
        "the runner composes (`vars`, `networks`,\n`images`, `renderz`, `users`)",  # wrapped 4-of-8, bogus pass
        "Work sections 01 → 15 in order",                # short range
        "scripts/runbook.py imagez --dry-run",           # unknown pass
    ]
    must_pass = [
        "k8s 1.34 (client baseline)", "Python 3.12 stdlib-only",
        "Expect: driver 580.95.05, CUDA 13.0", "  - nfsvers=4.1",
        "B300 is compute capability **10.3**", "(the dgx-vitals 2.0 vocabulary)",
        '<?xml version="1.0" encoding="UTF-8"?>', "see 06.7 for the dump",
        "Work sections 01 → 16 in order", "scripts/runbook.py render --out x",
        "cp 00-variables.sh site-vars.sh", "https://example.com/start.sh",
        # the real 8-pass list in 00-overview.md, verbatim: it wraps across
        # three lines and must pass clean (all 8 primary passes named)
        "runbook job (`vars`, `networks`, `images`, `disklayouts`, `nodes`,\n"
        "`storage`, `users`, `render` — plus the read-only `layoutgate` fleet\ngate)",
    ]
    bad = [t for t in must_hit if not check_line(t, "self-test", ctx)]
    bad += [t for t in must_pass if check_line(t, "self-test", ctx)]
    return bad


def main(argv):
    track = os.path.abspath(argv[1]) if len(argv) > 1 else TRACK
    repo = os.path.dirname(track)
    heads, files = section_headings(track)
    names = {f for _, _, fs in os.walk(repo) for f in fs}
    ctx = {"heads": heads, "passes": passes_defined(track), "last": last_section(files),
           "repo_names": names}
    base = [track, repo, os.path.join(track, "scripts")]
    st = self_test(dict(ctx, base_dirs=base, md=True))
    if st:
        print(f"REFLINT SELF-TEST FAILED: {st}")
        return 1
    targets = [f for f in sorted(glob.glob(os.path.join(track, "*.md")))
               if os.path.basename(f) != "CHANGELOG.md"]
    targets += [os.path.join(track, "00-variables.sh")]
    targets += [f for f in sorted(glob.glob(os.path.join(track, "scripts", "*.py")))
                if os.path.basename(f) != "reflint.py"]
    front = os.path.join(repo, "README.md")
    problems, refs = [], 0
    for fn in targets + ([front] if os.path.isfile(front) else []):
        rel = os.path.relpath(fn, repo)
        lctx = dict(ctx, md=fn.endswith(".md"), py=fn.endswith(".py"),
                    base_dirs=[os.path.dirname(fn)] + base)
        text = open(fn, encoding="utf-8").read()
        lines = text.splitlines()
        for n, line in enumerate(lines, 1):
            if fn == front and "bcm" not in line and "runbook.py" not in line:
                continue              # the front door: only its BCM references
            refs += 1
            problems += check_line(line.rstrip("\n"), f"{rel}:{n}", lctx)
        # The line scan above cannot see a pass list wrapped across lines
        # (LIST_RE is one-line only): check each paren group a line break
        # splits, reported at its opening line.
        for m in WRAP_RE.finditer(text):
            n = text.count("\n", 0, m.start())
            span, open_line = m.group(0), lines[n]
            if ALLOW.search(span) or ALLOW.search(open_line):
                continue
            if fn == front and "bcm" not in span and "runbook.py" not in span:
                continue              # the front door: only its BCM references
            if lctx["md"] and re.match(r"^#{1,4}\s", open_line):
                continue              # a heading defines, it doesn't refer
            refs += 1
            problems += list_problems(span, f"{rel}:{n + 1}", lctx)
    for p in problems:
        print(f"DANGLING-REF {p}")
    print(f"reflint: {len(problems)} dangling reference(s) in {len(targets) + 1} files "
          f"(sections 01–{ctx['last']:02d}, {len(heads)} numbered headings, "
          f"{len(ctx['passes'])} passes; self-test OK)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
