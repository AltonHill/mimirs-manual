# 03 — Ceph cluster deployment

Deploy Ceph Tentacle on the three hyperconverged nodes: install, initialize, MON/MGR placement, and OSD creation on the PM1743 NVMe pair per node.

**Design targets:** PVE 9.2.x + Ceph Tentacle 20.2.4. Three MONs (one per node), colocated MGRs, 6 OSDs (2 per node), default replicated CRUSH rule against the `host` failure domain.

> **Scope note — no CephFS in this design.** All VM storage is RBD images; that
> is the native Proxmox path (live migration, HA, and backups all ride on it).
> CephFS (MDS daemons, shared POSIX filesystem) is intentionally **not**
> deployed — nothing in the workload requires shared file storage, and the MDS
> tier would add RAM/CPU cost and operational surface for no benefit. If a
> future site needs CephFS, that is a design change with its own section — and
> that section is where an orphaned-subvolume cleanup audit belongs. Field
> lesson from another CephFS deployment (its write-up is not part of this
> library, so no figures are quoted here): CSI staging subvolumes can be
> stranded and go unnoticed when they are counted from a single cluster's
> PVs — audit from the Ceph side.

**Variables** — defined in the site file `site-vars.sh` at the track root (copied from the template [00-overview/variables.sh](../00-overview/variables.sh)); **do not redefine them here**. Names this section consumes: `CEPH_PUBLIC_NET` (template `10.20.20.0/24`), `CEPH_CLUSTER_NET` (template `10.20.30.0/24`), and the per-node OSD drives `NODE<n>_OSD1_BYID` / `NODE<n>_OSD2_BYID` (recorded in 00a §4). Each block sources the site file itself, through `CERBERUS_TRACK` (track README, "Working-directory contract"), as one fail-closed `( set -euo pipefail … )` unit.

All `pveceph` steps below run as `root`. Steps marked "each node" must be executed on every node (node-1, node-2, node-3).

---

## Prerequisites

- §01 cluster join and §02 network are complete: the Ceph public and cluster networks are up on every node, MTU 9000 verified end-to-end.
- **Corosync link1 is in place (01 §7 — REQUIRED).** This gate refuses to continue without it. The `corosync-cfgtool -s` wording it greps (`LINK ID 1`) is **[unverified]** on this release — read the real output at the first cluster and adjust the greps if needed (library verification checklist):

```bash
( set -euo pipefail
  out="$(corosync-cfgtool -s)"
  echo "$out"
  grep -q 'LINK ID 1' <<<"$out" \
    || { echo "STOP: corosync link1 is missing — do 01 §7 before Ceph" >&2; exit 1; }
  grep -Eq '^Quorate:[[:space:]]+Yes' <<<"$(pvecm status)" || { echo "STOP: cluster not quorate" >&2; exit 1; }
  echo "OK: link1 present, cluster quorate"
)
```

