#!/usr/bin/env python3
"""crosstrack.py — cross-track IP-plan guard (BCM <-> Cerberus).

Compares one site's two variables files and fails on any overlap between
the BCM plan and the Cerberus plan:

  BCM       internalnet / storagenet / usernet / mgmtnet (NET_PROVISION,
            NET_STORAGE, NET_USER, NET_OOB), the admin source networks
            (NET_ADMIN_SRC_A/B), both Kubernetes ranges (K8S_SERVICE_*,
            K8S_POD_*), the DNS hosts (NET_DNS1/2)
  Cerberus  MGMT_NET, CEPH_PUBLIC_NET, CEPH_CLUSTER_NET, PBS_NET, every
            *_IP host address, the DNS_SERVERS hosts

One exception, by design: a BCM DNS host and a Cerberus DNS server that
are the SAME address are a shared resolver, not a collision.

Each file is read by its own track's parser — runbook.load_vars() for
BCM, render.parse_variables() for Cerberus (both the ${NAME:-default}
form and a plain NAME="value" a site may write) — so this tool never
disagrees with the track about what a file says. And it never passes
silently over an unparsed value (M8.1): a compared name the raw file
assigns but the parser produced no comparable value for — a no-export
or indented line the parser skips, or a *_IP value that is not a bare
IPv4 address — is an error (value form not comparable, exit 2), never
a skip.

Built in M7 as a read-only design-close check (0 overlaps between the
shipped templates; a planted overlap caught); ruled into the BCM gate in
M8 (R1). bcm/scripts/check.sh runs it on the shipped templates, on a
planted overlap (self-test), and on the site pair when BOTH site files
exist (bcm/site-vars.sh and proxmox-ceph/site-vars.sh).

Usage:
    scripts/crosstrack.py BCM_VARS CERBERUS_VARS

Exit: 0 no overlap · 1 overlap(s) found · 2 a file is missing, a
required network is absent or invalid, or a compared value's form is
not comparable (fails closed — never a pass).
Python 3.8+ stdlib only; writes nothing (no bytecode either).
"""

import importlib.util
import ipaddress
import os
import re
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CERB_RENDER = os.path.join(REPO, "proxmox-ceph", "scripts", "render.py")

