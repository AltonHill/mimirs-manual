# 04 — RBD pools

Create the VM-storage RBD pool (the pool VMs and LXC volumes live on), with replication, PG autoscaling, a **capacity model built on the failure that binds**, and a pool **quota** that makes the ceiling a guard rather than prose. Optional second-pool guidance for bulk/scratch data.

**Design targets:** `vm-storage`, size=3 / min_size=2, PG autoscale **on** (the default is `warn` — always override it), RBD application, registered as Proxmox storage, quota = the single-OSD-failure ceiling (~3.3 TB on the reference build).

**Variables** — defined in the site file `site-vars.sh` at the track root (copied from the template [00-overview/variables.sh](../00-overview/variables.sh)); **do not redefine them here**. This section consumes `POOL_VM_NAME` (template `vm-storage`), `CEPH_POOL_SIZE` / `CEPH_POOL_MIN_SIZE` (3 / 2), `POOL_VM_TARGET_SIZE` (template `3T` — the autoscaler *hint*) and `POOL_VM_QUOTA_BYTES` (template `3300000000000` = 3.3 TB — the *guard*). Both sizing values are correct **only for the reference drives** (6 × 3.84 TB, 3 hosts); the render report flags them while they still equal the template, and the capacity model below says how to recompute them. There is no silent fallback: an unset value stops the block.

Run pool commands on any node (they execute against the cluster), as one fail-closed block (track README, "Working-directory contract").

---

## Prerequisites

- §03 complete: 6 OSDs up, `HEALTH_OK`, CRUSH `host` failure domain verified.

---