- §00a physical bring-up has **confirmed the PM1743s are wired direct-to-CPU (x4)** and do NOT appear in `storcli /c0/pall`. If any NVMe shows up under StorCLI, stop — recable before continuing.
- **Flat-layout sites:** the public network may be the pre-existing service/data VLAN (the nodes' bond IPs) and the cluster network one added VLAN — see [docs/site-profile-flat.md](../../docs/site-profile-flat.md). `pveceph init` takes both forms unchanged; what matters is that `CEPH_PUBLIC_NET` / `CEPH_CLUSTER_NET` in `site-vars.sh` describe what §02 actually built.

---

## Step 1 — Install Ceph Tentacle (each node)

```bash
pveceph install --version tentacle --repository no-subscription
```

- `tentacle` is explicit on purpose: **Squid reaches its estimated end of life on 2026-12-02 (checked 2026-11-17) — never pin Squid on a greenfield build.** (Verify at deploy time on the [Ceph active-releases table](https://docs.ceph.com/en/latest/releases/).)
- `--repository no-subscription` pulls from the public `download.proxmox.com` Ceph repo. For licensed builds use `--repository enterprise` (requires a Proxmox VE Ceph subscription key). Do not use `test` for production.
- Verify on each node afterwards: `ceph --version` — expect `20.2.x` Tentacle on all three. All nodes must run the same major release.

## Step 2 — Initialize the Ceph cluster (node-1 only)

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars CEPH_PUBLIC_NET CEPH_CLUSTER_NET
  pveceph init --network "${CEPH_PUBLIC_NET}" --cluster-network "${CEPH_CLUSTER_NET}"
)
```

- `--network` = Ceph public (client/VM I/O) network; `--cluster-network` = replication/recovery heartbeat network.
- Defaults baked into `pveceph init` already match this design: `size=3`, `min_size=2`. The flags `--size`/`--min_size` exist if a variant build ever needs different values.
- This writes `/etc/ceph/ceph.conf` and keyrings on node-1; they are distributed via `/etc/pve` (pmxcfs) to the other nodes automatically.

## Step 3 — Create the monitors (node-1, node-2, node-3)

On **node-1** first (the first MON auto-creates a manager):

```bash
pveceph mon create
```

Then on **node-2** and **node-3**:

```bash
pveceph mon create
```

- Defaults: `monid` = node name, address auto-detected on the public network. Override with `--monid`/`--mon-address` only if the node has multiple addresses on the public network.
- Verify quorum before continuing:

```bash
ceph -s        # look for: mon: 3 daemons, quorum node-1,node-2,node-3
ceph quorum_status
```

> **node-4 callout:** a fourth node gets **no new MON**. Three MONs is the quorum sweet spot; a fourth MON adds failure *risk* to quorum math with zero benefit. The optional 4th node runs OSDs and compute only.

## Step 4 — Create managers (node-2, node-3)

node-1 already has a manager from Step 3. On node-2 and node-3:

```bash
pveceph mgr create
```

Verify:

```bash
ceph -s | grep mgr   # expect: mgr: 3 daemons active
```

## Step 5 — Create the OSDs (on each node, for its own drives)

> **DESTRUCTIVE — this wipes the target device.** Only ever run against the `/dev/disk/by-id/` names of the two PM1743 NVMe drives on that node — recorded per node in the site file as `NODE<n>_OSD1_BYID` / `NODE<n>_OSD2_BYID` (00a §4). **by-id on purpose:** the name carries the drive's model and serial, and the block below parses that name and refuses to proceed unless the resolved device reports exactly that model and serial — the name alone proves nothing. (by-path, the BCM disk-layout standard, carries neither; Ceph OSDs sit on LVM, so name persistence is irrelevant to this one-time step.)

On **each node, for its own drives** — the block picks this node's two variables from `/root/cerberus-node.env` (02 §1), verifies each drive's reported model and serial against the recorded by-id name, and refuses anything that does not match, that is not a whole PM1743-class NVMe of ~3.84 TB, or that is not blank, and asks before each create:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  source /root/cerberus-node.env
  cerberus_require_vars NODE_NUM "NODE${NODE_NUM}_OSD1_BYID" "NODE${NODE_NUM}_OSD2_BYID"
  for k in 1 2; do
    v="NODE${NODE_NUM}_OSD${k}_BYID"; dev="${!v}"
    case "$dev" in /dev/disk/by-id/nvme-*) ;; *) echo "FATAL: $v=$dev is not a /dev/disk/by-id/nvme-* path" >&2; exit 1 ;; esac
    [ -e "$dev" ] || { echo "FATAL: $v=$dev does not exist on this node" >&2; exit 1; }
    real="$(readlink -f "$dev")"
    lsblk -dno NAME,MODEL,SERIAL,SIZE "$real"
    base="${dev##*/}"; base="${base#nvme-}"
    case "$base" in eui.*) echo "FATAL: $v=$dev is an eui by-id alias — record the model_serial name at 00a §4 so the target can be bound to its drive" >&2; exit 1 ;; esac
    case "$base" in *_1) base="${base%_1}" ;; esac
    exp_serial="${base##*_}"; exp_model="${base%_*}"
    model="$(lsblk -dno MODEL "$real" | tr -s ' ' | sed 's/^ //; s/ $//')"
    serial="$(lsblk -dno SERIAL "$real" | tr -d '[:space:]')"
    [ "$serial" = "$exp_serial" ] \
      || { echo "FATAL: $real reports serial '$serial', not the '$exp_serial' recorded in $v — wrong or swapped drive. NOT creating." >&2; exit 1; }
    [ "$(printf '%s' "$model" | tr '_' ' ' | tr -s ' ' | sed 's/^ //; s/ $//')" = "$(printf '%s' "$exp_model" | tr '_' ' ' | tr -s ' ')" ] \
      || { echo "FATAL: $real reports model '$model', not '$exp_model' from the recorded by-id name in $v. NOT creating." >&2; exit 1; }
    echo "Identity verified for $dev: model '$model', serial '$serial' match the recorded by-id name."
    [ "$(lsblk -nro NAME "$real" | wc -l)" -eq 1 ] \
      || { echo "FATAL: $real has partitions/holders — not blank. If it is an old Ceph drive, Step 5a wipes it; if it is the boot volume, the site file is wrong." >&2; exit 1; }
    size=$(lsblk -bdno SIZE "$real")
    [ "$size" -ge 3500000000000 ] && [ "$size" -le 4000000000000 ] \
      || { echo "FATAL: $real is $size bytes — not a 3.84 TB PM1743" >&2; exit 1; }
    read -rp "Create an OSD on $dev ($real) — DESTROYS its contents? [yes/NO] " ans
    [ "$ans" = "yes" ] || { echo "Stopped before $dev."; exit 1; }
    pveceph osd create "$dev"
  done
)
```

Repeat on **node-2** and **node-3** (six OSDs total).

### Step 5a — Wipe a previously used OSD drive (only if preflight said "present but NOT counted")

`scripts/preflight.sh` counts only **blank** NVMe devices; a drive with partitions or LVM holders (an earlier Ceph build, a vendor test image) is listed as *present but not counted*, and Step 5 refuses it. **DESTRUCTIVE / MANUAL-ONLY:** wipe only a drive you have identified by model and serial as one of this node's two PM1743s — never the boot volume. The block verifies the resolved device reports the model and serial encoded in the recorded by-id name before it asks:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  source /root/cerberus-node.env
  WIPE_VAR="${WIPE_VAR:?export WIPE_VAR=NODEn_OSD1_BYID or NODEn_OSD2_BYID — the drive to wipe}"
  case "$WIPE_VAR" in "NODE${NODE_NUM}_OSD1_BYID"|"NODE${NODE_NUM}_OSD2_BYID") ;; *) echo "FATAL: $WIPE_VAR is not one of this node's OSD drives" >&2; exit 1 ;; esac
  cerberus_require_vars "$WIPE_VAR"
  dev="${!WIPE_VAR}"; real="$(readlink -f "$dev")"
  lsblk -o NAME,MODEL,SERIAL,SIZE,FSTYPE,MOUNTPOINTS "$real"
  base="${dev##*/}"; base="${base#nvme-}"
  case "$base" in eui.*) echo "FATAL: $WIPE_VAR=$dev is an eui by-id alias — record the model_serial name at 00a §4 so the wipe target can be bound to its drive" >&2; exit 1 ;; esac
  case "$base" in *_1) base="${base%_1}" ;; esac
  exp_serial="${base##*_}"; exp_model="${base%_*}"
  model="$(lsblk -dno MODEL "$real" | tr -s ' ' | sed 's/^ //; s/ $//')"
  serial="$(lsblk -dno SERIAL "$real" | tr -d '[:space:]')"
  [ "$serial" = "$exp_serial" ] \
    || { echo "FATAL: $real reports serial '$serial', not the '$exp_serial' recorded in $WIPE_VAR — wrong or swapped drive. NOT wiping." >&2; exit 1; }
  [ "$(printf '%s' "$model" | tr '_' ' ' | tr -s ' ' | sed 's/^ //; s/ $//')" = "$(printf '%s' "$exp_model" | tr '_' ' ' | tr -s ' ')" ] \
    || { echo "FATAL: $real reports model '$model', not '$exp_model' from the recorded by-id name in $WIPE_VAR. NOT wiping." >&2; exit 1; }
  echo "Identity verified for $dev: model '$model', serial '$serial' match the recorded by-id name."
  grep -q "^$real" < <(findmnt -rno SOURCE) && { echo "FATAL: $real (or a partition) is mounted" >&2; exit 1; }
  read -rp "WIPE $dev ($real) — all data and LVM on it destroyed? [yes/NO] " ans
  [ "$ans" = "yes" ] || { echo "Not wiped."; exit 1; }
  ceph-volume lvm zap --destroy "$dev"
  lsblk "$real"   # expect: the bare device, no children
)
```

- **Why whole-disk, and why no flags here:** `pveceph osd create` defaults to whole-disk `ceph-volume` provisioning with block, DB, and WAL all colocated on the same NVMe. That is exactly right for this hardware:
  - `--osds-per-device 2` splits one NVMe into two OSDs — it buys nothing on a PM1743 (Gen5, plenty of queue depth per device; splitting doubles per-OSD overhead and fragments placement) and is **mutually exclusive** with `--db_dev`/`--wal_dev`.
  - `--db_dev`/`--wal_dev` exist to put metadata/WAL on a *faster* device than a slow spinner — irrelevant here; block+db+wal all live on the same fast NVMe.
- Verify:

```bash
ceph osd tree
```

Expected shape — CRUSH host failure domain, 2 OSDs per host:

```
ID  CLASS  WEIGHT   TYPE NAME        STATUS  REWEIGHT  PRI-AFF
-1         21.00000  root default
-3          7.00000      host node-1
 0   nvme   3.50000          osd.0      up   1.00000  1.00000
 1   nvme   3.50000          osd.1      up   1.00000  1.00000
