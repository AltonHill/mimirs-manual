# 02 — Network configuration (Proxmox side)

**Goal:** identical, verified host networking on all three nodes: an active-backup
bond across the two 200G BlueField-3 ports carrying the Ceph public (VLAN 100),
Ceph cluster (VLAN 200) and **PBS backup (VLAN `PBS_VLAN`)** networks at MTU
9000, plus the 1GbE management bridge at MTU 1500. The fabric built here is
what **corosync link1** (01 §7, REQUIRED) rides.

**Prerequisite:** 01 §1–§6 signed off (cluster quorate on link0).

Blocks follow the track's paste contract (track README, "Working-directory
contract"): `CERBERUS_TRACK` set on the node, each state block one
fail-closed `( set -euo pipefail … )` unit.

Reference: [docs/references.md](../../docs/references.md) — Proxmox VE Admin Guide
(network chapter), Cluster Manager chapter (corosync redundancy/second link).

> `/etc/network/interfaces` is **node-local** (not replicated by pmxcfs). Every
> step below runs per node, with that node's IP set.

---

## 1. Interface discovery — resolve `BF3_P1` / `BF3_P2` / `MGMT_IF`

Kernel interface names are host-specific. Map PCI address → kernel name → driver
on **each** node:

```bash
for f in /sys/class/net/*; do
  i=${f##*/}; [ "$i" = lo ] && continue
  drv=$(basename "$(readlink "$f/device/driver")" 2>/dev/null || echo '?')
  pci=$(basename "$(readlink -f "$f/device")" 2>/dev/null || echo '?')
  printf '%-18s %-14s %s\n' "$i" "$drv" "$pci"
done
```

- The two BlueField-3 200G ports show driver `mlx5_core`. Record their kernel
  names (e.g. `enp134s0f0np0`, `enp134s0f1np0`).
- The Broadcom 5719 OCP 1GbE ports show `tg3` (or `bnx2x`). Pick one for
  management and record its kernel name.

Then identify **which physical port is which** — MANUAL-ONLY, one node at a
time. This blinks every candidate port in turn (15 s each) — watch the rack
and note which name lights which port:

```bash
for f in /sys/class/net/*; do
  i=${f##*/}
  case "$(basename "$(readlink "$f/device/driver")" 2>/dev/null)" in
    mlx5_core|tg3|bnx2x) echo "blinking $i (15 s)"; ethtool -p "$i" 15 ;;
  esac
done
```

Export what you found for **this** node (example names — use yours):

```bash
export NODE_NUM=1 BF3_P1=enp134s0f0np0 BF3_P2=enp134s0f1np0 MGMT_IF=enp1s0f0   # paste: session
```

Convention: **P1 → sn5610-a, P2 → sn5610-b** on every node (matches the cable
plan in 00a §6).

Now verify the names and record them node-locally in `/root/cerberus-node.env`
— every later block sources that file, so a second SSH session, a reconnect or
the XCC console has them too. This is also where the host-side link checks
that 00a cannot run (no names before this point) happen:

```bash
( set -euo pipefail
  : "${NODE_NUM:?export NODE_NUM=1|2|3|4 first (block above)}"
  case "$NODE_NUM" in 1|2|3|4) ;; *) echo "FATAL: NODE_NUM=$NODE_NUM" >&2; exit 1 ;; esac
  for v in BF3_P1 BF3_P2 MGMT_IF; do
    i="${!v:?export $v first (block above)}"
    [ -e "/sys/class/net/$i" ] || { echo "FATAL: $v=$i is not an interface on this node" >&2; exit 1; }
  done
  drv() { basename "$(readlink "/sys/class/net/$1/device/driver")"; }
  [ "$(drv "$BF3_P1")" = mlx5_core ] && [ "$(drv "$BF3_P2")" = mlx5_core ] \
    || { echo "FATAL: BF3_P1/BF3_P2 are not mlx5_core (BlueField-3) ports" >&2; exit 1; }
  [ "$BF3_P1" != "$BF3_P2" ] || { echo "FATAL: BF3_P1 and BF3_P2 are the same port" >&2; exit 1; }
  # Host-side link checks (moved here from 00a §5/§8 — they need these names):
  for i in "$BF3_P1" "$BF3_P2"; do
    echo "== $i =="; ethtool "$i" | grep -E 'Speed|Duplex|Link detected'   # PASS: 200000Mb/s, Full, yes
  done
  echo "== $MGMT_IF =="; ethtool "$MGMT_IF" | grep -E 'Speed|Link detected'  # PASS: 1000Mb/s, yes
  # Record node-locally (temp file, then move: never a half-written env file)
  tmp="$(mktemp /root/cerberus-node.env.XXXXXX)"
  printf 'export NODE_NUM=%s BF3_P1=%s BF3_P2=%s MGMT_IF=%s\n' \
    "$NODE_NUM" "$BF3_P1" "$BF3_P2" "$MGMT_IF" > "$tmp"
  chmod 0644 "$tmp"
  mv "$tmp" /root/cerberus-node.env
  echo "recorded: $(cat /root/cerberus-node.env)"
)
```

