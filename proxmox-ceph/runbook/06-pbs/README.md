# 06 — Proxmox Backup Server (dedicated host)

PBS 4.2. Backs up the cluster's VMs/containers. **PBS runs on a dedicated physical
host — never on the hypervisor nodes themselves** (official Proxmox caution).

Conventions for this section: variables-first (all values come from
the site file `site-vars.sh` at the track root, copied from
[../00-overview/variables.sh](../00-overview/variables.sh) — never hard-code per-site
values in commands). Commands run on the PBS host (6.1–6.5) or a PVE node
(6.6–6.7); each host has the repo copy and `CERBERUS_TRACK` set (track
README, "Working-directory contract"), and every state block is one
fail-closed `( set -euo pipefail … )` unit.

**Two PBS addresses (H3).** `PBS_HOST_IP` (mgmt network) is for administration
only — GUI on :8007, SSH. **All backup and restore traffic uses
`PBS_VLAN_IP`, the PBS host's address on its own VLAN on the 200G fabric**
(`PBS_VLAN`, `PBS_NET`), so the nightly 02:00 job never shares the single 1GbE
mgmt link with corosync link0.

**Credentials.** The API token's secret is never stored in the repo, the site
file or a rendered guide, and never typed on a command line: it is read at run
time with `read -rs` into `PBS_PASSWORD` (the variable the PBS client reads)
and kept in the site's secrets vault. `render.py` refuses to run if a live
value sits in the site file. Auth-ids contain `!` — interactive bash
history-expands `!` inside double quotes, so **every literal auth-id in this
section is single-quoted** (`'pve-backup@pbs!pve-token'`, Proxmox's own
guidance), and the commands build it with `cerberus_pbs_auth_id` instead, so
no pasted line contains a `!` at all.

> **Docs this section leans on:** PBS docs home, `proxmox-backup-manager` man page,
> and the backup-client guide — see [../../docs/references.md](../../docs/references.md)
> (Backup section). Also see research note: **S3-compatible object storage backend is
> GA in PBS 4.2** — usable as an alternative/secondary datastore backend if the
> customer has S3 capacity; see "S3 backend option" at the end.
>
> **Verify against a live PBS before sign-off.** The token-form repository,
> the ACL intersection, the token-off-the-command-line forms and the
> PBS-storage restore path below cannot be proven from this repository; the
> §07 drill (d) run on the real PBS is the proof.

---

## 6.1 — Install PBS on the dedicated host (recommended: ISO) and put it on the backup VLAN

1. Download the PBS 4.2 ISO from the Proxmox download page; install bare-metal on
   the dedicated host (graphical or serial installer; the installer's
   management interface is the mgmt port — cable plan M7 — at `PBS_HOST_IP`).
2. After first boot, verify the version and register it in the site DNS
   so the PVE nodes can reach it by name:

```bash
proxmox-backup-manager version        # expect 4.2.x
```

3. **Backup VLAN interface.** Cable the PBS host's fabric NIC to the two
   SN5610 ports (cable plan P1/P2; switch side configured in 00a §7a as
   untagged access ports on `PBS_VLAN`). In the PBS host's
   `/etc/network/interfaces`, add an active-backup bond over the fabric NIC's
   two ports carrying `PBS_VLAN_IP` (no gateway, MTU `PBS_MTU`) — the shape,
   from the rendered guide (`FABRIC_P1`/`FABRIC_P2` are this host's kernel
   names for the fabric NIC ports, found the same way as 02 §1):

