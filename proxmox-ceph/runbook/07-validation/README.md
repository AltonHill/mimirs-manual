# 07 — Validation, failure drills, and acceptance sign-off

Acceptance gate for the whole build. Run on the fully built 3-node cluster
(cluster + both corosync links + network + Ceph + pools + HA + PBS, all complete).

Conventions: variables-first (each block sources the site file through
`CERBERUS_TRACK` — track README, "Working-directory contract"). State blocks
are fail-closed `( set -euo pipefail … )` units: declining a confirmation
prompt ends **that block only** — never your shell or console session.
Observation commands (`watch …`) sit in their own blocks, outside the
subshells, so Ctrl-C on them stops nothing else. **DESTRUCTIVE steps are
marked ⚠️ DESTRUCTIVE — read the whole drill before starting, and answer the
confirmation prompts honestly.**

> Docs this section leans on: the PVE cluster, HA (watchdog/fencing), and vzdump
> backup chapters plus the PBS client guide — see
> [../../docs/references.md](../../docs/references.md).

Drill map: (a) one OSD lost **at planned fill** · (b) node power loss / HA
restart · (c) one fabric leg lost · (d) backup restore · (e) **node isolation —
the fencing drill**. Timings throughout are §05's: protected VMs back in
roughly **2–5 minutes** end to end (plus guest boot).

---

## 7.1 — Re-run the pre-build checks (00a, post-build)

The cluster is built; re-verify the physical layer didn't shift under you:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  # per node: link speed still 200G/1G on every physical port
  for n in "$NODE1_HOSTNAME" "$NODE2_HOSTNAME" "$NODE3_HOSTNAME"; do
    ssh "root@$n" 'for f in /sys/class/net/*; do i=${f##*/}; [ -e "$f/device" ] || continue
      printf "%s %s: " "$(hostname)" "$i"; ethtool "$i" 2>/dev/null | grep -E "Speed|Link detected" | tr "\n" " "; echo; done'
  done
  # end-to-end MTU, corosync UDP on both links, PBS backup VLAN
  bash "$CERBERUS_TRACK/scripts/net-verify.sh"
)
```

(The remote loop is single-quoted, so `$i` and `$f` expand on the node itself;
it walks `/sys/class/net`, skipping virtual interfaces, instead of parsing
`ip` output.)

## 7.2 — Ceph deep health

All of these must be clean before any failure drill:

```bash
ceph -s                       # HEALTH_OK, 3 mons quorum, 6 OSDs up+in, no misplaced/degraded
ceph health detail            # must be empty (only expected warnings tolerated, logged below)
ceph osd df                   # all 6 OSDs, weight sane, reweight 1.0, util balanced
ceph osd pool ls detail       # size=3, min_size=2, pg_autoscale_mode=on on RBD pools
ceph osd pool get-quota vm-storage   # the §04 ceiling is set
ceph mon stat                 # 3 mons, quorum 0,1,2
ceph mgr stat                 # active + standby present
ceph status --format json | python3 -c "import json,sys; d=json.load(sys.stdin); print('pgmap:', d['pgmap'])"
ceph status | grep -E "degraded|misplaced|recovering" || echo "no recovery activity"
pveceph auth status           # Tentacle auth-status check (CephX aes256k item)
corosync-cfgtool -s           # both links, every peer connected
```

Record any `HEALTH_WARN` that is expected (e.g. autoscaler warm-up) in the sign-off
table; everything else must be `HEALTH_OK`.

---

## 7.3 — Drill (a): one OSD lost at planned fill ⚠️ DESTRUCTIVE (data-plane)

**What it proves:** the §04 capacity model. At the planned fill, losing one
OSD forces its **sibling on the same host** to absorb its data (~2× its
share); the drill shows that recovery **completes** — the sibling lands near
86 %, below `backfillfull` (90 %). On a near-empty cluster this drill proves
nothing, so it fills first. **It leaves the cluster at its limit by design** —
read the abort criteria before you start.

**Prerequisites:** §7.2 clean; the §04 quota set; a test VM doing steady
write I/O on `vm-storage` (e.g. `fio`) whose errors you are watching; not
inside the 02:00 backup window.

### Step 1 — Fill to the planned level

Fill `vm-storage` to **98 % of its quota** (the quota is the ceiling; stopping
just short keeps the pool's `full` quota flag — which blocks writes — from
tripping). The fill is one scratch RBD image written with `rbd bench`
(sequential 4 MiB writes, real allocated data; Ceph pools do not compress by
default). On the reference build that is ~3.2 TB minus what VMs already hold.
**Expected duration:** at 1–2 GB/s of client writes, ~30–60 minutes for
3.2 TB — the block prints the first minute's rate, extrapolate from it.

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars POOL_VM_NAME POOL_VM_QUOTA_BYTES
  POOL="$POOL_VM_NAME"; IMG="cerberus-fill"
  stored="$(ceph df detail --format json | python3 -c 'import json,sys
d=json.load(sys.stdin); print(next(p["stats"]["stored"] for p in d["pools"] if p["name"]==sys.argv[1]))' "$POOL")"
  target=$(( POOL_VM_QUOTA_BYTES / 100 * 98 ))
  fill=$(( target - stored ))
  [ "$fill" -gt 0 ] || { echo "Pool already at/over 98 % of quota (stored=$stored) — nothing to fill"; exit 1; }
  fill_mib=$(( fill / 1048576 ))
  echo "pool $POOL: stored $stored B, quota $POOL_VM_QUOTA_BYTES B -> writing $fill_mib MiB into $POOL/$IMG"
  ! rbd info "$POOL/$IMG" >/dev/null 2>&1 || { echo "FATAL: $POOL/$IMG exists — clean up first (Step 4)" >&2; exit 1; }
  read -rp "Fill $POOL with $fill_mib MiB of scratch data (cluster ends at its planned limit)? [yes/NO] " ans
  [ "$ans" = "yes" ] || { echo "Not filled."; exit 1; }
  rbd create --size "${fill_mib}M" "$POOL/$IMG"
  rbd bench --io-type write --io-size 4M --io-threads 16 --io-pattern seq \
    --io-total "${fill_mib}M" "$POOL/$IMG"
  ceph df detail
  ceph osd df
)
```