If a port shows anything but 200000Mb/s / 1000Mb/s with link detected: STOP —
cabling, DAC seating or BF3 mode (00a §5–§7) — before §4.

---

## 2. Bond design decision

**Recommendation: `bond0`, mode `active-backup`, slaves `$BF3_P1` (primary) +
`$BF3_P2`, miimon 100.**

Justification:

- **Simplicity and switch-failure survival.** One bond, no MLAG/peer-link config
  on the SN5610 pair, no LACP negotiation to debug. If a switch, DAC, or BF3 port
  dies, traffic fails over to the surviving path. With 2 OSDs per node, a single
  200G path is far more bandwidth than Ceph can saturate here.
- **Matches the cabling rule** (P1→switch A, P2→switch B from 00a §6): the two
  slaves are on physically independent switches, so active-backup genuinely
  survives a switch failure.
- **Corosync:** link0 stays on the independent 1GbE management NIC (01 §4/§5);
  **link1 — required — rides this bond** on the Ceph public VLAN (01 §7). The
  two links share no NIC, cable or switch. Proxmox warns that a corosync
  setup whose *only* link sits on a bond can be fragile in some failure modes;
  here the bond carries the second link, never the only one.

Trade-off (accepted): a single flow is capped at 200G — no 400G aggregation.

**Alternative (not configured here): LACP + MLAG.** If a future workload needs
>200G per node, configure 802.3ad LACP on the bond with an MLAG peer-link between
the two SN5610s. That buys ~400G aggregate and flow-level distribution at the
cost of MLAG config, peer-link fate-sharing, and harder troubleshooting. Revisit
only with measured demand.

---

## 3. VLAN and bridge layout

> **Flat-layout sites:** if the site's IP plan is one flat mgmt /24 plus
> one data /24 per role, the Ceph public network is the service VLAN
> itself (existing bond IPs), and the Ceph cluster network is one added
> VLAN — or `+100` secondary IPs as a fallback. See
> [docs/site-profile-flat.md](../../docs/site-profile-flat.md) before
> renumbering anything below.

| Interface | Ports | VLAN | Node IP (template, node-1) | MTU | Purpose |
|---|---|---|---|---|---|
| `vmbr0` | `$MGMT_IF` | untagged | 10.10.10.11, gw 10.10.10.1 | 1500 | Proxmox mgmt, **corosync link0**, SSH |
| `vmbr1` | `bond0.100` | 100 (tagged on the wire) | 10.20.20.11, no gw | 9000 | Ceph public, untagged guests, **corosync link1** |
| `vmbr2` | `bond0.200` | 200 (tagged on the wire) | 10.20.30.11, no gw | 9000 | Ceph cluster (replication/recovery) |
| `bond0.300` | (VLAN sub-if, no bridge) | `PBS_VLAN` (tagged on the wire) | 10.20.40.11, no gw | 9000 | PBS backup/restore traffic only |

Prefix lengths are **derived from the site file's CIDRs** (`MGMT_NET`,
`CEPH_PUBLIC_NET`, `CEPH_CLUSTER_NET`, `PBS_NET`) by the §4 generator — a /23
or /25 site gets the right mask; nothing is hard-coded to /24.

- **Guests on `vmbr1` are untagged** and land in VLAN 100 (the bridge's only
  port is the VLAN sub-interface `bond0.100`). `vmbr1` is deliberately **not**
  VLAN-aware: a VLAN-aware bridge on top of a VLAN sub-interface would put a
  guest's tag *inside* VLAN 100 (QinQ), so the switch would never see it as
  VLAN X — the old "tagged VLANs on the same bridge" claim did not work.
