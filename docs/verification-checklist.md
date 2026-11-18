# Verification checklist — claims the library takes on trust

Every load-bearing claim the library could not check from inside the
repo, with its status and what clears it. Built in M7 (2026-11-17) from
the review's unverifiable register (16 items) plus the items later
briefs added. Vendor sources were checked on 2026-11-17; re-check any
version-dependent line at deploy time.

**Status words:**
- **verified** — checked against the named vendor source; the text
  matches it.
- **corrected** — the vendor source disagreed; the text was fixed to
  match it.
- **still unverified** — no vendor source settles it (hardware-,
  firmware- or binary-dependent). Where it is load-bearing, the text
  carries the **[unverified]** marker, and this list says how to clear it.

**First contact** means the first time the library meets the real
thing: the first BCM 11 head node, the first PVE 9.2 node, the first
B300. The **BCM first-contact block** (end of this page) is run on the
first head node before the first provisioning wave; clear the items
there and record the results in BCM section 10.

When an item clears: change its status here, remove the
**[unverified]** marker from the text, and cite what cleared it.

## A. The original register (review M1, items 1–16)

| # | Claim (where) | Status | Source / how it clears |
|---|---|---|---|
| 1 | Vera Rubin rack-scale naming and "shipping as of Sept 2026" (`bcm/00-variables.sh`, `bcm/00-overview.md`) | **corrected** | The rack is **Vera Rubin NVL72**: NVIDIA's OCP blog carries an editor's note on the rebrand from "Vera Rubin NVL144" (https://blogs.nvidia.com/blog/gigawatt-ai-factories-ocp-vera-rubin/). Shipping now quoted in NVIDIA's words: "Production shipments of Vera Rubin are set to begin starting this fall" (https://nvidianews.nvidia.com/news/vera-rubin-full-production-agentic-ai-factory, 2026-05-31). |
| 2a | Tentacle 20.2.4 (2026-08-19) fixed CVE-2025-30156 and introduced the CephX key type `aes256k` (Cerberus 03 Step 6) | **verified** | https://ceph.io/en/news/blog/2026/v20-2-4-v19-2-6-combo-released/ (dated 2026-08-19: "The fix for CVE-2025-30156 introduces a new CephX key type, aes256k"); https://docs.ceph.com/en/latest/security/CVE-2025-30156/ (fixed in Tentacle 20.2.4 and Squid 19.2.6). Tentacle's latest is now 20.2.5 (2026-11-02, releases table) — 20.2.4 stays the floor. |
| 2b | `pveceph auth status` exists (Cerberus 03 Step 6, 07 §7.2, sign-off row 12) | **verified** | pveceph(1), PVE 9.2.14: `auth status` — "Show current and pending cephx key ciphers and aes256k migration prerequisites" (https://pve.proxmox.com/pve-docs/pveceph.1.html). |
| 2c | "PVE 9.2 ships kernel 7.0"; kernel ≥ 7.0 for `aes256k` on kernel clients (Cerberus 03) | **verified** | PVE Roadmap, 9.2: "Kernel 7.0 as new stable default" (https://pve.proxmox.com/wiki/Roadmap); CVE page: "Linux kernel support began in 7.0". |
| 3a | `disarm-ha` / `arm-ha` new in PVE 9.2 (Cerberus 05 Step 4) | **verified + corrected** | Roadmap 9.2: "The HA stack can now be disarmed cluster-wide via the new `disarm-ha` and `arm-ha` CRM commands." ha-manager(1) 9.2.14: a **resource mode is required** (`freeze` or `ignore`). The block ran `disarm-ha` with no mode; it now runs `disarm-ha freeze`. |
| 3b | Node maintenance "gracefully migrates … (new in 9.2)" (Cerberus 05) | **corrected** | ha-manager(1) 9.2.14, "Node Maintenance Mode": the LRM "will mark itself as unavailable, but still process all migration commands … until all active services got moved", and disabling the mode moves the services back — verified. Nothing marks it new in 9.2; the "(new in 9.2)" label was removed. |
| 4 | PBS 4.2; S3 backend GA; `--backend "type=s3,client=…,bucket=…"` (Cerberus 06) | **verified** | PBS 4.2 released 2026-04-29 with the S3 backend out of technology preview (https://pbs.proxmox.com/wiki/Roadmap; Proxmox press release). Syntax: https://pbs.proxmox.com/docs/storage.html (4.2.11-1) — `datastore create … --backend type=s3,client=my-s3-ep,bucket=pbs-s3-bucket`. The 06.3 `datastore create` options (`--keep-*`, `--gc-schedule`, `--prune-schedule`, `--verify-new`, `--notify`, `--notify-user`) are all listed in proxmox-backup-manager(1), 4.2.11-1. |
| 5a | Tentacle is the PVE 9.2 default (`variables.sh`) | **verified** | Roadmap 9.2: "Switch the default Ceph release for fresh installations to 20.2 Tentacle." |
| 5b | "Squid EOL ~Sept/Oct 2026" (`variables.sh`, Cerberus 00, 03) | **corrected** | https://docs.ceph.com/en/latest/releases/: Squid estimated EOL **2026-12-02**; Tentacle 2027-06-01. All three places now say so, with the check date. |
| 6 | "1-in-3 chance of a DGX coming up with the wrong mapping" (BCM 12) | **corrected (M4)** | Provenance attached by ruling H1: the field record (a sick NVMe) says each reboot had "a 1 in 3 chance of **working**". The companion claim "every mismatch makes BCM wipe and reprovision the node" is not in the record and carries **[unverified]** in 12 — clears on first contact (observe a mismatch reboot, or BCM documentation). |
| 7 | `cmsh … network; use X ; set network <cidr>` (`runbook.py networks`) | **corrected** | BCM 11 Administrator Manual, 3.2.3.3 (https://docs.nvidia.com/base-command-manager/product-manuals/admin/configuring): the network object's address is `set baseaddress 192.168.1.0` + `set netmaskbits 24`; no property is called `network`. The pass now emits `baseaddress`/`netmaskbits`. Still **unverified** (first contact): the exact `network; add NAME` form and the `managementallowed` property name — the manual names the GUI setting "Management allowed" but shows no cmsh example. |
| 8 | `set disksetup /root/<file>.xml` — a file path as the property value (BCM 12, `runbook.py disklayouts`) | **still unverified** — marked | First contact: apply to the 03.4 collection node, then `get disksetup` must print the XML itself, not the path. |
| 9 | `interfaces ; remove bootif` drops the boot flag (BCM 03.5) | **still unverified** — marked | First contact: on the first dual-NIC node, `interfaces ; list` before and after. |
| 10 | `cm-runai-setup` applies `node-role.kubernetes.io/runai-system` (BCM 06) | **no longer load-bearing** | 06.2 now applies the label explicitly before the installer (`kubectl label node … runai-system=true --overwrite` plus a read-back), so nothing depends on the wizard doing it. |
| 11 | "Expect: driver 580.95.05, CUDA 13.0" (BCM 04.4) | **verified** (the pairing) | CUDA 13.0 Update 2 requires Linux driver ≥ 580.95.05 — NVIDIA CUDA 13.0.2 release notes, Table 3 (cited in BCM 08.5 since M6). The 580.95.05 figure is the track's chosen baseline; 04.4 records the actuals. |
| 12 | "BCM forces a password change on first login" (BCM 15) | **still unverified** — marked | 15.3's test login proves it or disproves it; if no change is forced, users run `passwd` at once and 10 records it. |
| 13 | BF3 NIC mode: the five `INTERNAL_CPU_*` settings (Cerberus 00a §5) | **corrected** | NVIDIA "BlueField Modes of Operation" (https://docs.nvidia.com/doca/archive/2-9-0-cx8/bluefield-modes-of-operation.pdf), "Changing Mode › Using External Host Command Line": "For BlueField-3: `mlxconfig -d /dev/mst/<device> s INTERNAL_CPU_OFFLOAD_ENGINE=1`". The PAGE_SUPPLIER / ESWITCH_MANAGER / IB_VPORT0 set is listed for **BlueField-2**; `INTERNAL_CPU_MODEL` appears in neither. The block now sets the one parameter; "power cycle is recommended" is quoted. Query: `q INTERNAL_CPU_OFFLOAD_ENGINE` → `DISABLED(1)` = NIC mode (same document). |
| 14 | "the August 2026 cautionary runbook: ~16 stranded staging subvolumes, ~76 GiB" (Cerberus 03) | **corrected** | The cited document is another engagement's and is not in the library, so the figures can't be checked. They were removed; the lesson stays (audit CephFS staging subvolumes from the Ceph side). |
| 15 | "Marked unverified in research-findings" (`proxmox-ceph/docs/references.md`) | **corrected** | `research-findings` is not in the repo. The line now carries the library's own **[unverified]** marker and points here (StorCLI: no public Broadcom source; confirm on-box). |
| 16 | "`scripts/check.sh` passing" (BCM README) | **verified** | Both gates pass on a full run — re-check with `bcm/scripts/check.sh` and `proxmox-ceph/scripts/check.sh`. The leak-scan stage checks a real deny-list since r13. |

## B. Added by later briefs

### REPORT-M3 ("not verified")

| # | Claim | Status | How it clears |
|---|---|---|---|
| M3-1 | `cm-kubernetes-setup` asks for service/pod ranges as separate network + netmask fields (BCM 05) | **still unverified** — marked | First contact (the 05 dialog). 05 now also gives the single-CIDR form, so the values work either way. |
| M3-2 | The Cerberus node scripts' non-dry-run paths (`preflight.sh`, `net-verify.sh`, `backup-drill.sh`) | **still unverified** | First run on real nodes: run each with `--dry-run` first, then for real; any difference from the dry-run plan goes in a deviation note. |
| M3-3 | SR650 V4 by-id naming; whether the B550i-2i RAID1 boot volume appears as an `nvme*` device | **still unverified** (hardware) | 00a §4 on the first node: record `ls -l /dev/disk/by-id/`. The 03 Step 5 guard and `preflight.sh` [6] refuse any device with partitions or holders, so a boot volume that shows up as NVMe is refused, not wiped. |

### REPORT-M4 (unverified specifics, ruled into the register)

| # | Claim | Status | How it clears |
|---|---|---|---|
| M4-1 | 03.4 install-mode commands: `set nextinstallmode full`, `reboot -n` | **still unverified** — marked | BCM first-contact block. |
| M4-2 | Appending `AdvancedConfig = { "DeviceResolveAnyMAC=1" }` at the end of `cmd.conf` is equivalent to editing the commented example (BCM 02) | **still unverified** — marked | First head node: after `systemctl restart cmd`, confirm MAC-based resolution (a node with a swapped NIC is identified). |
| M4-3 | Piping commands into `cmsh` on stdin behaves like an interactive session (BCM 03.6 `bmcsettings`, 15, `generate-users.py`) | **still unverified** — marked | BCM first-contact block, with a harmless read (below). |
| M4-4 | kgateway GatewayParameters: API group/version, field path, gateway pod label (BCM 06.5) | **partly verified** | Verified against the kgateway 2.5 API reference (https://kgateway.dev/docs/envoy/latest/reference/api/): `gateway.kgateway.dev/v1alpha1`; `spec.kube.podTemplate` (type `Pod`) has `affinity`, `tolerations` and `nodeSelector`. **Still unverified — marked:** the proxy-pod label `gateway.networking.k8s.io/gateway-name` (the verify step reports loudly if it selects nothing). |

### cmsh output formats (M4 slate #8) and the M4A gate prerequisites

| # | Claim | Status | How it clears |
|---|---|---|---|
| C-1 | `cmsh -c "network; list"` / `"category; list"` / `"softwareimage; list"` / `"device; list"` print the object name in the **first column** — the passes' add-or-verify checks read it with `awk '{print $1}'` | **still unverified** | BCM first-contact block. If the name isn't column 1, the existence check misses and the `add` runs, then fails with "already exists", which is treated as success with a note. Wrong output, but safe. |
| C-2 | An `add` of an existing object fails with text containing "already exists" (treated as success with a note) | **still unverified** | First contact: re-add an existing network once and read the message. The match is deliberately loose. |
| C-3 | **`cmsh -c "category ; use CAT ; get disksetup"` prints the XML** — the 03.4/03.5 fleet gate (`runbook.py layoutgate`, `provision-nodes.sh`) parses it | **still unverified — gate prerequisite**, marked in 03.5 | **On the first real head node, before the first provisioning wave.** Run it against the collection node's category; it must print the XML with `<blockdev>` lines. Until it does, a gate PASS/FAIL is not evidence. |
| C-4 | The 14.1 jumpbox guard's `who -m` / utmp assumption: under `sudo -i`/`su -`, sshd's utmp record shows the client IP in parentheses | **still unverified** — marked | On the jumpbox: SSH in, `sudo -i`, run `who -m`. If no IP is shown, the guard refuses to enable the firewall (the safe failure). Use the console path (`CONSOLE_CONFIRMED=yes`) or SSH without sudo stripping `SSH_CLIENT`. |

### Cross-track IP plan

| # | Claim | Status | How it clears |
|---|---|---|---|
| X-1 | The BCM networks + Kubernetes ranges and the Cerberus MGMT / Ceph / PBS networks don't overlap — **guarded by the BCM gate's cross-track stage** (M8) | **verified for the shipped templates — on every gate run**; a site's pair is gated when both site files exist | `bcm/scripts/check.sh` (both modes) runs `bcm/scripts/crosstrack.py`, which reads each file with its own track's parser and compares the BCM side (internalnet, storagenet, usernet, mgmtnet, the admin ranges, both K8s ranges, the DNS hosts) with the Cerberus side (MGMT, Ceph public/cluster, PBS nets, every host address, the DNS servers; one resolver shared by both tracks is allowed). It runs on the shipped templates (**0 overlaps**), on a planted overlap (self-test: Cerberus `MGMT_NET` = BCM `NET_PROVISION` must be caught), and — **when both `bcm/site-vars.sh` and `proxmox-ceph/site-vars.sh` are present** — on the site's own pair, failing the gate on any overlap. With either site file absent the site run is skipped and the gate says so. So: once both site files are filled in, run `bcm/scripts/check.sh --site` from the repo copy that holds both. |

### M5 additions (ruled 2026-11-17)

| # | Claim (where) | Status | How it clears |
|---|---|---|---|
| M5-1 | `pvesm add pbs … --password` with no value prompts for it (Cerberus 06.6) | **still unverified** — marked | pvesm(1) 9.2.14's own `pvesm add pbs` example ends in a bare `--password`, but no text says it prompts. First PBS registration settles it; 06.6 has the fallback. |
| M5-2 | `pvesh create /cluster/backup --id …` accepts a chosen job ID (Cerberus 06.6) | **still unverified** — marked | No current vendor doc found. A pve-devel patch (2021) made `id` optional and auto-generated on create. First job creation settles it; 06.6 says to drop `--id` if rejected. |
| M5-3 | NVUE bridge / VLAN / access-port syntax (Cerberus 00a §7a) | **still unverified** — marked | First switch: on-box `nv set interface swp1s0 bridge domain br_default ?` before the first run, and adjust the block in the site copy if NVUE differs. The NVUE version follows the switch's Cumulus release. |
| M5-4 | A pool quota (`max_bytes`) counts `STORED` (logical, pre-replication) data (Cerberus 04) | **still unverified** — marked | The Ceph Tentacle pools page documents `set-quota` but not what is counted. First pool: write a known amount, read `ceph df detail` (`QUOTA BYTES` beside `STORED`). |
| M5-5 | knet link 1 appears on a corosync reload without a restart (Cerberus 01 §7) | **still unverified** — marked | §7's own check (`corosync-cfgtool -s` shows `LINK ID 1`); restart path documented if not. |
| M5-6 | `corosync-cfgtool -s` wording (`LINK ID n`, `connected`) (Cerberus 01, 03 gate) | **still unverified** — marked | First cluster: read the real output and adjust the greps if needed. |
| M5-7 | HA lock and watchdog timings — drill (e)'s 90 s abort point (Cerberus 05, 07) | **partly verified** | ha-manager(1) 9.2.14: the watchdog reboots the node "after 60 seconds"; "typical error detection and failover times of about 2 minutes". The exact 9.2 lock intervals stay **[unverified]** (07); drill (e) measures them. |
| M5-8 | MFT and StorCLI install and run on PVE 9.2 / Debian 13 (Cerberus 00a) | **still unverified** — marked | First node, phase B (Cerberus 01 §3g). |
| M5-9 | Debian's `mstflint` carries the BF3 mode parameter (Cerberus 00a) | **still unverified** — marked; **narrowed** | Only `INTERNAL_CPU_OFFLOAD_ENGINE` is needed now (item 13). First node: `mstconfig -d … q INTERNAL_CPU_OFFLOAD_ENGINE`. |
| M5-10 | OneCLI takes BMC credentials only on argv (Cerberus 00a §2b) | **still unverified** — marked | Lenovo OneCLI reference for the installed version; until then, run it from a single-user session (as 00a says). |
| M5-11 | `ha-manager config` lists resources as `vm: ID` (Cerberus 05 Step 2) | **corrected** | ha-manager(1) 9.2.14 example output shows `vm:100` (no space), while `resources.cfg` writes `vm: 501`. The check now accepts both forms. |
| M5-12 | Backfill duration 0.5–3 h for one OSD at planned fill (Cerberus 07 drill a) | **still unverified** — marked (estimate) | Drill (a) measures it; record the number in the sign-off. |

### M6 candidates (ruled into the register, M7 ruling #2)

| # | Claim (where) | Status | How it clears |
|---|---|---|---|
| M6-1 | `cmsh` reads `bmcsettings` commands from stdin (BCM 03.6) | **still unverified** — marked | Same as M4-3; the interactive fallback is in the text. |
| M6-2 | `kubectl create secret … --from-file=…=/dev/stdin` (BCM 08.5 burn-in pull secret) | **still unverified** — marked | First use: `kubectl get secret … -o jsonpath` shows a non-empty `.dockerconfigjson`. |
| M6-3 | The Run:ai PostgreSQL pod has `sh` and `cat` (BCM 06.7) | **still unverified** — marked | First backup: the dump is non-empty (`test -s`, already in the block). |
| M6-4 | `ibstat` prints `Rate: 800` for an XDR port (BCM 16.1b) | **still unverified** — marked | First B300. |
| M6-5 | `nvcr.io/nvidia/pytorch:25.11-py3` runs kernels on B300 (`sm_103`) (BCM 08.5) | **still unverified** — marked | NVIDIA's support matrix names no B300. The burn-in prints the device capability and the container's arch list, and stops with a clear message if the first kernel fails. |

### New in M7

| # | Claim (where) | Status | How it clears |
|---|---|---|---|
| M7-1 | CMDaemon keeps database backups in `/var/spool/cmd/backup/` (BCM 11.1) | **still unverified** — marked | BCM 11 Administrator Manual ("backups") on first contact; `ls -l` on the first head node. |
| M7-2 | The cluster can pull the `postgres` image, and the Run:ai dump restores into a stock image (BCM 11.1 test-restore) | **still unverified** — marked | The first test-restore: exit 0 and a non-zero table count. |
| M7-3 | `qm snapshot` on the head-node VM (BCM 11.2) | **verified** (command) | `qm snapshot <vmid> <snapname> [--description]`, `qm rollback`, `qm listsnapshot` — qm(1). The storage must support snapshots (Ceph RBD does). |

## BCM first-contact block

Run on the first BCM 11 head node, as root, **before the first
provisioning wave**. Every line only reads. Paste the output into
section 10 and update rows 7, C-1, C-3, M4-1 and M4-3 above — rows 8
and C-2 require a write (applying a disksetup; re-adding an existing
object), so they clear by their own rows' procedures.

```bash
cmsh -c "network; list"                              # C-1: is the name column 1?
cmsh -c "network; use internalnet; show" | grep -Ei 'base|netmask|management'   # item 7: property names
cmsh -c "category; list"                             # C-1
cmsh -c "category; use ${CAT_GPU:?source site-vars.sh}; get disksetup" | head -20   # C-3: XML (with blockdev lines), not a path
printf 'network\nlist\n' | cmsh -q                   # M4-3: stdin behaves like a session (same list as above)
cmsh -c "device; use ${BCM_HEADNODE:?source site-vars.sh}; show" | grep -i installmode   # M4-1: property name
```
