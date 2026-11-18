#!/usr/bin/env python3
"""reflint.py — dangling-reference lint for the Cerberus track (Brief M6).

Every internal reference in the track must land on something real — with
one deliberate, rule-driven exception (Brief M6.1, mirroring the BCM
lint's honest wording): version-shaped matches are suppressed by rule —
a dotted reference needs `§` or a zero-padded section number (item 2
below), and a bare `§N` outside a runbook README is not linted at all
(it has no unambiguous owner). That suppression has a blind spot: a
genuinely dangling reference written in suppressed form ("PVE 9.2",
`6.9` without `§`, bare `§4` outside a runbook README) passes. What is
checked:

  1. sections          `§02`, `§00a` -> a runbook/NN-*/ directory exists.
                       `§13`/`§16` past this track's last section count
                       only on a line that names the BCM track, and must
                       exist there (bcm/NN-*.md).
  2. sub-sections      `01 §7`, `§02 §4`, `00a §7a`, `§03 Step 5a`,
                       `§7.6`, `06.7` -> a matching heading in that
                       section's README (`## 7.`, `### 7a.`, `## Step 5a`,
                       `## 7.6`). A bare `§4` inside a runbook README is
                       that README's own heading `## 4.` / `## 4.x` /
                       `Step 4`. Versions (PVE 9.2, PBS 4.2, Tentacle
                       20.2.x) are never references: a dotted reference
                       needs `§` or a zero-padded section number.
  3. file names        `docs/references.md`, `../../docs/x.md`,
                       `scripts/net-verify.sh` -> a file that exists
                       (relative to the referencing file, the track, the
                       repo root, or $CERBERUS_TRACK = the track). Files
                       the operator creates (site-vars.sh, ...) are an
                       explicit allowlist.
  4. section ranges    "00 → 07" claims must end at the last section.

Scope: README.md, runbook/*/README.md, docs/*.md, scripts/*.sh,
scripts/render.py, runbook/00-overview/variables.sh (this file excluded:
its self-test plants bad references), plus the front-door README.md lines
that name proxmox-ceph/. Self-tested: the lint must fire on
planted dangling references and stay quiet on version strings, or it
fails. A line may opt out with `reflint: allow <reason>`. Exit 0 clean,
1 dangling references (or self-test failure). Python 3.8+ stdlib only.
"""

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.dirname(HERE)
REPO = os.path.dirname(TRACK)

SITE_FILES = {"site-vars.sh", "cerberus-node.env", "interfaces"}
ALLOW = re.compile(r"reflint:\s*allow\s+\S")

SUB_RE = re.compile(r"(?<![\w.])§?\s?(0\d[a-z]?)(?:-[a-z0-9-]+)?\s*(?:§\s*(\d+[a-z]?)(?:\.(\d+))?|Step\s+(\d+[a-z]?))")
DOT_RE = re.compile(r"(?<![\w.:/=-])(§\s?(\d{1,2})|(0\d))\.(\d{1,2}[a-z]?)(?![\w]|\.\d)")
SEC_RE = re.compile(r"(?<![\w.])§\s?(\d\d[a-z]?)(?![\w.]|\s*§|\s*Step\b)")
BARE_RE = re.compile(r"(?<![\w.§])(?<!\d )(?<!\d[a-z] )§\s?(\d[a-z]?)(?![\w.])")
FILE_RE = re.compile(
    r"(?<![\w/.$<>-])((?:\.\.?/)*(?:[\w.-]+/)*\w[\w.-]*\.(?:md|sh|py|svg|png|docx|pdf))\b")
RANGE_RE = re.compile(r"\b(00a?)\s*(?:→|->|–|—|-|to|through)\s*(0\d)\b")