```text
auto bond-pbs
iface bond-pbs inet static
    address ${PBS_VLAN_IP}/PREFIX-OF-${PBS_NET}
    bond-slaves FABRIC_P1 FABRIC_P2
    bond-mode active-backup
    bond-miimon 100
    mtu ${PBS_MTU}
```

   Apply with `ifreload -a` from the PBS console, then prove the path both ways
   (from the PBS host to each node's backup-VLAN address, full-size frames):

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars PBS_MTU NODE1_PBS_IP NODE2_PBS_IP NODE3_PBS_IP
  for ip in "$NODE1_PBS_IP" "$NODE2_PBS_IP" "$NODE3_PBS_IP"; do
    ping -M do -s $((PBS_MTU - 28)) -c 3 -W 2 "$ip" >/dev/null \
      && echo "PASS $ip (MTU $PBS_MTU)" || { echo "FAIL $ip — check bond-pbs, the SN5610 PBS ports, node bond0.$PBS_VLAN" >&2; exit 1; }
  done
)
```

### Alternative: Debian 13 minimal + pbs-no-subscription repo

Only if an ISO install is not possible. The repo source in **deb822** format:

```bash
( set -euo pipefail
  wget https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg \
    -O /usr/share/keyrings/proxmox-archive-keyring.gpg
  printf '%s\n' 'Types: deb' 'URIs: http://download.proxmox.com/debian/pbs' \
    'Suites: trixie' 'Components: pbs-no-subscription' \
    'Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg' \
    > /etc/apt/sources.list.d/proxmox.sources
  apt update
  apt install -y proxmox-backup-server
)
```

## 6.2 — Fingerprint capture (do this once)

The PVE nodes pin the PBS server's TLS fingerprint. It is public (not a
secret): record it in `site-vars.sh` as `PBS_FINGERPRINT` (it goes into
`pvesm add pbs` and `scripts/backup-drill.sh`; the helper
`cerberus_check_fingerprint` refuses a malformed one).

```bash
proxmox-backup-manager cert info | grep -i fingerprint
# -> copy the SHA256 value (32 colon-separated hex pairs) into site-vars.sh as PBS_FINGERPRINT
```

## 6.3 — Datastore creation + retention policy

Create one datastore on the backup host's local (or SAN-attached) storage path.
Retention here uses **`--keep-*` flags** — these are correct on the PBS side.

> ⚠️ **The #1 copy-paste error:** PBS datastore commands use `--keep-daily` etc.
> PVE backup **jobs** do NOT have `--keep-*` — they use
> `--prune-backups "keep-daily=7,..."` (§6.6 below). Swap them and the command
> fails (or silently misbehaves). Watch for this in every community guide you read.

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars PBS_DATASTORE PBS_DATASTORE_PATH
  proxmox-backup-manager datastore create "$PBS_DATASTORE" "$PBS_DATASTORE_PATH" \
    --comment "Cerberus cluster backup datastore" \
    --keep-daily 7 --keep-weekly 4 --keep-monthly 6 \
    --gc-schedule daily \
    --prune-schedule daily \
    --verify-new 1 \
    --notify "gc=always,prune=always,sync=always,verify=always" \
    --notify-user admin@pbs
  proxmox-backup-manager datastore list
)
```

| Setting | Value | Why |
|---|---|---|
| `--keep-daily 7` | 7 daily snapshots | matches the PVE job prune below |
| `--keep-weekly 4` | 4 weekly snapshots | matches |
| `--keep-monthly 6` | 6 monthly snapshots | matches |
| `--gc-schedule daily` | garbage collection daily | reclaims chunks freed by prune |
| `--prune-schedule daily` | pruning daily | enforces retention server-side (belt & braces with the job-level prune) |
| `--verify-new 1` | verify new backups | each new snapshot is verify-checked once after creation |
| `--notify ...=always` | email on every run | swap to `failure` if the customer finds daily mail noisy |

