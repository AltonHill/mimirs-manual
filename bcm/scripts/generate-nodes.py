#!/usr/bin/env python3
"""generate-nodes.py — BCM node provisioning from a CSV inventory.

Reads a node inventory CSV (exported from a spreadsheet) and generates
the cmsh commands to add every node: device creation, provisioning
interface, DHCP, MAC, category, and BMC interfaces for GPU nodes.

CSV columns, in this order (header row required):
    hostname,ip,mac,role,category,interface,bmc_ip

  hostname   short hostname, e.g. k8s-ctrl-1, gpu-worker-1
  ip         provisioning-network IP, e.g. 10.10.15.50
  mac        provisioning NIC MAC, e.g. 00:11:22:33:44:55
  role       ctrl | worker | gpu   (informational; drives grouping)
  category   BCM category, e.g. runai-cp-cat, runai-dgx-cat
  interface  provisioning interface name, e.g. ens33, enp225s0f0np0
  bmc_ip     BMC IP on the OOB network (empty for non-GPU nodes)

Usage:
    # 1. Make a skeleton CSV to fill in (MACs/IPs are placeholders):
    ./generate-nodes.py --skeleton --ctrl 3 --workers 4 --gpus 74 > inventory.csv

    # 2. Fill in real MACs/IPs/hostnames in your spreadsheet, export CSV.

    # 3. Validate only:
    ./generate-nodes.py --check inventory.csv

    # 4. Generate the cmsh script (review it before running):
    ./generate-nodes.py inventory.csv > provision-nodes.sh
    bash -n provision-nodes.sh   # syntax check
    # then run it on the BCM head node — only after the 03.4 disk-layout
    # gate has passed for every host type in the inventory. The script
    # enforces that itself: before its first add it runs
    # `runbook.py layoutgate` over every category in the inventory and
    # stops unless each carries a by-path layout (RUNBOOK=<path> if the
    # repo moved). Override (D3, logged): --allow-non-by-path --reason "..."

The skeleton follows the site variables file (--vars, default ../site-vars.sh —
a copy of the 00-variables.sh template; the template itself is refused)
and section 01:
  - hostnames take the $K8S_CTRL / $GPU_WORKERS form (gpu-worker-1, ...);
  - control plane starts at the first $K8S_CTRL_IPS address (.50), GPU
    workers at the first $GPU_WORKER_IPS address (.53), CPU workers after
    the GPUs; categories are $CAT_CP / $CAT_WRK / $CAT_GPU;
  - BMC IPs are $NET_OOB + 100 + n;
  - every address is bounds-checked: a range that collides with another,
    hits the gateway/head node, or runs past its subnet fails loudly — no
    row is written.
Skeleton MACs are RFC 7042 documentation addresses, 00:00:5e:00:53:01 to
00:00:5e:00:53:ff (so at most 255 rows per skeleton). --check (and
generation) REFUSES any row still carrying one, and also the pre-M6
skeleton pattern 00:50:56:b3:xx:xx (a VMware OUI that older skeletons
used): an unfilled skeleton never validates, whichever generation made it.

Validation performed: required fields present, no duplicate hostname /
IP / MAC, IP parses as IPv4, MAC looks like a MAC and is not a skeleton
placeholder, BMC IP present for gpu role.

The generated script is re-runnable (add-or-verify): a node or BMC
interface that already exists is a NOTE, and the set steps that follow
align it to the inventory.
"""

import argparse
import csv
import ipaddress
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from runbook import load_vars, override_requested  # noqa: E402  (one parser for the variables file)

COLUMNS = ["hostname", "ip", "mac", "role", "category", "interface", "bmc_ip"]
MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ROLES = {"ctrl", "worker", "gpu"}
# RFC 7042 sec. 2.1.2: 00-00-5E-00-53-00..FF is reserved for documentation —
# it can never belong to a real NIC.
SKELETON_MAC_PREFIX = "00:00:5e:00:53:"
SKELETON_MAC_MAX = 255
# Refused as well: the pattern skeletons used before M6 (a VMware OUI).
LEGACY_SKELETON_MAC_PREFIXES = ("00:50:56:b3:",)
SKELETON_MAC_PREFIXES = (SKELETON_MAC_PREFIX,) + LEGACY_SKELETON_MAC_PREFIXES
TRACK_DIR = os.path.dirname(HERE)
TEMPLATE_VARS = os.path.join(TRACK_DIR, "00-variables.sh")
SITE_VARS = os.path.join(TRACK_DIR, "site-vars.sh")
BMC_OFFSET = 100


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            fail(f"expected header exactly: {','.join(COLUMNS)}\n"
                 f"got: {','.join(reader.fieldnames or [])}")
        rows = [dict(r) for r in reader if any((r or {}).values())]
    return rows