- **Tagged guests belong on the bond or a trunk bridge.** If the site needs
  guest VLANs, the supported shape (not configured here — a design change,
  re-verify §5 after it) is one VLAN-aware bridge whose port is `bond0` itself
  (`bridge-vlan-aware yes`, `bridge-vids` = the guest VLANs), with the host's
  own addresses on VLAN interfaces of that bridge (`vmbr1.100`, `vmbr1.200`,
  `vmbr1.300`) instead of `bond0.N` sub-interfaces; the switch legs must trunk
  the guest VLANs too.
- `vmbr2` is not VLAN-aware (Ceph cluster traffic only); `bond0.300` has no
  bridge — no guest ever needs the backup VLAN.
- **Switch side:** the SN5610 breakout legs facing the nodes trunk VLANs 100,
  200 and `PBS_VLAN` (tagged), and the PBS host's ports carry `PBS_VLAN`
  untagged — configured in 00a §7a; verify with `nv show interface`.

### 3b. Netris OOB VLAN (data side)

The Netris controller nodes run as **VMs on the Proxmox hosts**. They reach
the data-side OOB network over a dedicated VLAN on the 1GbE path:

- Proxmox vSwitch (Linux bridge) on the dedicated 1Gb vLinks carries the
  OOB VLAN; each Netris VM gets a **dedicated vNIC** on that VLAN.
- This is the **data-side** OOB path — it is **separate from XCC true OOB**
  (BMC), which stays on its own physical path. The architecture diagram
  shows them as two different lines for a reason: two failure domains.
- **VLAN ID and subnet TBD** — record them here when assigned; the Netris
  VM vNICs and any IP plan for this VLAN follow the site's addressing.
- **Bootstrap order matters:** physical mgmt links → Proxmox cluster →
  Netris VMs → Netris-managed fabric. Netris automates the fabric, but the
  Netris VMs themselves depend on Proxmox being up — never assume the
  fabric is automated before the VMs that automate it exist.
- **Two switches stay permanently outside Netris management** (record
  which two in the site copy). They are the cold-start escape hatch: a
  path to the fabric that never depends on Netris being alive.

---

## 4. Generate and apply `/etc/network/interfaces` (per node)

Run on **each** node, after §1 has written `/root/cerberus-node.env`. All
IPs/MTUs/VLANs come from the site file; the interface names and `NODE_NUM`
from the node env file.

