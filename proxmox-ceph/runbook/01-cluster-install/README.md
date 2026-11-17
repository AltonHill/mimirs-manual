# 01 — Proxmox VE cluster install

**Goal:** Proxmox VE 9.2.x installed identically on all three nodes, formed into a
quorate 3-node cluster named `cerberus`, with **both** corosync links.

**Prerequisite:** 00a phase A (§1, §2, §6, §7, §8) signed off. 00a phase B
(the on-host hardware checks) runs inside this section, at §3g, before the
cluster exists.

**Required steps that span sections:** §3g (00a phase B, before §4) and **§7
(corosync link1, after 02)**. Every build passes through both; 03 refuses to
start without link1.

Blocks follow the track's paste contract (track README, "Working-directory
contract"): `CERBERUS_TRACK` set on the node, each state block one
fail-closed `( set -euo pipefail … )` unit.

Reference: [docs/references.md](../../docs/references.md) — Proxmox VE Admin Guide,
`pvecm(1)` man page, Cluster Manager chapter.

---

## 1. Pre-flight — finalize names and IPs

**Hostname and IP changes are not supported after cluster creation.** Before
touching the installer, lock these in `site-vars.sh` and get sign-off:

- `NODE*_HOSTNAME` = `node-1`, `node-2`, `node-3` (final — no renames later)
- `NODE*_MGMT_IP` on `MGMT_NET` with `MGMT_GW`, `DNS_SERVERS`
- `NODE*_PUBLIC_IP` (corosync link1 in §7 uses them)
- `DOMAIN` for FQDNs

Verify no placeholders remain for what this section consumes:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars CLUSTER_NAME \
    NODE1_HOSTNAME NODE2_HOSTNAME NODE3_HOSTNAME \
    NODE1_MGMT_IP NODE2_MGMT_IP NODE3_MGMT_IP \
    NODE1_PUBLIC_IP NODE2_PUBLIC_IP NODE3_PUBLIC_IP \
    MGMT_GW DOMAIN DNS_SERVERS
  echo "OK: names and addresses for 01 are set"
)
```

---

## 2. PVE 9.2 ISO install — MANUAL-ONLY (per node)

Boot the Proxmox VE 9.2 ISO on each node (XCC virtual media / USB — MANUAL-ONLY).

1. **Install target:** the volume presented by the B550i-2i (the 2x 960GB M.2 in
   HW RAID1). It appears as a single disk. Do **not** select either PM1743 —
   those are Ceph OSD drives (§03) and must remain untouched.
2. **Filesystem: ext4. NOT ZFS.** Why: ZFS on top of a hardware RAID1 gains
   nothing — the controller hides the individual disks, so ZFS checksums cannot
   self-heal (there is no redundancy ZFS can see), while ZFS still costs ARC RAM
   on these 1TB hosts and complicates booting. The OS volume holds no VM data
   (that lives on Ceph); `/etc/pve` is replicated cluster-wide by pmxcfs anyway.
   ext4 on HW RAID1 is the simple, supported, boringly reliable choice.
3. Set hostname (`node-1` …), management IP / netmask / gateway / DNS from
   `site-vars.sh` at the installer network screen.
4. Set the root password (MANUAL-ONLY — record in the site's password manager),
   and an admin email.
5. Complete the install and reboot. Repeat on node-2 and node-3 with their
   respective hostnames/IPs.

After first boot on each node, confirm you can reach it: `ping` the mgmt IP and
open the GUI at `https://` + that node's `NODE*_MGMT_IP` + `:8006` (certificate
warning is expected). Copy the repo onto the node and set `CERBERUS_TRACK`
(track README) before §3.

---

## 3. Post-install per node (before clustering)