def sections(track):
    out = {}
    for d in sorted(glob.glob(os.path.join(track, "runbook", "*-*"))):
        key = os.path.basename(d).split("-", 1)[0]
        readme = os.path.join(d, "README.md")
        heads = set()
        if os.path.isfile(readme):
            for line in open(readme, encoding="utf-8"):
                m = re.match(r"^#{2,4}\s+(?:Step\s+(\d+[a-z]?)\b|(\d+[a-z]?)\.(\d+)?)", line)
                if not m:
                    continue
                if m.group(1):
                    heads.add(("step", m.group(1)))
                elif m.group(3):
                    heads.add(("dot", f"{m.group(2)}.{m.group(3)}"))
                    heads.add(("num", m.group(2)))
                else:
                    heads.add(("num", m.group(2)))
        out[key] = (readme, heads)
    return out


def has_sub(heads, sub):
    return ("num", sub) in heads or ("step", sub) in heads or \
        any(k == "dot" and v.startswith(sub + ".") for k, v in heads)


def check_line(line, where, ctx):
    out = []
    if ALLOW.search(line):
        return out
    if ctx["md"] and re.match(r"^#{1,4}\s", line):
        return out
    secs, bcm = ctx["secs"], ctx["bcm"]
    covered = []
    for m in SUB_RE.finditer(line):
        covered.append(m.span())
        key, sub, dot, step = m.group(1), m.group(2), m.group(3), m.group(4)
        if key not in secs:
            out.append(f"{where}: {m.group(0).strip()} — no runbook section {key}")
            continue
        heads = secs[key][1]
        if step:
            if ("step", step) not in heads:
                out.append(f"{where}: {m.group(0).strip()} — section {key} has no 'Step {step}'")
        elif dot:
            if ("dot", f"{sub}.{dot}") not in heads:
                out.append(f"{where}: {m.group(0).strip()} — section {key} has no '## {sub}.{dot}'")
        elif not has_sub(heads, sub):
            out.append(f"{where}: {m.group(0).strip()} — section {key} has no heading {sub}")

    def inside(pos):
        return any(a <= pos < b for a, b in covered)

    for m in DOT_RE.finditer(line):
        if inside(m.start()):
            continue
        major = m.group(2) or m.group(3)
        key = f"{int(major):02d}"
        ref = f"{int(major)}.{m.group(4)}"
        if key not in secs or ("dot", ref) not in secs[key][1]:
            out.append(f"{where}: {m.group(0).strip()} — no heading '## {ref}' in section {key}")
    for m in SEC_RE.finditer(line):
        if inside(m.start()):
            continue
        key = m.group(1)
        if key in secs:
            continue
        if "BCM" in line and key.isdigit() and glob.glob(os.path.join(bcm, f"{key}-*.md")):
            continue
        out.append(f"{where}: §{key} — no such section"
                   + (" (a BCM section? name the BCM track on the line)" if key.isdigit() and int(key) > 7 else ""))
    for m in BARE_RE.finditer(line):
        if inside(m.start()):
            continue
        # A bare §N belongs to the nearest section named just before it
        # ("01 (§7)", "00a phase A (§1, §2", "01 §4–§5", "00a §5/§8");
        # otherwise to the README it sits in (outside a runbook README a
        # bare §N is not linted — it has no unambiguous owner).
        q = list(re.finditer(r"\b(0\d[a-z]?)(?:-[a-z0-9-]+)?\b", line[max(0, m.start() - 40):m.start()]))
        row = re.match(r"^\|\s*(0\d[a-z]?)\s*\|", line)   # a section-map table row
        if q and q[-1].group(1) in secs:
            heads, label = secs[q[-1].group(1)][1], f"section {q[-1].group(1)}"
        elif row and row.group(1) in secs:
            heads, label = secs[row.group(1)][1], f"section {row.group(1)}"
        elif ctx["own"] is not None:
            heads, label = ctx["own"], "this section"
        else:
            continue
        if not has_sub(heads, m.group(1)):
            out.append(f"{where}: §{m.group(1)} — {label} has no heading {m.group(1)}")
    for m in FILE_RE.finditer(line):
        ref = m.group(1)
        pre = line[:m.start()]
        if re.search(r"https?://\S*$", pre) or pre.endswith("//"):
            continue
        if line[m.end():m.end() + 2] == "](" or "NN" in ref:
            continue                  # Markdown link text (its target is checked) / a pattern
        base = os.path.basename(ref)
        if base in SITE_FILES:
            continue
        if "/" not in ref and base in ctx["repo_names"]:
            continue
        if pre.endswith("$CERBERUS_TRACK/") or pre.endswith("${CERBERUS_TRACK}/"):
            dirs = [ctx["track"]]
        else:
            dirs = ctx["base_dirs"]
        if not any(os.path.exists(os.path.normpath(os.path.join(d, ref))) for d in dirs):
            out.append(f"{where}: file {ref} — not found")
    for m in RANGE_RE.finditer(line):
        if m.group(2) != ctx["last"]:
            out.append(f"{where}: range {m.group(1)} → {m.group(2)} — the track runs 00 → {ctx['last']}")
    return out