BCM_NETS = ("NET_PROVISION", "NET_STORAGE", "NET_USER", "NET_OOB", "NET_ADMIN_SRC_A")
BCM_OPTIONAL_NETS = ("NET_ADMIN_SRC_B",)        # "" = unused (14.1)
BCM_DNS = ("NET_DNS1", "NET_DNS2")
CERB_NETS = ("MGMT_NET", "CEPH_PUBLIC_NET", "CEPH_CLUSTER_NET", "PBS_NET")
IPV4 = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
# Any assignment line a site might write (M8.1 F-A): export or plain,
# indented or not. Commented lines (leading #) never match.
ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
FUNC_START = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{\s*$")


def _read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _unwrap_value(rest, name):
    """The value one raw assignment line assigns — quote-aware, a trailing
    `# comment` stripped only outside the quotes, and the ${NAME:-default}
    / ${NAME:=default} shell forms unwrapped."""
    rest = rest.strip()
    if rest[:1] in ('"', "'"):
        end = rest.find(rest[0], 1)
        rest = rest[1:] if end < 0 else rest[1:end]
    else:
        rest = rest.split("#", 1)[0]
    m = re.match(r"^\$\{%s(:[-=])(.*)\}$" % re.escape(name), rest)
    if m:
        rest = m.group(2).strip()
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
            rest = rest[1:-1]
    return rest.strip()


def _raw_assignments(text):
    """(name, value) for every assignment line in the raw file — export or
    plain, quoted, bare or ${NAME:-default}, indented or not. Lines inside
    a function body are skipped, as render.parse_variables skips them:
    helper locals are not site variables."""
    out = []
    in_func = False
    for line in text.splitlines():
        if in_func:
            if line.rstrip() == "}":
                in_func = False
            continue
        if FUNC_START.match(line.rstrip()):
            in_func = True
            continue
        m = ASSIGN_RE.match(line)
        if m:
            out.append((m.group(1), _unwrap_value(m.group(2), m.group(1))))
    return out


def _unparsed(text, parsed, names):
    """Compared names the raw file assigns a value to but the track parser
    produced no comparable value for (M8.1 F-A: an error, never a silent
    skip). A name merely absent from the file — or explicitly empty — is
    not an error: optional nets may be unused. A parsed *_IP value that is
    not a bare IPv4 address is not comparable either: the *_IP host scan
    below skips it, so it must be named here, not dropped."""
    raw = {}
    for n, v in _raw_assignments(text):
        raw.setdefault(n, []).append(v)
    out = []
    for name in names:
        if not any(raw.get(name, ())):
            continue                    # absent, or explicitly empty
        v = parsed.get(name, "")
        if not v or (name.endswith("_IP") and not IPV4.match(v)):
            out.append(name)
    return out


def _bcm_loader():
    sys.path.insert(0, HERE)
    from runbook import load_vars
    return load_vars


def _cerb_loader():
    spec = importlib.util.spec_from_file_location("cerberus_render", CERB_RENDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_variables


def plans(bcm_path, cerb_path):
    """(bcm ranges, cerberus ranges, errors) — each a {label: ip_network}."""
    errs = []
    b = _bcm_loader()(bcm_path)
    c = _cerb_loader()(cerb_path)

    # M8.1 F-A: a compared name the raw file assigns a value to but the
    # track parser produced no comparable value for fails loudly — a
    # no-export/indented line, or a *_IP value that is not a bare IPv4
    # address, must never pass silently. A required net in such a form is
    # reported as not comparable, not as missing.
    cerb_ip_names = sorted({n for n, _ in _raw_assignments(_read_text(cerb_path))
                            if n.endswith("_IP")})
    bcm_unparsed = _unparsed(_read_text(bcm_path), b,
                             BCM_NETS + BCM_OPTIONAL_NETS + BCM_DNS)
    cerb_unparsed = _unparsed(_read_text(cerb_path), c,
                              CERB_NETS + ("DNS_SERVERS",) + tuple(cerb_ip_names))
    for src, names in (("BCM", bcm_unparsed), ("Cerberus", cerb_unparsed)):
        for name in names:
            errs.append(f"{src}: {name} value form not comparable — use the "
                        f"track's standard assignment form")

    def net(vals, name, src, required=True, host=False, unparsed=()):
        v = vals.get(name, "")
        if not v:
            if required and name not in unparsed:
                errs.append(f"{src}: {name} is missing or empty")
            return None
        try:
            return ipaddress.ip_network(v + "/32" if host else v, strict=True)
        except ValueError as e:
            errs.append(f"{src}: {name}={v} is not a valid network: {e}")
            return None

    bn, cn = {}, {}
    for k in BCM_NETS + BCM_OPTIONAL_NETS:
        n = net(b, k, "BCM", required=k in BCM_NETS, unparsed=bcm_unparsed)
        if n:
            bn[k] = n
    for label, k_net, k_mask in (("K8S_SERVICE", "K8S_SERVICE_NET", "K8S_SERVICE_MASK"),
                                 ("K8S_POD", "K8S_POD_NET", "K8S_POD_MASK")):
        spec = f"{b.get(k_net, '')}/{b.get(k_mask, '')}"
        try:
            bn[label] = ipaddress.ip_network(spec, strict=True)
        except ValueError as e:
            errs.append(f"BCM: {label} range {spec} is not a valid network: {e}")
    for k in BCM_DNS:
        n = net(b, k, "BCM", host=True, unparsed=bcm_unparsed)
        if n:
            bn[k] = n
    for k in CERB_NETS:
        n = net(c, k, "Cerberus", unparsed=cerb_unparsed)
        if n:
            cn[k] = n
    for k, v in sorted(c.items()):
        if k.endswith("_IP") and IPV4.match(v):
            n = net(c, k, "Cerberus", host=True, unparsed=cerb_unparsed)
            if n:
                cn[k] = n
    for v in c.get("DNS_SERVERS", "").split():
        try:
            cn["DNS_SERVERS:" + v] = ipaddress.ip_network(v + "/32", strict=True)
        except ValueError as e:
            errs.append(f"Cerberus: DNS_SERVERS entry {v} is not an address: {e}")
    return bn, cn, errs


def overlaps(bn, cn):
    out = []
    for bk, bnet in bn.items():
        for ck, cnet in cn.items():
            if not bnet.overlaps(cnet):
                continue
            if bk in BCM_DNS and ck.startswith("DNS_SERVERS:") and bnet == cnet:
                continue                    # one resolver shared by both tracks
            out.append(f"BCM {bk} {bnet}  <->  Cerberus {ck} {cnet}")
    return out


def main(argv):
    if len(argv) != 3:
        print("usage: crosstrack.py BCM_VARS CERBERUS_VARS", file=sys.stderr)
        return 2
    bcm_path, cerb_path = argv[1], argv[2]
    for p in (bcm_path, cerb_path):
        if not os.path.isfile(p):
            print(f"CROSSTRACK-FAIL variables file {p} not found")
            return 2
    bn, cn, errs = plans(bcm_path, cerb_path)
    for e in errs:
        print(f"CROSSTRACK-FAIL {e}")
    if errs:
        print(f"crosstrack: NOT COMPARED — fix the file(s) above ({bcm_path} / {cerb_path})")
        return 2
    found = overlaps(bn, cn)
    for o in found:
        print(f"CROSSTRACK-OVERLAP {o}")
    print(f"crosstrack: {len(found)} overlap(s) — {len(bn)} BCM ranges/hosts vs "
          f"{len(cn)} Cerberus ranges/hosts ({bcm_path} / {cerb_path})")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