def check(rows):
    errors = []
    seen_host, seen_ip, seen_mac = set(), set(), set()
    skeleton_lines = []
    for i, r in enumerate(rows, start=2):  # 1-based + header
        tag = f"line {i} ({r.get('hostname', '?')})"
        for c in COLUMNS:
            if c != "bmc_ip" and not (r.get(c) or "").strip():
                errors.append(f"{tag}: missing required field '{c}'")
        host = (r.get("hostname") or "").strip()
        ip = (r.get("ip") or "").strip()
        mac = (r.get("mac") or "").strip().lower()
        role = (r.get("role") or "").strip()
        cat = (r.get("category") or "").strip()
        iface = (r.get("interface") or "").strip()
        for field, val in (("hostname", host), ("category", cat),
                           ("interface", iface)):
            if val and not SAFE_NAME_RE.match(val):
                errors.append(f"{tag}: {field} '{val}' has characters "
                              "outside [A-Za-z0-9._-] \u2014 it is "
                              "interpolated verbatim into the generated "
                              "shell/cmsh script and must be a single "
                              "safe token")
        if host in seen_host:
            errors.append(f"{tag}: duplicate hostname '{host}'")
        seen_host.add(host)
        if ip in seen_ip:
            errors.append(f"{tag}: duplicate IP '{ip}'")
        seen_ip.add(ip)
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            errors.append(f"{tag}: invalid IPv4 '{ip}'")
        if mac in seen_mac:
            errors.append(f"{tag}: duplicate MAC '{mac}'")
        seen_mac.add(mac)
        if not MAC_RE.match(mac):
            errors.append(f"{tag}: malformed MAC '{mac}' (want 00:11:22:33:44:55)")
        elif mac.startswith(SKELETON_MAC_PREFIXES):
            skeleton_lines.append(i)
        if role not in ROLES:
            errors.append(f"{tag}: role must be one of {sorted(ROLES)}, got '{role}'")
        if role == "gpu" and not (r.get("bmc_ip") or "").strip():
            errors.append(f"{tag}: gpu role requires bmc_ip")
        bmc = (r.get("bmc_ip") or "").strip()
        if bmc:
            try:
                ipaddress.IPv4Address(bmc)
            except ValueError:
                errors.append(f"{tag}: invalid BMC IPv4 '{bmc}'")
    if skeleton_lines:
        shown = ", ".join(str(n) for n in skeleton_lines[:8])
        more = f" (+{len(skeleton_lines) - 8} more)" if len(skeleton_lines) > 8 else ""
        errors.insert(0, f"SKELETON: {len(skeleton_lines)} row(s) still carry the "
                         f"--skeleton placeholder MAC pattern "
                         f"({SKELETON_MAC_PREFIX}xx, or the pre-M6 00:50:56:b3:xx:xx) "
                         f"(lines {shown}{more}) — this is an unfilled skeleton; "
                         f"replace every MAC (and IP/BMC IP) with the node's real values")
    return errors


def gate_lines(categories, gate_reason=None, devices=(), vars_path=None):
    """The 03.4 fleet gate as shell: runbook.py layoutgate for every
    category in the inventory, and every device's device-level layout
    (if it has one), before the first add. Fails closed if runbook.py
    can't be found (set RUNBOOK to its path). --vars locates
    disk-layouts/OVERRIDES.log for an override's durable record."""
    runbook = os.path.join(HERE, "runbook.py")
    vars_path = os.path.abspath(vars_path or SITE_VARS)
    cats = " ".join(f"--category {shlex.quote(c)}" for c in categories)
    cats += "".join(f" --device {shlex.quote(d)}" for d in devices)
    override = ""
    if gate_reason:
        override = f" --allow-non-by-path --reason {shlex.quote(gate_reason)}"
    return [
        "# --- 03.4 fleet gate (H1): no node is added unless every category in",
        "# this inventory, and every device-level layout an existing node",
        "# carries, is a verified by-path disk layout. Refuses (and this",
        "# script stops) on a kernel path, a by-id/uuid alias, or a category",
        "# with no layout set.",
        f'RUNBOOK="${{RUNBOOK:-{runbook}}}"',
        f'GATE_VARS="${{GATE_VARS:-{vars_path}}}"',
        'if [ ! -f "$RUNBOOK" ]; then',
        '    echo "REFUSED: runbook.py not found at $RUNBOOK — the 03.4 layout gate cannot run;" >&2',
        '    echo "         set RUNBOOK to the path of bcm/scripts/runbook.py on this head node" >&2',
        "    exit 1",
        "fi",
        f'python3 "$RUNBOOK" --vars "$GATE_VARS" layoutgate {cats}{override}',
        "",
    ]