Run on **each** node, in this order:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars DOMAIN NODE1_HOSTNAME NODE2_HOSTNAME NODE3_HOSTNAME \
    NODE1_MGMT_IP NODE2_MGMT_IP NODE3_MGMT_IP
  # 3a. /etc/hosts — all nodes resolvable by name (the installer writes the
  #     local entry; each peer is added once — re-running adds nothing).
  for n in 1 2 3; do
    ip_var="NODE${n}_MGMT_IP"; host_var="NODE${n}_HOSTNAME"
    ip="${!ip_var}"; host="${!host_var}"
    if awk -v ip="$ip" -v h="$host" '$1 == ip { for (i = 2; i <= NF; i++) if ($i == h) f = 1 } END { exit !f }' /etc/hosts; then
      echo "NOTE: /etc/hosts already maps $ip to $host"
    else
      printf '%s %s.%s %s\n' "$ip" "$host" "$DOMAIN" "$host" >> /etc/hosts
    fi
  done
  # 3b. Package repositories: disable enterprise, enable no-subscription.
  #     (PVE 9 / Debian 13 "trixie" uses deb822 .sources files.)
  ls /etc/apt/sources.list.d/
  if [ -e /etc/apt/sources.list.d/pve-enterprise.sources ]; then
    mv /etc/apt/sources.list.d/pve-enterprise.sources \
       /etc/apt/sources.list.d/pve-enterprise.sources.disabled
  fi
  printf '%s\n' 'Types: deb' 'URIs: http://download.proxmox.com/debian/pve' \
    'Suites: trixie' 'Components: pve-no-subscription' \
    'Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg' \
    > /etc/apt/sources.list.d/pve-no-subscription.sources
)
```

> **Guardrail — enterprise repo 401s.** Without a Proxmox subscription key, the
> enterprise repository answers `apt update` with `401 Unauthorized`. It is
> harmless but noisy, and the wall of red can mask real errors later. That is
> why step 3b disables the enterprise `.sources` file *before* the first
> `apt update` below — do not skip it, and do not "just ignore the 401s."
> (If the site *does* hold subscriptions, keep the enterprise repo and skip
> the no-subscription file instead — the repo file is the only change.)

```bash
# 3c. Full upgrade and reboot. Service-affecting for the node (nothing runs on
#     it yet — just slow). MANUAL-ONLY in the sense that you watch it finish.
#     A failed update or upgrade stops the block: no reboot into a half-upgraded system.
( set -euo pipefail
  apt update
  apt full-upgrade -y
  reboot
)
```

After the reboot, on each node:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  # 3d. Version sanity: expect PVE 9.2.x.
  pveversion
  # 3e. Time sync — corosync and Ceph both punish clock skew.
  timedatectl
  chronyc tracking 2>/dev/null || systemctl status systemd-timesyncd --no-pager
  # If NTP_SERVERS differs from the default, point chrony at it and restart.
  # 3f. DNS resolution works for every peer.
  for h in "$NODE1_HOSTNAME" "$NODE2_HOSTNAME" "$NODE3_HOSTNAME"; do
    getent hosts "$h.$DOMAIN" || { echo "FATAL: $h.$DOMAIN does not resolve" >&2; exit 1; }
  done
)
```

Do not proceed until all three nodes are on the same `pveversion` output and
clocks agree (offset < 1s).

### 3g. REQUIRED — 00a phase B hardware gate (each node, before §4)

Now that each node has an OS, run **00a §3 (940-8i JBOD), §4 (NVMe
direct-to-CPU, by-id names recorded) and §5 (BlueField-3 NIC mode + power
cycle)** on every node, and sign off 00a's phase B checklist. They land here:
before §4, because the BF3 mode change power-cycles the node and can rename
its interfaces; and they need the OS that did not exist during 00a's first
pass. Then run `scripts/preflight.sh` on each node (it is built for exactly
this window: PVE installed, Ceph not yet).

---

## 4. Create the cluster on node-1