The generator is **fail-closed**: every guard runs before anything is
written; the new config is written to a temporary file beside the live one,
**verified** (the same blank-interface scan `scripts/check.sh` applies to the
rendered guide, plus "every expected interface named, no unexpanded `$`"),
and only then moved into place. A failure at any point leaves
`/etc/network/interfaces` exactly as it was.

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  source /root/cerberus-node.env      # NODE_NUM, BF3_P1, BF3_P2, MGMT_IF — written in §1
  # cerberus-generate-interfaces
  : "${NODE_NUM:?NODE_NUM missing from /root/cerberus-node.env — redo §1}"
  case "$NODE_NUM" in 1|2|3|4) ;; *) echo "FATAL: NODE_NUM=$NODE_NUM" >&2; exit 1 ;; esac
  cerberus_require_vars BF3_P1 BF3_P2 MGMT_IF MGMT_NET MGMT_GW MGMT_MTU \
    CEPH_PUBLIC_NET CEPH_PUBLIC_VLAN CEPH_PUBLIC_MTU \
    CEPH_CLUSTER_NET CEPH_CLUSTER_VLAN CEPH_CLUSTER_MTU \
    PBS_NET PBS_VLAN PBS_MTU \
    "NODE${NODE_NUM}_MGMT_IP" "NODE${NODE_NUM}_PUBLIC_IP" \
    "NODE${NODE_NUM}_CLUSTER_IP" "NODE${NODE_NUM}_PBS_IP"
  v="NODE${NODE_NUM}_MGMT_IP";    MGMT_IP="${!v}"
  v="NODE${NODE_NUM}_PUBLIC_IP";  PUBLIC_IP="${!v}"
  v="NODE${NODE_NUM}_CLUSTER_IP"; CLUSTER_IP="${!v}"
  v="NODE${NODE_NUM}_PBS_IP";     PBS_IP="${!v}"
  # Prefix lengths from the site CIDRs (never a hard-coded /24)
  prefix() { local p="${1#*/}"; [[ "$1" == */* && "$p" =~ ^[0-9]+$ && "$p" -ge 8 && "$p" -le 30 ]] \
               || { echo "FATAL: '$1' is not an IPv4 CIDR with a /8–/30 prefix" >&2; return 1; }; echo "$p"; }
  MGMT_PREFIX="$(prefix "$MGMT_NET")"
  PUBLIC_PREFIX="$(prefix "$CEPH_PUBLIC_NET")"
  CLUSTER_PREFIX="$(prefix "$CEPH_CLUSTER_NET")"
  PBS_PREFIX="$(prefix "$PBS_NET")"
  TARGET="${INTERFACES_FILE:-/etc/network/interfaces}"
  TMP="$(mktemp "$TARGET.cerberus.XXXXXX")"   # same directory: the final mv is atomic
  trap 'rm -f "$TMP"' EXIT
  cat > "$TMP" <<EOF
# Cerberus node-${NODE_NUM} — generated from site-vars.sh. Do not hand-edit;
# re-run the generator in runbook/02-network instead.
auto lo
iface lo inet loopback

# --- 1GbE management (OCP) ---
auto ${MGMT_IF}
iface ${MGMT_IF} inet manual
    mtu ${MGMT_MTU}

# --- 200G fabric ports (BlueField-3, NIC mode) ---
auto ${BF3_P1}
iface ${BF3_P1} inet manual
    mtu ${CEPH_PUBLIC_MTU}

auto ${BF3_P2}
iface ${BF3_P2} inet manual
    mtu ${CEPH_PUBLIC_MTU}

# --- bond0: active-backup across the two 200G ports ---
auto bond0
iface bond0 inet manual
    bond-slaves ${BF3_P1} ${BF3_P2}
    bond-miimon 100
    bond-mode active-backup
    bond-primary ${BF3_P1}
    mtu ${CEPH_PUBLIC_MTU}

# --- VLAN sub-interfaces on the bond ---
auto bond0.${CEPH_PUBLIC_VLAN}
iface bond0.${CEPH_PUBLIC_VLAN} inet manual
    mtu ${CEPH_PUBLIC_MTU}

auto bond0.${CEPH_CLUSTER_VLAN}
iface bond0.${CEPH_CLUSTER_VLAN} inet manual
    mtu ${CEPH_CLUSTER_MTU}

# --- PBS backup VLAN (host traffic only, no bridge) ---
auto bond0.${PBS_VLAN}
iface bond0.${PBS_VLAN} inet static
    address ${PBS_IP}/${PBS_PREFIX}
    mtu ${PBS_MTU}

# --- bridges ---
auto vmbr0
iface vmbr0 inet static
    address ${MGMT_IP}/${MGMT_PREFIX}
    gateway ${MGMT_GW}
    bridge-ports ${MGMT_IF}
    bridge-stp off
    bridge-fd 0
    mtu ${MGMT_MTU}

# vmbr1: Ceph public + untagged guests + corosync link1. NOT VLAN-aware (02 §3).
auto vmbr1
iface vmbr1 inet static
    address ${PUBLIC_IP}/${PUBLIC_PREFIX}
    bridge-ports bond0.${CEPH_PUBLIC_VLAN}
    bridge-stp off
    bridge-fd 0
    mtu ${CEPH_PUBLIC_MTU}

auto vmbr2
iface vmbr2 inet static
    address ${CLUSTER_IP}/${CLUSTER_PREFIX}
    bridge-ports bond0.${CEPH_CLUSTER_VLAN}
    bridge-stp off
    bridge-fd 0
    mtu ${CEPH_CLUSTER_MTU}
EOF
  # VERIFY before anything touches the live file: no blank interface
  # positions (the check.sh scan), every expected name present, nothing
  # unexpanded.
  if grep -nE '^[[:space:]]*(bridge-ports|bond-slaves|bond-primary)[[:space:]]*$|^[[:space:]]*iface[[:space:]]+inet6?([[:space:]]|$)|^[[:space:]]*auto[[:space:]]*$' "$TMP"; then
    echo "FATAL: generated config has blank interface positions (above) — live file untouched" >&2; exit 1
  fi
  if grep -n '\$' "$TMP"; then
    echo "FATAL: generated config has unexpanded \$ references (above) — live file untouched" >&2; exit 1
  fi
  for want in "auto $MGMT_IF" "auto $BF3_P1" "auto $BF3_P2" "bond-slaves $BF3_P1 $BF3_P2" \
              "bridge-ports $MGMT_IF" "address $MGMT_IP/$MGMT_PREFIX" "address $PBS_IP/$PBS_PREFIX"; do
    grep -qF -- "$want" "$TMP" || { echo "FATAL: generated config lacks '$want' — live file untouched" >&2; exit 1; }
  done
  # Only now: back up, show the diff, move into place.
  if [ -e "$TARGET" ]; then
    cp -a "$TARGET" "${BACKUP_DIR:-/root}/interfaces.bak.$(date +%F-%H%M%S)"
    diff -u "$TARGET" "$TMP" || true
  fi
  chmod 0644 "$TMP"
  mv "$TMP" "$TARGET"
  trap - EXIT
  echo "--- installed $TARGET for node-${NODE_NUM} ---"
  grep -E '^(auto|iface|    address|    gateway|    bridge-ports|    bond-)' "$TARGET"
)
```

(`INTERFACES_FILE` / `BACKUP_DIR` exist only so the generator can be
rehearsed against a scratch path; on a node, leave them unset.)

Apply:

```bash
ifreload -a
```

> **MANUAL-ONLY caution:** `ifreload -a` rewrites the management path you are
> SSH'd over. If the generated `vmbr0` stanza differs from the running config
> (it shouldn't — same IP — but read the diff the generator printed), apply
> from the XCC virtual console instead of SSH. Keep the `/root/interfaces.bak.*`
> copy until §5 verification passes.

---

## 5. Verification (per node, then cross-node)

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  source /root/cerberus-node.env
  cerberus_require_vars BF3_P1 BF3_P2 MGMT_IF NODE_NUM
  # Local: links, addresses, bond state
  ip -br link
  ip -br addr
  cat /proc/net/bonding/bond0
  # PASS: "Bonding Mode: fault-tolerance (active-backup)", "MII Status: up",
  #       "Currently Active Slave:" = BF3_P1
  grep -q "Currently Active Slave: $BF3_P1" /proc/net/bonding/bond0 \
    || echo "CHECK: active slave is not $BF3_P1"
  # Negotiated speeds
  ethtool "$BF3_P1" | grep -E 'Speed|Link detected'   # 200000Mb/s
  ethtool "$BF3_P2" | grep -E 'Speed|Link detected'   # 200000Mb/s
  ethtool "$MGMT_IF" | grep -E 'Speed|Link detected'  # 1000Mb/s
  # MTUs
  ip link show bond0 | grep -o 'mtu [0-9]*'           # 9000
  ip link show vmbr1 | grep -o 'mtu [0-9]*'           # 9000
  ip link show vmbr2 | grep -o 'mtu [0-9]*'           # 9000
  ip link show "bond0.$PBS_VLAN" | grep -o 'mtu [0-9]*'   # PBS_MTU
  ip link show vmbr0 | grep -o 'mtu [0-9]*'           # 1500
  # Cross-node reachability + end-to-end MTU on every network, to every
  # other node (8972 = 9000 - 20 IP - 8 ICMP)
  for n in 1 2 3; do
    [ "$n" = "$NODE_NUM" ] && continue
    for net in MGMT PUBLIC CLUSTER PBS; do
      v="NODE${n}_${net}_IP"; ip="${!v}"
      ping -c 3 -W 2 "$ip" >/dev/null && echo "PASS ping node-$n $net $ip" || echo "FAIL ping node-$n $net $ip"
    done
    for net in PUBLIC CLUSTER PBS; do
      v="NODE${n}_${net}_IP"; ip="${!v}"
      ping -M do -s 8972 -c 4 -W 2 "$ip" >/dev/null && echo "PASS mtu9000 node-$n $net $ip" || echo "FAIL mtu9000 node-$n $net $ip"
    done
  done
  # Corosync still healthy after the network rework
  corosync-cfgtool -s
  pvecm status   # still 3 votes, quorate
)
```

PASS: every line `PASS`, 0% loss on every pair and every VLAN. Then, from
one node, run `bash "$CERBERUS_TRACK/scripts/net-verify.sh"` — it adds the
corosync UDP probe on both link networks and the MTU check to the PBS host's
VLAN address (once the PBS host is on the VLAN, 06.1).

**Failover drill (do it now, while the build is young):** MANUAL-ONLY — pull
(or administratively down) the active 200G leg / switch-A DAC. Expect: the
bond fails over to `BF3_P2` (<30 s with miimon 100, typically ~1–3 s), pings
on vmbr1/vmbr2 resume, corosync unaffected.

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  source /root/cerberus-node.env
  cerberus_require_vars BF3_P1 BF3_P2 NODE_NUM
  peer=$(( NODE_NUM == 1 ? 2 : 1 )); v="NODE${peer}_CLUSTER_IP"; PEER_IP="${!v}"
  trap 'ip link set "$BF3_P1" up' EXIT          # the leg comes back even if a step fails
  ip link set "$BF3_P1" down                    # software equivalent of pulling the cable
  sleep 3
  grep 'Currently Active Slave' /proc/net/bonding/bond0   # expect BF3_P2
  ping -c 10 "$PEER_IP"
)
```

---

## 6. Corosync link1 — REQUIRED (the procedure is 01 §7)

link1 is mandatory: it rides `vmbr1` (the node's `NODE*_PUBLIC_IP`), over the
bond — the physically separate DAC path from the mgmt link. **Go to 01 §7
now** and add it by the copy → edit → verify → move procedure; come back for
§7 below. 03 refuses to start until `corosync-cfgtool -s` shows link 1.

Honest scope: link1 shares the fabric bond with Ceph traffic (VLAN-separated).
It protects quorum against a mgmt NIC, cable or switch failure; a fabric-wide
event still leaves link0. Together the two links share no NIC, cable or
switch.

---

## 7. Firewall note

If the Proxmox datacenter/host firewall is enabled, allow between the nodes:

- **UDP 5405–5412** (corosync) on **both** corosync networks: the mgmt network
  (link0) **and** the Ceph public network (link1)
- **TCP 3300** (Ceph MON, msgr2 — the default MON port since Nautilus),
  **TCP 6789** (Ceph MON, legacy msgr1) and **TCP 6800–7300** (Ceph OSD/MGR) on
  the Ceph public and Ceph cluster networks
- **TCP 8007** (PBS API/backup) from the nodes to the PBS host's VLAN address
  on the PBS network

Default Proxmox firewall policy with no rules can blackhole cluster traffic —
either leave the firewall off until §07 validation, or add the allows above first
(GUI: Datacenter → Firewall, or per-node Host → Firewall).

---

## Node-4 callout

Repeat §1 (discover its interface names, `NODE_NUM=4`) and §4 (`.14`
addresses from `site-vars.sh`). Its P1/P2 follow the same switch-A/switch-B
rule (leg 4 of each DAC); the §5 checks and failover drill apply unchanged;
its link1 is added per the 01 Node-4 callout.

## 02 acceptance checklist

- [ ] `BF3_P1`/`BF3_P2`/`MGMT_IF` resolved, verified (driver, 200G/1G link) and recorded in `/root/cerberus-node.env` per node (§1)
- [ ] SN5610 breakout legs trunk VLANs 100 + 200 + `PBS_VLAN` tagged (00a §7a, §3)
- [ ] `/etc/network/interfaces` generated by the fail-closed generator on all nodes; backup kept (§4)
- [ ] `ifreload -a` applied; bond0 active-backup up, active slave = P1 (§5)
- [ ] 200000Mb/s on both BF3 ports, 1000Mb/s mgmt, MTUs 9000/9000/9000/1500 (§5)
- [ ] Cross-node ping clean on mgmt/public/cluster/PBS; MTU-9000 ping clean on all three fabric VLANs (§5)
- [ ] Failover drill: P1 down → traffic on P2, corosync unaffected (§5)
- [ ] **Corosync link1 added (01 §7) and both links up to every peer — REQUIRED (§6)**
- [ ] Firewall allows in place if firewall is enabled — incl. TCP 3300 and corosync UDP on both link networks (§7)