Expect afterwards: `ceph osd df` shows every OSD near **43 %** used, the pool
just under its quota, `HEALTH_OK` (or a quota-proximity warning you record).

### Step 2 — Fail one OSD by stopping its daemon

Stopping the daemon is a real failure: `ceph osd down` alone does not hold —
a running OSD reports "wrongly marked me down" and is back within seconds.
Choose the OSD (`DRILL_OSD`) and note its **sibling** on the same host from
`ceph osd tree` (on the reference build: osd.0 ↔ osd.1, 2 ↔ 3, 4 ↔ 5). Run on
**the node that hosts the OSD**:

```bash
( set -euo pipefail
  OSD="${DRILL_OSD:?export DRILL_OSD=the OSD id to fail, e.g. 0}"
  [[ "$OSD" =~ ^[0-9]+$ ]] || { echo "FATAL: DRILL_OSD=$OSD" >&2; exit 1; }
  systemctl is-active --quiet "ceph-osd@$OSD" \
    || { echo "FATAL: ceph-osd@$OSD is not running on $(hostname) — run this on its host" >&2; exit 1; }
  ceph osd tree
  read -rp "STOP ceph-osd@$OSD on $(hostname)? Its sibling must absorb its data. [yes/NO] " ans
  [ "$ans" = "yes" ] || { echo "Not stopped."; exit 1; }
  systemctl stop "ceph-osd@$OSD"
  sleep 60
  # PASS criterion 1: the OSD is DOWN and STAYS down (a daemon that is merely
  # marked down comes straight back)
  systemctl is-active --quiet "ceph-osd@$OSD" && { echo "FAIL: ceph-osd@$OSD is running again" >&2; exit 1; }
  grep -Eq "^osd\.$OSD[[:space:]]+down" <<<"$(ceph osd dump)" \
    && echo "PASS: osd.$OSD is down 60 s after the stop" \
    || { echo "FAIL: osd.$OSD is not reported down" >&2; exit 1; }
  echo "Backfill onto the sibling starts when osd.$OSD is marked OUT: automatically after"
  echo "mon_osd_down_out_interval (600 s default), or now with: ceph osd out $OSD"
)
```

Watch it (Ctrl-C stops only the watch):

```bash
watch -n 10 'ceph -s; ceph osd df'
```

**EXPECTED RESULT (pass criteria):**

- `ceph -s`: `1 osds down` (then `out`), PGs **degraded**, then **backfilling** —
  the degraded count *falls* as the **sibling** OSD rebuilds the failed OSD's
  copies (no other OSD can take them: the other two hosts already hold theirs).