On **node-1 only**:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars CLUSTER_NAME NODE1_HOSTNAME NODE1_MGMT_IP
  [ "$(hostname)" = "$NODE1_HOSTNAME" ] \
    || { echo "FATAL: this is $(hostname), not $NODE1_HOSTNAME — create the cluster on node-1 only" >&2; exit 1; }
  # link0 = corosync on the management network (physically separate 1GbE NIC).
  # link1 (fabric) is added in §7 once 02 has built it — REQUIRED.
  pvecm create "${CLUSTER_NAME}" --link0 "address=${NODE1_MGMT_IP}"
)
```

Expected: cluster created, node-1 is the single quorate member. Verify:

```bash
pvecm status
# look for: Quorate: Yes, Total votes: 1, node-1 with 1 vote
```

Capture node-1's join fingerprint **now** (needed to verify node-2/3 joins):

```bash
openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256 | cut -d= -f2
# record this value (32 colon-separated hex pairs) — every join is checked against it
```

---

## 5. Join node-2 and node-3 — fingerprint-verified

On **node-2**, then **node-3**. First give this shell node-1's fingerprint,
typed or pasted from the record you made in §4 (out of band — never fetch it
from node-1 over the network you are verifying):

```bash
read -rp "node-1 SHA256 fingerprint (from §4): " NODE1_FINGERPRINT; export NODE1_FINGERPRINT   # paste: session
```

Then join. The block works out which node it is on from the hostname
(hostnames are final), refuses a malformed fingerprint, and pins link0 to
this node's mgmt IP:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars NODE1_MGMT_IP NODE2_HOSTNAME NODE3_HOSTNAME NODE2_MGMT_IP NODE3_MGMT_IP
  cerberus_check_fingerprint "${NODE1_FINGERPRINT:?read node-1s fingerprint first (block above)}"
  case "$(hostname)" in
    "$NODE2_HOSTNAME") MY_MGMT_IP="$NODE2_MGMT_IP" ;;
    "$NODE3_HOSTNAME") MY_MGMT_IP="$NODE3_MGMT_IP" ;;
    "$NODE4_HOSTNAME") MY_MGMT_IP="$NODE4_MGMT_IP" ;;
    *) echo "FATAL: hostname $(hostname) is not a joining node in the site file" >&2; exit 1 ;;
  esac
  pvecm add "${NODE1_MGMT_IP}" \
    --fingerprint "$NODE1_FINGERPRINT" \
    --link0 "address=${MY_MGMT_IP}"
)
```

- You will be prompted for `root@pam`'s password on node-1 — MANUAL-ONLY (type it;
  it is never stored).
- The `--fingerprint` must match the value captured in §4. **If the join prompt
  shows a different fingerprint, abort** — you may be joining the wrong cluster
  (or a MITM). This is the whole point of the flag.
- `--link0 address=...` pins corosync link0 to this node's mgmt IP.

---

## 6. Quorum verification

On any node:

```bash
pvecm status
# PASS: Cluster information
#         Name:             cerberus
#         Quorate:          Yes
#         Total votes:      3
#       Membership — all three nodes listed, each 1 vote, at their mgmt IPs
#       (${NODE1_MGMT_IP}, ${NODE2_MGMT_IP}, ${NODE3_MGMT_IP})

pvecm nodes
corosync-cfgtool -s     # expect LINK ID 0 with every peer connected (link 1 arrives in §7)
```

Also confirm pmxcfs is replicating: create a scratch file on node-1 under
`/etc/pve/` (e.g. `/etc/pve/.cluster-join-test`) and read it back on node-2/3,
then delete it. If it appears everywhere, the cluster filesystem is healthy.

**Now go to 02** — then come back to §7. The cluster is not complete yet.

---

## 7. REQUIRED — add corosync link1 (run after 02 §5 passes)

**This step is not optional and it is not skipped.** link0 shares the single
1GbE mgmt link and switch; one link is a single point of failure for quorum,
and a lost quorum fences nodes. link1 rides the Ceph public VLAN on the
fabric bond — the physically separate DAC path. The cluster was created with
link0 only (§4–§5) because the fabric does not exist until 02; come back here
as soon as 02 §5 passes, **before 03** (03's prerequisites refuse to start
without link1).

The procedure never edits the live replicated file in place. It **copies**
`/etc/pve/corosync.conf` to a draft, **edits** the draft (each node's
`ring1_addr`, a totem `interface { linknumber: 1 }` stanza, `config_version`
+1), **verifies** the draft, then **moves** it into place through
`corosync.conf.new` — the Proxmox Cluster Manager procedure. A broken edit
pushed to all nodes at once is the failure mode this avoids.