def self_test(ctx):
    must_hit = ["see 02 §9 for the bond", "restore per §06.9", "drill (d) is §7.5(d)x §7.9",
                "§03 Step 9 wipes it", "details: docs/no-such-brief.md",
                "follow runbook/ 00 -> 06", "§16 has the UFM rule"]
    must_pass = ["PVE 9.2 ships kernel 7.0", "PBS 4.2 GA", "Tentacle 20.2.4",
                 "the procedure is 01 §7", "§03 Step 5a", "§7.6", "06.7",
                 "see docs/references.md", "follow runbook/ 00 -> 07",
                 "(§16 of the BCM track)", "00a §7a"]
    bad = [t for t in must_hit if not check_line(t, "self-test", ctx)]
    bad += [t for t in must_pass if check_line(t, "self-test", ctx)]
    return bad


def main(argv):
    track = os.path.abspath(argv[1]) if len(argv) > 1 else TRACK
    repo = os.path.dirname(track)
    secs = sections(track)
    last = max(k for k in secs if k[:2].isdigit())[:2]
    names = {f for _, _, fs in os.walk(repo) for f in fs}
    base = [track, repo]
    ctx = {"secs": secs, "bcm": os.path.join(repo, "bcm"), "last": last,
           "repo_names": names, "track": track}
    st = self_test(dict(ctx, md=True, own=None, base_dirs=base))
    if st:
        print(f"REFLINT SELF-TEST FAILED: {st}")
        return 1
    targets = [os.path.join(track, "README.md")]
    targets += sorted(glob.glob(os.path.join(track, "runbook", "*", "README.md")))
    targets += sorted(glob.glob(os.path.join(track, "docs", "*.md")))
    targets += sorted(glob.glob(os.path.join(track, "scripts", "*.sh")))
    targets += [os.path.join(track, "scripts", "render.py"),
                os.path.join(track, "runbook", "00-overview", "variables.sh")]
    front = os.path.join(repo, "README.md")
    if os.path.isfile(front):
        targets.append(front)
    problems = []
    for fn in targets:
        own = None
        d = os.path.dirname(fn)
        if os.path.basename(os.path.dirname(d)) == "runbook":
            own = secs.get(os.path.basename(d).split("-", 1)[0], (None, set()))[1]
        lctx = dict(ctx, md=fn.endswith(".md"), own=own, base_dirs=[d] + base)
        for n, line in enumerate(open(fn, encoding="utf-8"), 1):
            if fn == front and "proxmox-ceph" not in line:
                continue              # the front door: only its Cerberus references
            problems += check_line(line.rstrip("\n"), f"{os.path.relpath(fn, repo)}:{n}", lctx)
    for p in problems:
        print(f"DANGLING-REF {p}")
    print(f"reflint: {len(problems)} dangling reference(s) in {len(targets)} files "
          f"(sections 00 → {last}, {sum(len(h) for _, h in secs.values())} headings; "
          f"self-test OK)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