def generate(rows, gate_reason=None, vars_path=None):
    out = []
    out.append("#!/bin/bash")
    out.append("# Generated by generate-nodes.py — REVIEW BEFORE RUNNING on the BCM head node.")
    out.append("# Re-runnable (add-or-verify): an existing node or BMC interface is a NOTE;")
    out.append("# the set steps that follow align it to the inventory.")
    out.append(f"# Nodes: {len(rows)}")
    out.append("set -euo pipefail")
    out.append("")
    out.extend(gate_lines(sorted({r["category"].strip() for r in rows}), gate_reason,
                          devices=sorted({r["hostname"].strip() for r in rows}),
                          vars_path=vars_path))
    out.append('existing="$(cmsh -c "device; list")"')
    out.append("have_device() {  # hostname appears as a field of `device list`")
    out.append("    printf '%s\\n' \"$existing\" | grep -Eq \"(^|[[:space:]])$1([[:space:]]|\\$)\"")
    out.append("}")
    out.append("")
    for r in rows:
        h, ip = r["hostname"].strip(), r["ip"].strip()
        mac = r["mac"].strip().lower()
        cat, iface = r["category"].strip(), r["interface"].strip()
        bmc = (r.get("bmc_ip") or "").strip()
        out.append(f'echo "==> {h} ({ip})"')
        out.append(f'if have_device {h}; then')
        out.append(f'    echo "NOTE: {h} already exists — aligning, not re-adding"')
        out.append("else")
        # combined one-liner: add + provisioning interface + category
        out.append(f'    cmsh -c "device ; add physicalnode {h} {ip} {iface} ; '
                   f'set provisioninginterface {iface} ; set category {cat} ; commit"')
        out.append("fi")
        out.append(f'cmsh -c "device ; use {h} ; set provisioninginterface {iface} ; '
                   f'set category {cat} ; commit"')
        out.append(f'cmsh -c "device ; use {h} ; interfaces ; use {iface} ; '
                   f'set dhcp yes ; commit"')
        out.append(f'cmsh -c "device ; use {h} ; set mac {mac} ; commit"')
        if bmc:
            out.append(f'if cmsh -c "device ; use {h} ; interfaces ; list" | grep -qw ipmi0; then')
            out.append(f'    echo "NOTE: {h} ipmi0 already exists — aligning"')
            out.append(f'    cmsh -c "device ; use {h} ; interfaces ; use ipmi0 ; '
                       f'set ip {bmc} ; set network mgmtnet ; commit"')
            out.append("else")
            out.append(f'    cmsh -c "device ; use {h} ; interfaces ; '
                       f'add bmc ipmi0 {bmc} mgmtnet ; commit"')
            out.append("fi")
        out.append("")
    out.append('echo "All nodes provisioned. Verify: cmsh -c \'device list\'"')
    return "\n".join(out)


def _name_form(names, fallback):
    """('gpu-worker-', 0) from 'gpu-worker-1 ...'; pad width if zero-padded."""
    first = (names or "").split()
    m = re.match(r"^(.*?)(\d+)$", first[0]) if first else None
    if not m:
        return fallback, 0
    digits = m.group(2)
    return m.group(1), (len(digits) if digits.startswith("0") and len(digits) > 1 else 0)


def _first_ip(ips, var):
    try:
        return ipaddress.IPv4Address((ips or "").split()[0])
    except (IndexError, ValueError):
        fail(f"{var} in the variables file has no valid first address")