Run on **any one** node (the cluster must be quorate — `/etc/pve` is
read-only otherwise):

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars NODE1_MGMT_IP NODE2_MGMT_IP NODE3_MGMT_IP \
    NODE1_PUBLIC_IP NODE2_PUBLIC_IP NODE3_PUBLIC_IP
  LIVE="${COROSYNC_CONF:-/etc/pve/corosync.conf}"
  NEW="$LIVE.new"
  grep -Eq '^Quorate:[[:space:]]+Yes' <<<"$(pvecm status)" \
    || { echo "FATAL: cluster not quorate — /etc/pve is read-only; fix quorum first" >&2; exit 1; }
  [ ! -e "$NEW" ] || { echo "FATAL: $NEW exists (an earlier edit in flight?) — inspect it, remove it, re-run" >&2; exit 1; }
  if grep -Eq '^[[:space:]]*ring1_addr:' "$LIVE"; then
    echo "NOTE: ring1_addr already present — link1 is configured; nothing to edit. Run the verify block."
    exit 0
  fi
  # 1) COPY: a backup, and a draft outside /etc/pve
  BAK="${COROSYNC_WORKDIR:-/root}/corosync.conf.bak.$(date +%F-%H%M%S)"
  DRAFT="$(mktemp "${COROSYNC_WORKDIR:-/root}/corosync.conf.draft.XXXXXX")"
  cp -a "$LIVE" "$BAK"
  OLDV="$(awk '$1 == "config_version:" { print $2; exit }' "$LIVE")"
  [[ "$OLDV" =~ ^[0-9]+$ ]] || { echo "FATAL: no numeric config_version in $LIVE" >&2; exit 1; }
  NEWV=$((OLDV + 1))
  # 2) EDIT the draft: ring1_addr after each ring0_addr (mgmt IP -> public IP
  #    from the site file), config_version +1, a linknumber-1 interface stanza
  #    at the end of totem.
  MAP="$NODE1_MGMT_IP=$NODE1_PUBLIC_IP $NODE2_MGMT_IP=$NODE2_PUBLIC_IP $NODE3_MGMT_IP=$NODE3_PUBLIC_IP $NODE4_MGMT_IP=$NODE4_PUBLIC_IP"
  awk -v map="$MAP" -v newv="$NEWV" '
    BEGIN { n = split(map, a, " "); for (i = 1; i <= n; i++) { split(a[i], kv, "="); m[kv[1]] = kv[2] } }
    $1 == "ring0_addr:" {
      print
      if (!($2 in m)) { print "FATAL: ring0_addr " $2 " matches no NODE*_MGMT_IP in the site file" > "/dev/stderr"; bad = 1; next }
      ind = $0; sub(/ring0_addr:.*/, "", ind); print ind "ring1_addr: " m[$2]; next
    }
    $1 == "config_version:" { ind = $0; sub(/config_version:.*/, "", ind); print ind "config_version: " newv; next }
    /^totem[[:space:]]*[{]/ { intotem = 1 }
    intotem && /^[}]/ { print "  interface {"; print "    linknumber: 1"; print "  }"; intotem = 0 }
    { print }
    END { exit bad }' "$LIVE" > "$DRAFT"
  # 3) VERIFY the draft before it goes anywhere near /etc/pve
  r0="$(grep -Ec '^[[:space:]]*ring0_addr:' "$DRAFT")"
  r1="$(grep -Ec '^[[:space:]]*ring1_addr:' "$DRAFT")"
  [ "$r0" -ge 3 ] && [ "$r0" -eq "$r1" ] \
    || { echo "FATAL: draft has $r0 ring0_addr but $r1 ring1_addr lines" >&2; exit 1; }
  [ "$(awk '$1 == "config_version:" { print $2 }' "$DRAFT")" = "$NEWV" ] \
    || { echo "FATAL: draft config_version is not $NEWV" >&2; exit 1; }
  [ "$(grep -Ec '^[[:space:]]*linknumber:[[:space:]]*1[[:space:]]*$' "$DRAFT")" -eq 1 ] \
    || { echo "FATAL: draft does not have exactly one linknumber 1 stanza" >&2; exit 1; }
  [ "$(tr -cd '{' < "$DRAFT" | wc -c)" -eq "$(tr -cd '}' < "$DRAFT" | wc -c)" ] \
    || { echo "FATAL: draft braces do not balance" >&2; exit 1; }
  diff -u "$LIVE" "$DRAFT" || true
  read -rp "Apply this corosync.conf to the whole cluster (config_version $OLDV -> $NEWV)? [yes/NO] " ans
  [ "$ans" = "yes" ] || { echo "Not applied. Draft kept at $DRAFT, backup at $BAK."; exit 1; }
  # 4) MOVE into place: copy to .new on pmxcfs, then an atomic rename
  cp "$DRAFT" "$NEW"
  mv "$NEW" "$LIVE"
  echo "Applied (config_version $NEWV). Backup: $BAK. Now run the verify block on EVERY node."
)
```

(`COROSYNC_CONF` / `COROSYNC_WORKDIR` exist only so the procedure can be
rehearsed against a scratch copy; on a node, leave them unset.)

Verify on **every** node:

```bash
corosync-cfgtool -s   # PASS: LINK ID 0 and LINK ID 1, every peer connected on both
pvecm status          # PASS: Quorate: Yes, all votes present
```

- pmxcfs syncs the new file to `/etc/corosync/corosync.conf` on every node
  and corosync reloads it on the `config_version` change. Whether this
  corosync adds a new knet link on reload without a restart is
  **[unverified]**: if `LINK ID 1` does not appear on a node, restart
  corosync **one node at a time** (`systemctl restart corosync`, then wait for
  `Quorate: Yes` before the next node).
- The `corosync-cfgtool -s` wording (`LINK ID n`, `connected`) is the
  corosync 3 / knet form — **[unverified]** on this release; the meaning to
  check is "two links, every peer up on both".
- Rollback: copy the `/root/corosync.conf.bak.*` file to
  `/etc/pve/corosync.conf.new`, raise its `config_version` above the current
  one, and `mv` it into place.

---

## Node-4 callout

Joining a 4th node later uses the identical §5 block (it recognises
`$NODE4_HOSTNAME` and pins link0 to `$NODE4_MGMT_IP`). Then add its link1:
the §7 block edits only a config with no `ring1_addr` yet, so add node-4's
`ring1_addr: $NODE4_PUBLIC_IP` line by the same copy → edit → verify → move
discipline (bump `config_version`), and re-run the §7 verify block.
Quorum notes for 4 nodes:

- 4 votes: the cluster still tolerates **one** node down (quorum needs 3 of 4).
  A 4th node adds capacity, not resilience.
- **Keep Ceph MONs at 3** (§03) — an even MON count gains nothing and complicates
  elections. MGRs may run on all nodes (active/standby is automatic).

## 01 acceptance checklist

- [ ] Hostnames/IPs final and recorded in `site-vars.sh` (§1)
- [ ] PVE 9.2.x installed on the M.2 RAID1 volume, **ext4**, on all 3 nodes (§2)
- [ ] Enterprise repo disabled; no-subscription enabled; `full-upgrade` done; same `pveversion` everywhere (§3)
- [ ] NTP in sync, DNS resolving on all nodes (§3)
- [ ] 00a phase B (§3–§5) signed off on every node; `preflight.sh` passes on every node (§3g)
- [ ] `pvecm create cerberus` on node-1 with link0 = mgmt IP (§4)
- [ ] node-2 and node-3 joined with **verified** fingerprints (§5)
- [ ] `pvecm status`: 3 votes, quorate; `corosync-cfgtool -s` shows link 0 up to every peer (§6)
- [ ] **REQUIRED, after 02:** corosync link1 added by copy → edit → verify → move; both links up to every peer on every node (§7)