## Step 1 — Create the vm-storage pool and set its quota

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars POOL_VM_NAME POOL_VM_TARGET_SIZE POOL_VM_QUOTA_BYTES \
    CEPH_POOL_SIZE CEPH_POOL_MIN_SIZE
  [[ "$POOL_VM_QUOTA_BYTES" =~ ^[0-9]+$ ]] \
    || { echo "FATAL: POOL_VM_QUOTA_BYTES=$POOL_VM_QUOTA_BYTES is not a byte count" >&2; exit 1; }
  if grep -qxF "$POOL_VM_NAME" <<<"$(ceph osd pool ls)"; then
    echo "NOTE: pool $POOL_VM_NAME already exists — not re-created; the quota and checks below still converge it"
  else
    pveceph pool create "$POOL_VM_NAME" \
      --size "$CEPH_POOL_SIZE" --min_size "$CEPH_POOL_MIN_SIZE" \
      --pg_num 128 \
      --pg_autoscale_mode on \
      --target_size "$POOL_VM_TARGET_SIZE" \
      --application rbd \
      --add_storages
  fi
  # The GUARD: the pool cannot grow past the single-OSD-failure ceiling.
  ceph osd pool set-quota "$POOL_VM_NAME" max_bytes "$POOL_VM_QUOTA_BYTES"
  ceph osd pool get-quota "$POOL_VM_NAME"
  q="$(ceph osd pool get-quota "$POOL_VM_NAME" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["quota_max_bytes"])')"
  [ "$q" = "$POOL_VM_QUOTA_BYTES" ] \
    || { echo "FATAL: quota reads back as $q, expected $POOL_VM_QUOTA_BYTES" >&2; exit 1; }
  echo "OK: $POOL_VM_NAME quota = $POOL_VM_QUOTA_BYTES bytes"
)
```

Flag-by-flag:

- `--size 3` — three copies of every object. One replica lands on each node (see §03 CRUSH rule).
- `--min_size 2` — writes proceed as long as **two** replicas are writable. This is the pair that makes the cluster survivable: lose one node, one replica goes dark, but the remaining two keep serving I/O and quorum holds. `min_size=1` would risk split-brain writes; `min_size=3` would stall I/O on any single OSD failure. 3/2 is the correct setting for a 3-node cluster — do not change it without redesigning the topology.
- `--pg_num 128` — starting PG count: 128 PGs × 3 replicas ÷ 6 OSDs = **64 PG replicas per OSD**, below Ceph's default target of ~100 per OSD (`mon_target_pg_per_osd`). That is deliberate headroom at creation; with `--target_size` set, the autoscaler sizes the pool for its expected fill and raises `pg_num` as needed.
- `--pg_autoscale_mode on` — **mandatory, not optional.** `pveceph pool create` defaults this to `warn` (it nags instead of scaling). With `on`, Ceph grows/shrinks PG counts automatically as the pool fills. A stale PG count is how clusters end up with hot OSDs years later — set it now, at creation.
- `--target_size "$POOL_VM_TARGET_SIZE"` — tells the autoscaler how big the pool is *expected* to get, so it picks sane PG counts before the data exists. **It is a hint, not a limit**: nothing stops a pool growing past it. The template's `3T` (pveceph sizes are binary: 3 TiB ≈ 3.3 TB) matches the ceiling below.
- `--application rbd` — tags the pool for RBD use (enables RBD-specific health checks).
- `--add_storages` — registers the pool as Proxmox storage (Datacenter → Storage) so VM disks can be provisioned on it directly.
- `set-quota … max_bytes` — **the limit**. When the pool's stored data reaches the quota, Ceph marks the pool full and blocks further writes to it (VM writes stall; reads continue). That is an outage of new writes on one pool, chosen deliberately over the alternative: a cluster too full to recover from a single drive failure. Pool quotas count the pool's logical (pre-replication) data — **[unverified]** on Tentacle: confirm with `ceph df detail` (the `QUOTA BYTES` column sits beside `STORED`) before relying on it.

Verify:

```bash
ceph osd pool autoscale-status
```

Expect `vm-storage` with `AUTOSCALE on`. Also:

```bash
ceph osd pool get vm-storage size        # 3
ceph osd pool get vm-storage min_size    # 2
ceph osd pool get-quota vm-storage       # max bytes: 3.0 TiB (3.3 TB) on the reference build
ceph df detail                           # pool visible; QUOTA BYTES beside STORED
```

Alerting: watch the pool's fill against its quota (`ceph df detail`) and act at **80 % of quota** — add capacity or move data. Raising the quota above the ceiling is a design change (the cluster then cannot heal a single drive failure); record it in the site's deviation log, never as a quick fix.

---

## Capacity model — the failure that binds is one OSD, not one node

Raw: 6 × 3.84 TB = **23.04 TB**. At size=3, theoretical usable = 23.04 ÷ 3 = **7.68 TB**. **Do not plan to fill it.** The planning ceiling falls out of the failure that actually moves data.

**The placement fact.** 3 hosts, `size=3`, failure domain `host` (§03 Step 7): every PG keeps exactly one copy on each host. So **each host stores one full copy of the pool** — U TB of VM data means U TB per host, about U/2 on each of the host's two OSDs.

**The binding failure: a single OSD.** When one OSD fails, its PGs' copies must be rebuilt *on the same host* (the other two hosts already hold their own copies; CRUSH will not put two copies on one host). The only target is the **sibling OSD**, which must absorb ~2× its normal share: it goes from U/2 to **U**.

**The arithmetic (reference build, 3.84 TB OSDs, Ceph default ratios):**

| Threshold (per OSD) | Ratio | Sibling holds U ≤ … |
|---|---|---|
| `nearfull_ratio` (HEALTH_WARN) | 0.85 | 0.85 × 3.84 = **3.26 TB** |
| `backfillfull_ratio` (recovery **stops**: `backfill_toofull`) | 0.90 | 0.90 × 3.84 = **3.46 TB** |
| `full_ratio` (writes stop) | 0.95 | 0.95 × 3.84 = 3.65 TB |

- Above **3.46 TB** the cluster **cannot heal a single drive failure**: backfill onto the sibling stops at `backfillfull`, the PGs stay degraded indefinitely, and the sibling drifts toward `full`.
- **Planning ceiling: 3.3 TB of VM data** (the quota). After an OSD failure the sibling lands at 3.3 ÷ 3.84 ≈ **86 %** — past `nearfull` (a HEALTH_WARN you expect and accept during the incident), 4 points below `backfillfull` as margin for uneven PG placement and BlueStore overhead. The honest range is **~3.3–3.5 TB**; 3.3 is the safe end.
- **Raw fill at the ceiling:** 3 × 3.3 = 9.9 TB of 23.04 TB = **≈ 43 % raw** (the range tops out at ≈ 45 % raw). Normal per-OSD fill at the ceiling: 1.65 ÷ 3.84 ≈ 43 %.
- For contrast, the old "plan ~5 TB" target: a failed OSD's sibling would need 5 ÷ 3.84 ≈ **130 %** of its capacity — recovery could never complete.

**Node loss, described truthfully.** Losing a whole node does **not** re-replicate anything — there is no third host to rebuild onto:

- every PG keeps **two** copies (one on each surviving host) and runs **undersized + degraded**; with `min_size=2` it stays **readable and writable**;
- no data moves and the surviving OSDs do not fill further (after `mon_osd_down_out_interval`, 10 min by default, the dead node's OSDs are marked out — there is still nowhere to put a third copy);
- the cluster stays degraded until the node comes back (or is replaced); then recovery copies only what changed;
- **the exposure while degraded:** one more OSD failure on either surviving host leaves the affected PGs with one copy, below `min_size` — their I/O stops until a copy returns. Treat a node outage as urgent.

Rule to carry forward: **with 3 hosts and 2 OSDs per host, plan VM data ≤ ~43 % of raw** (≈ 3.3 TB here) — the quota enforces it. VM disks, LXC volumes and snapshots all draw from it. **Recompute** (sibling ≤ 0.86 × OSD size, i.e. U ≤ 0.86 × per-OSD capacity) for other drive sizes, and redo the whole model for a **4th node**: with 4 hosts a failed OSD's PGs can also move to other hosts, and the per-host share drops — the arithmetic above no longer applies.

## Step 2 (optional) — A second pool for bulk/scratch data

Add a second pool only when there is a genuine two-tier need — e.g. a scratch/archive dataset where capacity matters more than surviving a node loss. It **shares the ceiling** with vm-storage: with `size=2` each host holds on average 2/3 of the bulk data, so the binding rule becomes **vm data + 2/3 × bulk data ≤ 3.3 TB** — lower `POOL_VM_QUOTA_BYTES` in the site file (and re-run Step 1) before you give bulk any quota. The block refuses a combination that breaks the ceiling:

```bash
# DESTRUCTIVE-CAPABLE: pool deletion is irreversible. Create is safe; deletion is not.
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars POOL_VM_QUOTA_BYTES
  : "${POOL_BULK_TARGET_SIZE:?export POOL_BULK_TARGET_SIZE (autoscaler hint, e.g. 1T)}"
  : "${POOL_BULK_QUOTA_BYTES:?export POOL_BULK_QUOTA_BYTES (the bulk pool limit, bytes)}"
  CEILING_BYTES=3300000000000   # reference-build ceiling (Capacity model) — recompute for other drives
  (( POOL_VM_QUOTA_BYTES + 2 * POOL_BULK_QUOTA_BYTES / 3 <= CEILING_BYTES )) \
    || { echo "FATAL: vm quota + 2/3 x bulk quota exceeds the $CEILING_BYTES-byte ceiling — lower POOL_VM_QUOTA_BYTES first" >&2; exit 1; }
  pveceph pool create bulk-scratch \
    --size 2 --min_size 1 \
    --pg_num 64 \
    --pg_autoscale_mode on \
    --target_size "$POOL_BULK_TARGET_SIZE" \
    --application rbd \
    --add_storages
  ceph osd pool set-quota bulk-scratch max_bytes "$POOL_BULK_QUOTA_BYTES"
  ceph osd pool get-quota bulk-scratch
)
```

- `size=2 / min_size=1` buys more usable space from the same 6 OSDs — but a node loss leaves objects with a single copy, `min_size=1` keeps writing to that single copy, and a second failure loses data. This pool is for data you can afford to lose or can rebuild (build artifacts, staging copies, non-critical bulk). **Never put production VM disks on it.**
- When you would *not* add one: if the workload is all production VMs. One pool, full budget, simpler operations.

---

## Verification checklist

- [ ] `ceph osd pool autoscale-status`: `vm-storage` shows `on`
- [ ] `ceph osd lspools`: pool(s) listed with `application rbd`
- [ ] `pvesm status`: `vm-storage` appears as Proxmox storage, enabled on all nodes
- [ ] `ceph osd pool get-quota vm-storage`: max bytes = `POOL_VM_QUOTA_BYTES` (3.3 TB on the reference build)
- [ ] `ceph df detail`: pool data ≤ the quota; cluster raw use ≤ ~43 % at the ceiling; `QUOTA BYTES` confirmed to count `STORED`

## References

Full link list lives in [docs/references.md](../../docs/references.md); the ones this section leans on:

- https://pve.proxmox.com/pve-docs/pveceph.1.html — pveceph(1): pool create flags.
- https://docs.ceph.com/en/tentacle/rados/operations/placement-groups/ — Placement groups & pg_autoscale_mode semantics.
- https://docs.ceph.com/en/tentacle/rados/operations/pools/ — pool quotas (`set-quota`).
- https://docs.ceph.com/en/tentacle/rados/configuration/mon-config-ref/ — `nearfull` / `backfillfull` / `full` ratios.