Adjust with `datastore update` (same flags):

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars PBS_DATASTORE
  proxmox-backup-manager datastore update "$PBS_DATASTORE" \
    --gc-schedule daily --prune-schedule daily \
    --keep-daily 7 --keep-weekly 4 --keep-monthly 6
)
```

## 6.4 — Datastore sizing guidance

Size the datastore against the **retention depth × live data × change rate**, not
just against the Ceph pool's capacity. The protected pool holds at most its
quota — **~3.3 TB live** on the reference build (§04 capacity model) — but 17
retained snapshots accumulate changed chunks.

**Formula:**

```
datastore_capacity ≈ Σ(protected VM disk sizes) × (1 + daily_change_rate × Σ(retained days))
```

Where `Σ(retained days)` = 7 (daily) + 28 (4 weekly) + 180 (6 monthly) = 215, but
weekly/monthly snapshots mostly share chunks with the dailies — use the *change
window* of ~31 days for the incremental tail.

**Worked example against a full pool (3.3 TB, the vm-storage quota):**

- Protected VM disks (live): **3.3 TB** (everything in the pool, at its ceiling)
- Daily change rate: **5 %** (typical for mixed server VMs; measure real with `proxmox-backup-client` stats)
- Incremental tail: 0.05 × 3.3 TB × 31 days = **5.1 TB**
- Base full (one full snapshot's chunks, deduped/compressed): ~3.3 TB × ~0.6 (zstd + chunk dedup on OS disks) ≈ **2.0 TB**
- Total ≈ 2.0 + 5.1 = **7.1 TB**

→ **Provision ≥ 10 TB for the datastore** (7.1 × 1.4 ≈ 9.9 TB: headroom for
growth and GC lag). If the workload is write-heavy (>10 % daily churn), re-run
the formula with the measured rate — at 20 % churn: 0.20 × 3.3 × 31 = 20.5 TB
tail + 2.0 TB base ≈ 22.5 TB, × 1.4 ≈ **31 TB**. Dedup in PBS is excellent for
OS disks and poor for already-compressed/encrypted data; assume worst-case
compression ratios in the estimate.

## 6.5 — Backup user, token, and ACL

Least-privilege: one dedicated user per cluster (`PBS_BACKUP_USER`, template
`pve-backup@pbs`), one token (`PBS_AUTH_NAME`, template `pve-token`), scoped
to the datastore. The auth-id is `'pve-backup@pbs!pve-token'` — written
single-quoted whenever it appears literally; the commands get it from
`cerberus_pbs_auth_id`.

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars PBS_BACKUP_USER PBS_AUTH_NAME PBS_DATASTORE
  AUTH_ID="$(cerberus_pbs_auth_id)"
  proxmox-backup-manager user create "$PBS_BACKUP_USER" \
    --comment "PVE backup user for Cerberus cluster"
  # Generates and PRINTS the token secret — copy it once into the site's
  # secrets vault. It cannot be displayed again. Never into site-vars.sh.
  proxmox-backup-manager user generate-token "$PBS_BACKUP_USER" "$PBS_AUTH_NAME"
  # ACL on BOTH the user and the token (DatastoreAdmin lets the job prune + upload)
  proxmox-backup-manager acl update "/datastore/$PBS_DATASTORE" DatastoreAdmin --auth-id "$PBS_BACKUP_USER"
  proxmox-backup-manager acl update "/datastore/$PBS_DATASTORE" DatastoreAdmin --auth-id "$AUTH_ID"
  proxmox-backup-manager acl list
)
```

**Why both (the intersection rule):** PBS evaluates an API token's effective
privileges as the **intersection** of the token's own ACL and its owning
user's ACL — a token can never do more than its user. Granting the role to the
token alone leaves it with nothing effective; granting it to the user alone
leaves the token with nothing of its own. Both get `DatastoreAdmin` on
`/datastore/$PBS_DATASTORE` and nowhere else. (Documented PBS behaviour —
**verify on the live PBS**: `proxmox-backup-manager acl list` shows both
entries, and the first backup in §6.6 succeeding with the token is the proof.)

Save: the token secret → the site secrets vault (never a file in the repo).
`PBS_FINGERPRINT` → `site-vars.sh` (6.2).

## 6.6 — PVE side: register PBS storage + create the backup job