def skeleton(ctrl, workers, gpus, vars_):
    """Emit a fill-in CSV. MACs/IPs are placeholders — replace with real values.
    Every address is bounds-checked before anything is written."""
    try:
        prov = ipaddress.ip_network(vars_["NET_PROVISION"], strict=True)
        oob = ipaddress.ip_network(vars_["NET_OOB"], strict=True)
    except (KeyError, ValueError) as e:
        fail(f"NET_PROVISION / NET_OOB missing or invalid in the variables file: {e}")
    ctrl_base = _first_ip(vars_.get("K8S_CTRL_IPS"), "K8S_CTRL_IPS")
    gpu_base = _first_ip(vars_.get("GPU_WORKER_IPS"), "GPU_WORKER_IPS")
    reserved = {}
    for var in ("GW_PROVISION", "BCM_HEADNODE_IP", "GW_OOB"):
        try:
            reserved[ipaddress.IPv4Address(vars_.get(var, ""))] = var
        except ValueError:
            pass
    cform, cpad = _name_form(vars_.get("K8S_CTRL"), "k8s-ctrl-")
    gform, gpad = _name_form(vars_.get("GPU_WORKERS"), "gpu-worker-")
    cat_cp = vars_.get("CAT_CP", "runai-cp-cat")
    cat_wrk = vars_.get("CAT_WRK", "runai-wrk-cat")
    cat_gpu = vars_.get("CAT_GPU", "runai-dgx-cat")

    errors, used = [], {}

    def addr(base, n, net, what):
        a = base + n
        if a not in net or a in (net.network_address, net.broadcast_address):
            errors.append(f"{what}: {a} is outside the usable range of {net} — "
                          f"range overflows its subnet")
        elif a in reserved:
            errors.append(f"{what}: {a} collides with {reserved[a]}")
        elif a in used:
            errors.append(f"{what}: {a} collides with {used[a]}")
        used[a] = what
        return str(a)

    if ctrl and ctrl_base + ctrl > gpu_base and gpus:
        errors.append(f"{ctrl} control-plane nodes from {ctrl_base} run into the GPU "
                      f"range starting at {gpu_base} (section 01) — adjust "
                      f"K8S_CTRL_IPS / GPU_WORKER_IPS")
    rows = []
    mac_n = 0

    total = ctrl + gpus + workers
    if total > SKELETON_MAC_MAX:
        errors.append(f"{total} rows need {total} skeleton MACs, but the RFC 7042 "
                      f"documentation range holds {SKELETON_MAC_MAX} "
                      f"({SKELETON_MAC_PREFIX}01-ff) — generate the skeleton in parts")

    def mac():
        nonlocal mac_n
        mac_n += 1
        return SKELETON_MAC_PREFIX + "%02x" % (mac_n & 0xFF)

    for i in range(ctrl):
        h = f"{cform}{i + 1:0{cpad}d}" if cpad else f"{cform}{i + 1}"
        rows.append([h, addr(ctrl_base, i, prov, h), mac(), "ctrl", cat_cp, "ens33", ""])
    for i in range(gpus):
        h = f"{gform}{i + 1:0{gpad}d}" if gpad else f"{gform}{i + 1}"
        rows.append([h, addr(gpu_base, i, prov, h), mac(), "gpu", cat_gpu,
                     "enp225s0f0np0",
                     addr(oob.network_address, BMC_OFFSET + i + 1, oob, f"{h} BMC")])
    for i in range(workers):
        h = f"k8s-worker-{i + 1}"
        rows.append([h, addr(gpu_base, gpus + i, prov, h), mac(), "worker", cat_wrk,
                     "ens33", ""])
    if errors:
        print(f"{len(errors)} problem(s) — no skeleton written:", file=sys.stderr)
        for e in errors[:20]:
            print(f"  - {e}", file=sys.stderr)
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more", file=sys.stderr)
        sys.exit(1)
    w = csv.writer(sys.stdout)
    w.writerow(COLUMNS)
    w.writerows(rows)
    print(f"# skeleton: {ctrl} ctrl + {gpus} gpus + {workers} workers — "
          f"REPLACE every placeholder MAC ({SKELETON_MAC_PREFIX}xx) and check every "
          f"IP; --check refuses the skeleton until you do", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Generate BCM cmsh node-provisioning from CSV.")
    ap.add_argument("csv", nargs="?", help="inventory CSV file")
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--skeleton", action="store_true", help="emit fill-in CSV")
    ap.add_argument("--vars", default=None,
                    help="site variables file (default: ../site-vars.sh; the "
                         "00-variables.sh template itself is refused)")
    ap.add_argument("--ctrl", type=int, default=3)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--gpus", type=int, default=0)
    ap.add_argument("--allow-non-by-path", dest="allow_non_by_path", action="store_true",
                    help="the logged override (D3): the generated gate accepts "
                         "non-by-path layouts (needs --reason; warns loudly)")
    ap.add_argument("--reason", default=None,
                    help="why by-path fails in the installer on this host type")
    a = ap.parse_args()
    gate_reason = override_requested(a)

    if a.skeleton:
        vars_path = a.vars or SITE_VARS
        if not os.path.isfile(vars_path):
            print(f"REFUSED: site variables file not found: {vars_path}\n"
                  f"         cp {TEMPLATE_VARS} {SITE_VARS}   # then fill it in, "
                  f"or pass --vars FILE", file=sys.stderr)
            sys.exit(2)
        if os.path.samefile(vars_path, TEMPLATE_VARS):
            print(f"REFUSED: {vars_path} is the pristine template — the skeleton "
                  f"would follow the lab defaults. Copy it to {SITE_VARS}, fill it "
                  f"in, and pass that.", file=sys.stderr)
            sys.exit(2)
        skeleton(a.ctrl, a.workers, a.gpus, load_vars(vars_path))
        return
    if not a.csv:
        ap.error("provide a CSV file (or --skeleton)")
    rows = load(a.csv)
    errors = check(rows)
    if errors:
        print(f"{len(errors)} problem(s) in {a.csv}:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {len(rows)} nodes validated ({a.csv})", file=sys.stderr)
    if not a.check:
        print(generate(rows, gate_reason=gate_reason, vars_path=a.vars))


if __name__ == "__main__":
    main()