- The sibling's `%USE` in `ceph osd df` climbs from ~43 % toward **~86 %**;
  an `OSD_NEARFULL` HEALTH_WARN for it is **expected** (above 85 %) — record it.
- Client I/O on the test VM continues throughout — a latency rise is
  acceptable, I/O errors are not.
- **Recovery completes:** all PGs `active+clean` with osd.N still down/out,
  no `backfill_toofull`, HEALTH_WARN only for the down OSD and nearfull.
- **Backfill duration** — record it. Expect roughly **0.5–3 h** to rebuild
  ~1.6 TB onto one NVMe under the default mClock recovery profile
  (**[unverified]** estimate for this hardware; the measured number is the
  site's evidence).

**ABORT criteria — stop the drill and go straight to Step 3 if any of:**

- any OSD reaches **≥ 89 % `%USE`**, or `ceph health detail` shows
  `OSD_BACKFILLFULL`, `backfill_toofull` or `OSD_FULL`;
- client I/O errors on the test VM, or `SLOW_OPS` persisting > 5 minutes;
- any second OSD, host or MON fails during the drill;
- the pool's quota flag trips (writes blocked).

(Aborting is not passing: an abort at planned fill means the ceiling is
wrong for this site — re-derive §04's numbers before sign-off.)

### Step 3 — Roll back: delete the fill, then bring the OSD back

Deleting the scratch image first frees the sibling and shortens the
rebalance that follows:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars POOL_VM_NAME
  OSD="${DRILL_OSD:?export DRILL_OSD=the OSD id stopped in Step 2}"
  if rbd info "$POOL_VM_NAME/cerberus-fill" >/dev/null 2>&1; then
    rbd rm "$POOL_VM_NAME/cerberus-fill"     # reclaim is asynchronous; ceph df catches up
  fi
  systemctl start "ceph-osd@$OSD"            # on the OSD's host
  ceph osd in "$OSD"
  ceph osd df
)
```

```bash
watch -n 10 'ceph -s'      # EXPECTED: recovery traffic, then HEALTH_OK with 6 OSDs up+in
```

If the OSD does not come back, follow §03 OSD replacement steps (wipe with
§03 Step 5a, then Step 5) — do not proceed to drill (b) until `ceph -s` is
HEALTH_OK and `ceph df` shows the fill gone.

---

## 7.4 — Drill (b): node power loss / HA restart ⚠️ DESTRUCTIVE (cluster-level)

Simulates a node that **loses power**. **What it proves:** corosync notices,
the CRM restarts the node's HA VMs on the survivors, Ceph keeps serving I/O
degraded, and the node rejoins cleanly. **What it does not prove: fencing.** A
powered-off node cannot fence itself — its watchdog lives on the dead box.
Fencing is proven only by drill (e) (§7.7).

> Proxmox HA has **no external fence agents** (no ipmilan/STONITH). Fencing here =
> the node's own watchdog timer expiring. The XCC web session may be used to
> *observe* the node only — never as a fence device.

**Prerequisites:** at least one HA VM running on node-2; `ha-manager status`
shows the HA manager active on all 3 nodes; §7.3 rolled back, HEALTH_OK.

```bash
ha-manager status
qm list | head -20
```

Then **power off node-2 through its XCC web console** (Power Actions → Power
Off — immediate, not graceful). MANUAL-ONLY: there is no command to paste for
this step; note the wall-clock time.

**EXPECTED RESULT (pass criteria; §05 timings):**

| Milestone | How to observe | Approx timing |
|---|---|---|
| corosync marks node-2 lost | `pvecm status` on node-1 shows node-2 offline | within ~30–60 s of power-off |
| fencing | **not exercised** — node-2 is off; nothing can fence (drill e covers it) | — |
| HA manager recovers node-2's resources | `journalctl -u pve-ha-crm -f` on a survivor: node-2 `fence` → services recovered, once its HA lock has expired | ~1–2 min after detection |
| node-2's VMs running on node-1/node-3 | `ha-manager status`, `qm status VMID` | **~2–5 min from power-off**, plus guest boot |

- VMs come up on survivors per HA rules (`ha-manager rules` — node-affinity, not
  the deprecated `groupadd`).
- Ceph: node-2's **2 OSDs** go down → HEALTH_WARN, PGs **undersized+degraded
  at two copies**; `min_size=2` keeps pools readable and writable. **No
  re-replication happens** — with only two hosts up there is nowhere to put a
  third copy (§04). After 10 minutes node-2's OSDs are marked out; still no
  data moves. Nothing heals until node-2 returns.
- **No split-brain**: corosync quorum is 2 of 3; node-1 + node-3 hold quorum.
  This drill runs with exactly one node down — a second failure now would lose
  quorum (and, for Ceph, push PGs below `min_size`).

**ROLLBACK (rejoin node-2):** power node-2 back on via XCC (Power Actions →
Power On), then:

```bash
watch -n 5 'pvecm status'      # node-2 returns to quorum: 3 votes online, both links
```

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  # HA: VMs that failed over STAY on survivors (HA does not migrate back
  # automatically unless rules say so). Migrate one back if site policy wants
  # node-2 repopulated:
  VMID="${MIGRATE_VMID:?export MIGRATE_VMID=the VM to move back to node-2}"
  qm migrate "$VMID" "$NODE2_HOSTNAME" --online
)
```

```bash
watch -n 10 'ceph -s'          # EXPECTED: recovery of what changed while node-2 was off, then HEALTH_OK
```

Do not start drill (c) until corosync shows 3/3 on both links and Ceph is HEALTH_OK.

---

## 7.5 — Drill (c): one fabric leg lost ⚠️ DESTRUCTIVE (network)

Simulates losing one SN5610 fabric path (one 200G leg on node-2). **Expected:
the bond fails over inside itself and nothing above it notices** — Ceph,
corosync link1 and the PBS VLAN all ride `bond0`, so they move with it.

**Prerequisites:** dual 200G links per node landed on *different* SN5610s
(00a §6, §02); corosync link0 on 1GbE mgmt and link1 on the fabric (01 §7); a
test VM doing steady write I/O (e.g. `fio` sequential write) with visible
throughput.

Down node-2's **active** leg — pick ONE way: on the switch, `nv set interface`
for node-2's leg (`${SWITCH_A_DAC_PORT}s1` in the cable plan) `link state
down` + `nv config apply`; physically, pull the DAC (the real-world case); or
on node-2 itself:

```bash
( set -euo pipefail
  source /root/cerberus-node.env      # on node-2
  read -rp "Administratively DOWN $BF3_P1 on $(hostname)? I/O must continue. [yes/NO] " ans
  [ "$ans" = "yes" ] || { echo "Not downed."; exit 1; }
  ip link set "$BF3_P1" down
  sleep 5
  grep 'Currently Active Slave' /proc/net/bonding/bond0   # expect BF3_P2
)
```

**EXPECTED RESULT (pass criteria):**

- Test-VM I/O **continues uninterrupted** — a brief stall during the bond
  failover (~1–3 s with miimon 100) is acceptable; I/O errors and VM stalls
  are not.
- `ceph -s` stays HEALTH_OK (or at most a brief WARN if a heartbeat flaps).
- corosync: `corosync-cfgtool -s` shows **both links still connected** on
  node-2 — dropping one bond leg fails over *inside* active-backup, so link1
  (on vmbr1 over the bond) stays up, and link0 was never touched. **No quorum
  loss, no fencing.** (A link1 "down" here would mean the bond did not fail
  over — a FAIL.)
- `cat /proc/net/bonding/bond0` on node-2: active slave = `BF3_P2`;
  `ip -s link show "$BF3_P2"` shows the traffic.

**ROLLBACK:** bring the leg back (`ip link set "$BF3_P1" up` on node-2 with
`/root/cerberus-node.env` sourced, or reverse the switch change / re-seat the
DAC). Then:

```bash
sleep 30
corosync-cfgtool -s    # both links up on all nodes
ceph -s                # HEALTH_OK
cat /proc/net/bonding/bond0   # both slaves up (primary returns to BF3_P1)
```

If I/O *stopped*, the dual-link design is broken (both legs share one switch or
the bond config is wrong) — fix §02 before signing off.

---

## 7.6 — Drill (d): backup restore drill

Non-destructive to production **if** done on a scratch VM — treat it as
destructive to the *test* VM only. Restores a real backup from PBS and boots
it, isolated.

```bash
bash "$CERBERUS_TRACK/scripts/backup-drill.sh"   # guided; confirmations at every step
```

What the guided path does (and the manual path, §06.7, matches it):

1. Builds the one repository string — `'pve-backup@pbs!pve-token'@PBS_VLAN_IP:PBS_DATASTORE`
   (token form, backup VLAN) — and reads the token secret with no echo.
2. Lists the test VM's snapshots; restores one disk image to a scratch path;
   `qemu-img info` verifies it.
3. Boot test: `qmrestore` from PBS **through the registered storage** —
   volume ID `pbs-cerberus:backup/vm/VMID/TIME` — to a NEW vmid (never the
   source), with `--unique 1` (new MACs) and **every NIC `link_down=1` before
   the first start**, so the copy can never collide with the running original
   on a production bridge. `qmrestore`'s errors are shown, not hidden.
4. Times snapshot-list → booted-login.

**EXPECTED RESULT (pass criteria):**

- Snapshot list returns the nightly backup(s) from the §06 job.
- Restored disk image verifies (`qemu-img info` sane, size matches).
- Restored VM boots to a login prompt on its console, network links down.
- **Timing recorded:** snapshot-list → booted-login. This is the site's RTO
  evidence — write it into the sign-off table (§7.8, item 10).
- **Run against the live PBS before sign-off** — the auth string, ACLs and
  volume-ID path are only proven there.

Clean up after sign-off: `qm destroy` the restored test VM (with `--purge`)
and delete the scratch image.

---

## 7.7 — Drill (e): node isolation — the fencing drill ⚠️ DESTRUCTIVE (cluster-level)

**What it proves:** watchdog self-fencing — the only fencing this design has
(§05). node-2 stays **powered on** with its HA VM running; both corosync paths
are cut; node-2 loses quorum, its **IPMI watchdog fires and power-cycles it**,
and only **after** that do its VMs start elsewhere. That ordering is the whole
protection against two writers on one RBD image. Sign-off row 8 ("fencing
validated") is checkable **only** by this drill.

**Prerequisites (carried over from drill (b), and stricter):**

- hardware watchdog configured on every node (`ipmi_watchdog`;
  `ipmitool mc watchdog get` shows a ~10 s countdown, timer running);
- **node-2 holds a running HA resource** — `ha-manager status` shows an HA VM
  `started` on node-2. A node with no HA resources has no armed watchdog and
  cannot self-fence; the drill would prove nothing;
- `ha-manager status` shows the HA manager active on all 3 nodes; both
  corosync links up; HEALTH_OK; drills (a)–(d) rolled back;
- **an observer on node-1** (SSH) and **node-2's XCC remote console open**:
  link0 carries SSH to node-2, so the cut is made from node-2's console.

On **node-1**, start observing:

```bash
watch -n 2 'pvecm status | grep -E "Quorate|Total votes"; ha-manager status'
```

On **node-2's XCC console** (not SSH), cut both links — link0 (`vmbr0`, mgmt)
and link1 (`vmbr1`, Ceph public): the node stays up, its VM keeps running.
Note the time printed:

```bash
( set -euo pipefail
  source /root/cerberus-node.env
  [ "$NODE_NUM" = "2" ] || { echo "FATAL: run this on node-2 (NODE_NUM=$NODE_NUM)" >&2; exit 1; }
  ha-manager status | grep -E "$(hostname)" || true
  ipmitool mc watchdog get
  read -rp "Cut BOTH corosync links on $(hostname) (vmbr0 + vmbr1)? It should self-fence. [yes/NO] " ans
  [ "$ans" = "yes" ] || { echo "Not cut."; exit 1; }
  echo "CUT at $(date -u +%H:%M:%S) UTC"
  ip link set vmbr1 down
  ip link set vmbr0 down
)
```

**EXPECTED RESULT (pass criteria; §05 timings, approximate — verify on site):**

| Milestone | How to observe | Approx timing (from the cut) |
|---|---|---|
| node-2 loses quorum; node-1+3 stay quorate (2/3) | node-1 watch: node-2 gone, `Quorate: Yes` | seconds |
| **node-2 self-fences** — IPMI watchdog fires, BMC power-cycles it | XCC console goes dark / POST; XCC event log: watchdog timer expired → power cycle | **~1–2 min** (lost quorum → LRM stops feeding the watchdog → ~10 s countdown) |
| CRM recovers node-2's HA VM on node-1/node-3 | node-1: `ha-manager status` node-2 `fence` → VM `started` elsewhere; `journalctl -u pve-ha-crm` | after node-2's HA lock expires: total **~2–5 min** |
| node-2 boots and rejoins | links come back up on boot (the `ip link` cut is not persistent); `pvecm status` 3/3 | a few minutes later |

- **The pass criterion that matters:** the XCC event-log timestamp of node-2's
  watchdog power-cycle is **earlier** than the time the VM started on the
  survivor (task log / `journalctl -u pve-ha-crm` on node-1). Record both.
- Ceph during the drill: node-2's 2 OSDs go down → degraded at two copies, no
  re-replication (§04), recovery of the delta after node-2 rejoins.

**ABORT — and record a FAIL — if node-2 has not reset ~90 s after the cut:**
power node-2 **off** immediately from its XCC (Power Actions → Power Off).
Do not wait: if the watchdog is not firing, the CRM will start node-2's VM on
a survivor while the original may still be writing — the exact dual-writer
case fencing exists to prevent. A FAIL here blocks sign-off: fix §05 Step 1
(watchdog module, competing watchdogs, XCC auto-restart policy), then re-run.
(The timing rationale — the LRM lock lapsing ~60 s after quorum loss and the
CRM waiting for it to expire — follows the PVE HA chapter's fencing
description; the exact intervals on 9.2 are **[unverified]**.)

**ROLLBACK:** none needed beyond the automatic reboot — node-2 comes back with
its links up. Confirm: `pvecm status` 3/3 with both links (`corosync-cfgtool
-s`), `ha-manager status` all nodes active, `ceph -s` back to HEALTH_OK. HA
VMs that moved stay where they are (migrate back per drill (b)'s rollback if
policy wants).

---

## 7.8 — Acceptance sign-off checklist

Fill `Actual` during the run. Every row must be PASS before handover.

| # | Item | Expected | Actual | Pass |
|---|---|---|---|---|
| 1 | PVE cluster | 3/3 nodes, quorum, PVE 9.2.x | | |
| 2 | Pre/post-build physical checks | 200G links, MTU 9000 end-to-end (all three fabric VLANs), OSD drives raw | | |
| 3 | Ceph health | `HEALTH_OK`, 6 OSDs up+in, 3 mons quorum | | |
| 4 | Ceph pools | size=3/min_size=2, pg_autoscale=on, **quota = the §04 ceiling** | | |
| 5 | Corosync | **both links required and up** (link0 mgmt, link1 fabric), quorum 3/3, UDP 5405–5412 open on both networks | | |
| 6 | HA rules + watchdog | node-affinity rule active for `HA_PROTECTED_VMS`; watchdog ~10 s countdown on all nodes (configured — not yet proof of fencing) | | |
| 7 | Drill (a) OSD loss at planned fill | filled to 98 % of quota; OSD daemon stopped and stayed down; sibling backfill **completed** (≤ ~86 %, no `backfill_toofull`); I/O uninterrupted; backfill time: ____; rollback to HEALTH_OK | | |
| 8 | **Fencing validated** — drill (e) isolation | node-2 powered on, both links cut; watchdog power-cycle **before** the VM started elsewhere (times: fence ____ / VM start ____); total ≤ ~5 min | | |
| 9 | Drill (c) fabric leg loss | bond failover inside active-backup; I/O continues; **link1 stays up**; no quorum loss | | |
| 10 | Drill (d) restore | test VM restores from PBS (`pbs-cerberus:backup/…`), isolated, and boots; RTO recorded: ____ | | |
| 11 | Backup job | nightly 02:00 job on `pbs-cerberus` (backup VLAN), prune `keep-daily=7,keep-weekly=4,keep-monthly=6` matching PBS `--keep-*` | | |
| 12 | Tentacle auth status | `pveceph auth status` reviewed, aes256k plan noted | | |
| 13 | Firmware baseline | XCC/UEFI/adapters at agreed baseline, 940-8i JBOD, no virtual drives | | |
| 14 | Drill (b) node power loss / HA restart | node-2's HA VMs running on survivors ≤ ~5 min from power-off (§05: 2–5 min + guest boot); Ceph degraded at two copies, no re-replication, writable; rejoin clean | | |

(Row 8 was the old "drill (b) — fence ≤ ~5 min" row: fencing cannot be
observed on a powered-off node, so it now names the isolation drill; drill
(b) moved to row 14. Rows 1–13 keep their numbers, so references such as
"item 10" stay valid.)

**Signed off by:** ____________________ **Date:** ______________

Record the completed table (and any HEALTH_WARN exceptions from §7.2 and the
drills, with justification) in the site handover pack.