Run on any PVE node (commands replicate across the cluster). The storage
registration points at the **backup-VLAN address** and the **token** auth-id;
the secret is entered at a no-echo prompt — `pvesm add` with `--password`
given no value prompts for it **[unverified]**: if this pvesm instead rejects
the empty option, register the storage in the GUI (Datacenter → Storage →
Add → Proxmox Backup Server, same values) — never by typing the secret into
the command.

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars PBS_STORAGE_ID PBS_VLAN_IP PBS_DATASTORE PBS_FINGERPRINT MAILTO BACKUP_JOB_ID
  cerberus_check_fingerprint "$PBS_FINGERPRINT"
  AUTH_ID="$(cerberus_pbs_auth_id)"
  # 1) Register PBS as a PVE storage target (skip if already registered)
  if grep -qxF "$PBS_STORAGE_ID" <<<"$(pvesm status | awk 'NR > 1 { print $1 }')"; then
    echo "NOTE: storage $PBS_STORAGE_ID already registered — verify: pvesm status"
  else
    pvesm add pbs "$PBS_STORAGE_ID" \
      --server "$PBS_VLAN_IP" \
      --datastore "$PBS_DATASTORE" \
      --username "$AUTH_ID" \
      --fingerprint "$PBS_FINGERPRINT" \
      --password      # no value: pvesm prompts, no echo [unverified]
  fi
  # 2) Create the nightly backup job, under a fixed ID (re-running is a NOTE).
  #    NOTE: retention uses --prune-backups, NOT --keep-*. There is NO --keep-* on
  #    pvesh/vzdump. (Repeating because this is the most common copy-paste error.)
  if pvesh get "/cluster/backup/$BACKUP_JOB_ID" >/dev/null 2>&1; then
    echo "NOTE: backup job $BACKUP_JOB_ID already exists — not re-created"
  else
    pvesh create /cluster/backup \
      --id "$BACKUP_JOB_ID" \
      --schedule "02:00" \
      --storage "$PBS_STORAGE_ID" \
      --mode snapshot \
      --all 1 \
      --compress zstd \
      --mailto "$MAILTO" \
      --mailnotification failure \
      --prune-backups "keep-daily=7,keep-weekly=4,keep-monthly=6" \
      --comment "Nightly cluster backup to dedicated PBS host (backup VLAN)"
  fi
  # 3) Inspect
  pvesh get /cluster/backup
  pvesm status
)
```

- `--id` on `pvesh create /cluster/backup` (a fixed job ID instead of an
  auto-generated one) is **[unverified]** on 9.2; if rejected, drop it and
  note the generated ID from `pvesh get /cluster/backup` into
  `BACKUP_JOB_ID`.
- To re-create the job: `pvesh delete "/cluster/backup/$BACKUP_JOB_ID"` (in a
  shell that has sourced the site file), then re-run the block.
- `MAILTO` is in the template-default audit: the render report flags it while it
  is still `ops@example.com`.

Retention table (both sides must agree):

| Level | PVE job (`--prune-backups`) | PBS datastore (`--keep-*`) | Verify | GC |
|---|---|---|---|---|
| daily | `keep-daily=7` | `--keep-daily 7` | `--verify-new 1` | `--gc-schedule daily` |
| weekly | `keep-weekly=4` | `--keep-weekly 4` | — | — |
| monthly | `keep-monthly=6` | `--keep-monthly 6` | — | — |
| prune run | client-side at job end | `--prune-schedule daily` | — | — |

## 6.7 — Restore drill procedure (run once after first successful backup, then per §07)

Restores are done with `proxmox-backup-client` from a PVE node. **Never restore
over a production VM's disk during the drill** — restore to a scratch path and
verify, or restore a *test* VM, isolated, and boot it (the guided
[scripts/backup-drill.sh](../../scripts/backup-drill.sh) wraps this flow, with
the same repository construction).

First, the token secret into **this shell only** (no echo, no history, no file):

```bash
read -rsp "PBS token secret (from the secrets vault): " PBS_PASSWORD; echo; export PBS_PASSWORD   # paste: session
```

Then name the drill's VM and snapshot (list them first with the read-only
step 1 below if you do not know the time), and run the restore. The block is
**set-or-fail**: every value is checked once and never overwritten afterwards:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars PBS_FINGERPRINT
  cerberus_check_fingerprint "$PBS_FINGERPRINT"
  cerberus_require_secret PBS_PASSWORD
  # The ONE repository construction (shared with scripts/backup-drill.sh):
  #   'pve-backup@pbs!pve-token'@PBS_VLAN_IP:PBS_DATASTORE
  PBS_REPOSITORY="$(cerberus_pbs_repository)"
  export PBS_REPOSITORY PBS_FINGERPRINT PBS_PASSWORD="$PBS_PASSWORD"   # the secret stays a run-time reference
  echo "repository: $PBS_REPOSITORY"
  VMID="${DRILL_VMID:?export DRILL_VMID=the test VM ID}"
  # 1) List available snapshots for the test VM (add --ns NAMESPACE if the job uses one)
  proxmox-backup-client snapshot list "vm/$VMID"
  TS="${DRILL_SNAPSHOT_TIME:?export DRILL_SNAPSHOT_TIME=a time from the list above, e.g. 2026-11-16T02:00:03Z}"
  # 2) Inspect what the snapshot contains
  proxmox-backup-client snapshot files "vm/$VMID/$TS"
  # 3) Restore a disk image to a SCRATCH path (not onto live storage); never overwrite
  OUT="/srv/restore/$VMID-disk.img"
  [ ! -e "$OUT" ] || { echo "FATAL: $OUT exists — refusing to overwrite" >&2; exit 1; }
  mkdir -p /srv/restore
  proxmox-backup-client restore "vm/$VMID/$TS" drive-scsi0.img "$OUT"
  # 4) Verify the restored image
  qemu-img info "$OUT"
  ls -lh "$OUT"
)
```