-5          7.00000      host node-2
 2   nvme   3.50000          osd.2      up   1.00000  1.00000
 3   nvme   3.50000          osd.3      up   1.00000  1.00000
-7          7.00000      host node-3
 4   nvme   3.50000          osd.4      up   1.00000  1.00000
 5   nvme   3.50000          osd.5      up   1.00000  1.00000
```

- All 6 OSDs `up`, class `nvme`, correct host mapping. If an OSD landed on the wrong host or is missing, fix before pools — misplacement here corrupts the failure domain for everything above.

## Step 6 — CephX auth check (aes256k rollout item)

Tentacle 20.2.4 (2026-08-19) fixed several CVEs including the CephX auth bypass (CVE-2025-30156) and introduced the new CephX key cipher **`aes256k`**. Run this during bring-up — it is a checklist item, not a patch you can defer:

```bash
pveceph auth status
```

- Confirm the cluster's keys report the new cipher. **Client keys are NOT auto-rotated** — per-client migration is manual if any legacy keys remain in use.
- PVE 9.2 ships kernel 7.0, which satisfies the kernel ≥ 7.0 requirement for AES256 key support on Ceph clients (relevant if Ceph-CSI clients are ever attached).

## Step 7 — CRUSH rule review

```bash
ceph osd crush rule dump replicated_rule
```

- The default `replicated_rule` is **correct for this topology and needs no changes.** It takes from `host` (failure-domain `host`), so the three replicas of every object land on three different *hosts* — one per node, spread across that node's two OSDs by the placement algorithm.
- Why that matters: with replicas spread host-wise, losing an entire node degrades the pool to 2/3 replicas (still ≥ `min_size=2`) instead of losing data. A per-OSD rule would risk placing two replicas on the same dead node.
- The consequence §04 is built on: with 3 hosts and `size=3`, **every host holds one full copy of the pool**, and a failed OSD's data can only be rebuilt onto its **sibling OSD on the same host**. A whole-node loss leaves nowhere to rebuild to — the pool runs degraded at two copies until the node returns.
- Revisit only when hardware changes (e.g. a 4th node — still host-spread; expansion bays — still per-host). Device-class rules are unnecessary: all OSDs are `nvme`.

## Verification checklist

- [ ] `ceph -s`: `mon: 3 daemons, quorum …`, `mgr: 3 daemons active`, `osd: 6 osds: 6 up, 6 in`, `health: HEALTH_OK`
- [ ] `ceph osd tree`: 2 OSDs under each of node-1/2/3, all class `nvme`
- [ ] `pveceph auth status`: aes256k cipher reported, no stale keys
- [ ] `ceph health detail` returns nothing (no warnings)

## References

Full link list lives in [docs/references.md](../../docs/references.md); the ones this section leans on:

- https://pve.proxmox.com/pve-docs/pveceph.1.html — pveceph(1) man page: install/mon/mgr/osd/pool flags.
- https://docs.ceph.com/en/tentacle/ — Ceph Tentacle documentation home.
- https://docs.ceph.com/en/latest/releases/ — Active-releases table: Tentacle vs Squid EOL check at deploy time.