Full VM restore (the boot test — §07 drill (d)): through the **registered PVE
storage**, by PBS volume ID — there is no vzdump archive in this design.
`qmrestore "$PBS_STORAGE_ID:backup/vm/$DRILL_VMID/$DRILL_SNAPSHOT_TIME" NEW_VMID
--unique 1`, then set **every** NIC of the new VM to `link_down=1` **before its
first start**: the restored copy has the original's identity (hostname, IP,
and without `--unique` its MAC), and must never come up on a production
bridge while the original runs. `scripts/backup-drill.sh` does exactly this,
with confirmations, and shows `qmrestore`'s errors.

For the booted restore test, see [07-validation](../07-validation/README.md) §7.6 (drill d).

## 6.8 — S3 backend option (PBS 4.2 GA)

PBS 4.2 supports S3-compatible object storage as a datastore backend (GA). If the
customer wants off-site/immutable copies instead of (or in addition to) the local
datastore, use a second datastore with the S3 backend — the local datastore path
then acts as cache. The backend references an S3 endpoint configuration by ID
(created first on the PBS host — its access keys are credentials and follow the
same rule: never in the repo or the site file); the syntax is **[unverified]**
(review register #4):

```bash
( set -euo pipefail
  S3_ENDPOINT_ID="${PBS_S3_ENDPOINT_ID:?export PBS_S3_ENDPOINT_ID=the PBS S3 endpoint config ID}"
  S3_BUCKET="${PBS_S3_BUCKET:?export PBS_S3_BUCKET=the bucket name}"
  proxmox-backup-manager datastore create s3-offsite /mnt/pbs-s3-cache \
    --backend "type=s3,client=$S3_ENDPOINT_ID,bucket=$S3_BUCKET" \
    --keep-weekly 4 --keep-monthly 12 \
    --gc-schedule weekly --prune-schedule weekly
)
```

Verify bucket credentials, region/endpoint, and multipart upload permissions
before pointing backup jobs at it; treat the first week as a pilot.

---

**Checkpoint before moving on:** PBS host on the backup VLAN (MTU-clean both
ways), datastore created with matching `--keep-*` retention, user + token +
ACL (both) in place, PVE storage `pbs-cerberus` registered on `PBS_VLAN_IP`,
nightly job created, and **one manual backup + restore drill (§6.7)
completed successfully against the live PBS**.
