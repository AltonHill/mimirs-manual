# 00a — Physical bring-up verification

**Goal:** prove the baremetal is built correctly *before* any software goes on.
No vendor integration kit exists for this build (no EveryScale) — the builder racks
and cables everything, so this section owns the full physical verification.

**Ordering — two phases (no OS exists before 01):**

| Phase | Sections | Runs where | When |
|---|---|---|---|
| A — before any OS | §1 rack/power, §2 firmware, §6 cabling, §7 switches, §8 switch-side links | at the rack, the XCC web UI, OneCLI **out-of-band** from an admin host, the switch consoles | before 01 |
| B — on-host hardware checks | §3 940-8i JBOD, §4 NVMe direct-to-CPU, §5 BlueField-3 NIC mode | **on the freshly installed PVE OS** of each node (Debian 13 underneath), with StorCLI and NVIDIA MFT installed there | 01 §3g sends you here — after the PVE install + upgrade, **before** `pvecm create` (01 §4) |

Why phase B runs on the installed OS: §3–§5 need `storcli`, `lspci` and
`mst`/`mlxconfig`, and before 01 there is no OS to run them in. The PVE
install touches only the M.2 boot volume behind the B550i-2i (01 §2 — never a
PM1743), so installing first and verifying before clustering loses nothing;
and the BF3 mode change (§5) power-cycles the node and can rename its
interfaces, which is why it must land **before** 01 §4 and 02.
Tools for phase B (install on each node's PVE OS; versions **[unverified]** —
confirm against the vendor download pages at build time):
- **StorCLI** for the ThinkSystem RAID 940-8i (Broadcom StorCLI, from Lenovo
  support for the adapter) — the `storcli` binary used in §3/§4;
- **NVIDIA MFT** (Mellanox Firmware Tools: `mst`, `mlxconfig`, `mlxfwreset`)
  for Debian 13 — its kernel-module build against the PVE 9.2 kernel is
  **[unverified]**. Debian's own `mstflint` (`mstconfig`, `mstfwreset`) is the
  in-distro fallback; whether it carries the BlueField-3 `INTERNAL_CPU_*`
  parameters is **[unverified]**.

Several steps are **MANUAL-ONLY** (firmware flashes, physical cabling) — they
cannot be scripted and must be done by a person at the rack or in the XCC web
UI.

**Conventions:** `MANUAL-ONLY` = a human does this step by hand. `DESTRUCTIVE` =
data loss is possible; double-check the target first. `STOP` = halt the build,
do not proceed until resolved.

Every block that needs site values sources the site file itself, through
`CERBERUS_TRACK` (track README, "Working-directory contract"), as one
fail-closed `( set -euo pipefail … )` unit. Switch blocks (§7) are pasted from
the **rendered** guide, which carries the real values; pasted from the
template, `set -u` stops them at the first unfilled name. No command here
references a value that nothing has assigned — interface names are resolved
in 02 §1, and the host-side link checks live there.

Reference: [docs/references.md](../../docs/references.md) — Lenovo (XCC2, OneCLI,
SR650 V4 product guide), NVIDIA (NVUE, BlueField modes), StorCLI notes.

---

## 1. Rack & power check — MANUAL-ONLY

1. Verify each node is racked with both 1300W PSUs seated, and **both power cords
   connected to independent feeds** (N+N redundancy is a design assumption).
2. Confirm airflow: front intake / rear exhaust unobstructed; blanking panels in
   empty RU above/below.
3. Connect the XCC management port on each node to the mgmt network (SN2201).
   Set the XCC IPs from `site-vars.sh` (`NODE*_XCC_IP`) in the XCC web UI or via
   BIOS setup — MANUAL-ONLY.
4. Power on. Verify via XCC that both PSUs report healthy and no critical
   hardware events are logged (XCC → Events).

**Node-4 callout:** repeat this whole section for the 4th node if/when it is added.

---

## 2. XCC/BMC firmware baseline — MANUAL-ONLY

**Rule: update XCC/BMC firmware FIRST, then UEFI.** Wrong order causes incorrect
behavior (Lenovo XCC2 documented ordering).

### 2a. Web flow (XCC2)

Per node, in the XCC web UI (https://`<NODE*_XCC_IP>`):

1. BMC → Firmware Update → **System Firmware** | **Adapter Firmware** | **PSU Firmware** |
   **Update from Repository**.
2. Click **Update Firmware** → **Browse** (select the update bundle file) → **Next**
   (upload + verify) → select the target device → **Update** → **Finish**.
3. Do the XCC/BMC bundle first, let it complete, then UEFI, then adapters/PSU.

Notes:

- **Update-from-Repository** (CIFS/NFS/HTTPS staging) and onboard firmware history
  require an **XCC Platinum license**. Without it, use the Browse/upload flow or
  OneCLI below.
- Record the final firmware levels per node (XCC, UEFI, BF3, 940-8i, PSU) in the
  build log — they are the baseline for all future maintenance.

### 2b. OneCLI flow (CLI alternative)

OneCLI (`onecli` on Linux) can flash in-band (run on the host OS) or out-of-band
(toward the XCC). Download the UXSP bundle for the SR650 V4 machine type first.

First, put the XCC password into **this shell only** (never a file, never a
command you type in full — `read -s` keeps it out of history):

```bash
read -rsp "XCC password for $XCC_USER (not echoed): " XCC_PASSWORD; echo; export XCC_PASSWORD   # paste: session
```

(`XCC_USER` comes from the site file — source it in this shell first if it is
not already: `source "$CERBERUS_TRACK/site-vars.sh"   # paste: session`.)

Inventory and compare, every node (read-only toward the XCCs):

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars XCC_USER NODE1_XCC_IP NODE2_XCC_IP NODE3_XCC_IP
  cerberus_require_secret XCC_PASSWORD
  PKG_DIR=./packages      # the downloaded UXSP bundle for the SR650 V4 machine type
  for XCC_IP in "$NODE1_XCC_IP" "$NODE2_XCC_IP" "$NODE3_XCC_IP"; do
    echo "== $XCC_IP: installed vs latest =="
    onecli update compare --scope latest --bmc "$XCC_USER:$XCC_PASSWORD@$XCC_IP" \
      --dir "$PKG_DIR" --output "./output/$XCC_IP"
  done
)
```

Flash out-of-band — **MANUAL-ONLY, one node at a time** (XCC bundle first,
then UEFI, then adapters; let each finish). Set `FLASH_NODE` to the node you
are flashing; the block refuses anything else:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars XCC_USER
  cerberus_require_secret XCC_PASSWORD
  case "${FLASH_NODE:?export FLASH_NODE=1, 2 or 3 — the node to flash}" in
    1) XCC_IP="$NODE1_XCC_IP" ;; 2) XCC_IP="$NODE2_XCC_IP" ;; 3) XCC_IP="$NODE3_XCC_IP" ;;
    *) echo "FATAL: FLASH_NODE=$FLASH_NODE is not 1, 2 or 3" >&2; exit 1 ;;
  esac
  PKG_DIR=./packages
  # Out-of-band payload delivery may need an SFTP server on some OneCLI
  # versions (add: --sftp SERVER) — [unverified] for this machine type.
  onecli update flash --bundle --dir "$PKG_DIR" --bmc "$XCC_USER:$XCC_PASSWORD@$XCC_IP"
)
```

Staged update across a maintenance window (same node selection):

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars XCC_USER
  cerberus_require_secret XCC_PASSWORD
  case "${FLASH_NODE:?export FLASH_NODE=1, 2 or 3 — the node to stage}" in
    1) XCC_IP="$NODE1_XCC_IP" ;; 2) XCC_IP="$NODE2_XCC_IP" ;; 3) XCC_IP="$NODE3_XCC_IP" ;;
    *) echo "FATAL: FLASH_NODE=$FLASH_NODE is not 1, 2 or 3" >&2; exit 1 ;;
  esac
  BMC="$XCC_USER:$XCC_PASSWORD@$XCC_IP"
  onecli update flash --bundle --dir ./packages --bmc "$BMC" --applytime OnStartUpdateRequest
  onecli update checktask --bmc "$BMC"      # note the task ID it reports
)
# then, when the window opens (same FLASH_NODE), start — or cancel by task ID:
#   onecli update startstaged --bmc "$XCC_USER:$XCC_PASSWORD@$XCC_IP"
#   onecli update canceltask --taskid "$ONECLI_TASK_ID" --bmc "$XCC_USER:$XCC_PASSWORD@$XCC_IP"
```

In-band alternative (run on the node's own OS once it exists — phase B; no
`--bmc`, no password): `onecli update flash --bundle --dir ./packages`.
Fleet flash from one JSON task file: `onecli multiupdate flash --configfile
multi_task.json` (the task file holds BMC credentials — keep it out of the
repo and delete it after use).

> **Credential exposure, stated honestly:** OneCLI takes the BMC login inside
> `--bmc user:password@host`, so the password is in that process's argument
> list (visible in `ps` on the admin host) while it runs. Whether this OneCLI
> accepts it any other way is **[unverified]**. Run it from a single-user
> admin host; `read -s` keeps it out of shell history and the site file.

Change the factory XCC login (`USERID` / factory password) on first boot —
MANUAL-ONLY — and record the new login name as `XCC_USER` in `site-vars.sh`
(the render report flags it while it is still `USERID`); the password goes in
the site's password manager, never a file in the repo. Reference: Lenovo pubs
`onecli_r_flash_command`, `xcc2/updating_firmware_*` (see docs/references.md).

---

## 3. RAID 940-8i — JBOD verification (phase B: on the installed PVE OS)

The 940-8i must expose drives raw. **No virtual drives may exist** — Ceph's hard
requirement is raw disks with no virtual-drive layer and no controller caching in
the path. (JBOD ≠ HBA: JBOD still passes through controller firmware, which is
fine for Ceph.)

> The 2x 960GB M.2 OS drives sit behind the **B550i-2i**, a different controller.
> StorCLI against `/c0` (the 940-8i) does not touch them — do not go looking for
> them here.

```bash
storcli /c0 show          # controller info — confirm you are talking to the 940-8i
storcli /c0/pall show     # physical drives
storcli /c0/vall show     # virtual drives — MUST BE EMPTY on a Ceph node
```

- If `/c0/vall show` lists any virtual drive: **DESTRUCTIVE / MANUAL-ONLY** —
  delete it/them (confirm with the builder that nothing on them matters; these are
  fresh Ceph nodes, so normally nothing does) and re-run until empty.
- Enable JBOD mode (a reboot may be required for it to take effect):

```bash
storcli /c0 set jbod=on
# verify: drives should show state "JBOD" in the next command
storcli /c0/pall show
```

- If the controller rejects `set jbod=on`: STOP. The fallback is the HBA
  personality (`storcli /c0 set personality=hba`) **where supported** — whether the
  940-8i exposes that switch is unverified (Broadcom StorCLI reference is behind a
  support login); confirm on first boot with on-box `storcli /c0 help` before
  improvising.

---

## 4. CRITICAL — NVMe direct-to-CPU verification (STOP if wrong) (phase B)

The 2x 3.84TB Samsung PM1743 U.3 NVMe per node **must be wired direct-to-CPU at
x4**, NOT behind the 940-8i (whose NVMe U.3 support is x1 ≈ 2 GB/s per drive vs
~14 GB/s direct). This is the single highest-risk physical step: NVMe behind the
controller is a silent x1 link with controller firmware in the I/O path.

Prove it two independent ways on **every** node:

```bash
# 1) PCIe link width/speed — expect 32GT/s and x4 for each PM1743.
for addr in $(lspci -nn | grep -i nvme | awk '{print $1}'); do
  echo "== $addr =="
  lspci -vv -s "$addr" | grep -E 'LnkCap|LnkSta'
done
# PASS: LnkSta: Speed 32GT/s, Width x4   (per drive)
# FAIL: Width x1  -> the drive is behind a controller. STOP, recable.

# 2) The PM1743s must NOT appear in StorCLI at all.
storcli /c0/pall show
# PASS: no NVMe/PM1743 entries (only SAS/SATA/backplane devices, if any)
# FAIL: PM1743s listed -> they are behind the 940-8i. STOP, recable.
```

**STOP condition:** if either check fails, halt the build and have the builder
recable the U.3 backplane to the direct-to-CPU (x4) path. Do not "accept" x1 —
Ceph performance and the entire storage design assume x4.

While here, record the stable device IDs for OSD creation in §03. Use the
**by-id model_serial** names: they carry the drive's model and serial, which
is how §03's destructive wipe proves its target is a PM1743 and not the boot
volume (by-path carries neither; Ceph OSDs sit on LVM, so name persistence
across reboots does not matter for this one-time step):

```bash
for l in /dev/disk/by-id/nvme-*; do
  case "$l" in *-part*|*nvme-eui.*|*nvme-nvme.*|*_[0-9]) continue ;; esac   # aliases + partitions
  printf '%s -> %s  %s\n' "$l" "$(readlink -f "$l")" "$(lsblk -dno MODEL,SERIAL,SIZE "$(readlink -f "$l")")"
done
# Record the two PM1743 lines (model SAMSUNG MZWLO3T8…, ~3.84 TB) for THIS node
# into site-vars.sh as NODE<n>_OSD1_BYID / NODE<n>_OSD2_BYID (full
# /dev/disk/by-id/... path). The boot volume must not be among them.
```

---

## 5. BlueField-3 B3220 — Ethernet + NIC mode (phase B)

The B3220 ships from the factory in **DPU mode**. For a plain Proxmox/Ceph host
there is no benefit to DPU mode (Arm cores active, host-trusted services) — put
the card in **NIC mode** so it behaves exactly like a ConnectX adapter, with both
ports forced to Ethernet.

On each node (needs `mst` from the Mellanox/NVIDIA firmware tools):

Find the device and read the current mode (read-only):

```bash
mst start
mst status    # note the BlueField-3 device path, e.g. /dev/mst/mt41692_pciconf0 (example)
```

Then set it — MANUAL-ONLY, one block, stops at the first failure. Set `DEV` to
the path `mst status` printed (the block refuses a path that does not exist);
`mlxconfig` asks for confirmation on each `set`:

```bash
( set -euo pipefail
  DEV="${DEV:?export DEV=the BlueField-3 path from mst status, e.g. /dev/mst/mt41692_pciconf0}"
  [ -e "$DEV" ] || { echo "FATAL: $DEV does not exist — re-check mst status" >&2; exit 1; }
  # 1) Current mode: DISABLED(1) = NIC mode | ENABLED(0) = DPU mode
  mlxconfig -d "$DEV" q INTERNAL_CPU_OFFLOAD_ENGINE
  # 2) Both ports to Ethernet (1=InfiniBand, 2=Ethernet)
  mlxconfig -d "$DEV" set LINK_TYPE_P1=2 LINK_TYPE_P2=2
  # 3) NIC mode (Arm cores off) — the five-parameter recipe is [unverified]
  #    for BlueField-3 (review register #13: BF3 docs centre on
  #    INTERNAL_CPU_OFFLOAD_ENGINE); confirm against NVIDIA's BF3 "modes of
  #    operation" page before running
  mlxconfig -d "$DEV" s \
    INTERNAL_CPU_MODEL=1 \
    INTERNAL_CPU_PAGE_SUPPLIER=1 \
    INTERNAL_CPU_ESWITCH_MANAGER=1 \
    INTERNAL_CPU_IB_VPORT0=1 \
    INTERNAL_CPU_OFFLOAD_ENGINE=1
  # 4) Firmware reset; then a full AC power cycle via XCC / rack PDU
  mlxfwreset -d "$DEV" r
)
```

Verify after the power cycle (same `DEV`):

```bash
mlxconfig -d "$DEV" q LINK_TYPE_P1                  # expect ETH(2)
mlxconfig -d "$DEV" q INTERNAL_CPU_OFFLOAD_ENGINE   # expect DISABLED(1) = NIC mode
```

The per-port link check (`ethtool` on the BF3 ports) needs the kernel
interface names, which are resolved in **02 §1** — it runs there.

Reference: NVIDIA DOCA "BlueField modes of operation" (see docs/references.md).

---

## 6. Cabling audit — MANUAL-ONLY

Audit every cable against
[docs/diagrams/cable-diagram.svg](../../docs/diagrams/cable-diagram.svg) **and
this plan** — the two agree (see the note below the table). Fill the
"Verified" column per site and sign it off. Port names come from the site
file (`SWITCH_*_DAC_PORT`, `PBS_SWITCH_*_PORT`); verify them against each
switch's `ports.conf` (§7a).

| # | From | To | Cable | Carries | Required | Verified |
|---|---|---|---|---|---|---|
| F1 | node-1 BF3 P1 | sn5610-a `${SWITCH_A_DAC_PORT}` leg 1 (`s0`) | breakout DAC-1 | bond0: Ceph public + cluster, VM, **corosync link1**, PBS VLAN | yes | |
| F2 | node-2 BF3 P1 | sn5610-a `${SWITCH_A_DAC_PORT}` leg 2 (`s1`) | breakout DAC-1 | same | yes | |
| F3 | node-3 BF3 P1 | sn5610-a `${SWITCH_A_DAC_PORT}` leg 3 (`s2`) | breakout DAC-1 | same | yes | |
| F4 | spare (node-4 BF3 P1) | sn5610-a `${SWITCH_A_DAC_PORT}` leg 4 (`s3`) | breakout DAC-1 | same, once node-4 exists | node-4 only | |
| F5 | node-1 BF3 P2 | sn5610-b `${SWITCH_B_DAC_PORT}` leg 1 (`s0`) | breakout DAC-2 | bond0 backup path (incl. **corosync link1**) | yes | |
| F6 | node-2 BF3 P2 | sn5610-b `${SWITCH_B_DAC_PORT}` leg 2 (`s1`) | breakout DAC-2 | same | yes | |
| F7 | node-3 BF3 P2 | sn5610-b `${SWITCH_B_DAC_PORT}` leg 3 (`s2`) | breakout DAC-2 | same | yes | |
| F8 | spare (node-4 BF3 P2) | sn5610-b `${SWITCH_B_DAC_PORT}` leg 4 (`s3`) | breakout DAC-2 | same, once node-4 exists | node-4 only | |
| P1 | PBS host fabric NIC port 1 | sn5610-a `${PBS_SWITCH_A_PORT}` | per the PBS NIC (BOM item) | **PBS backup VLAN** (access, VLAN `${PBS_VLAN}`) | yes | |
| P2 | PBS host fabric NIC port 2 | sn5610-b `${PBS_SWITCH_B_PORT}` | per the PBS NIC (BOM item) | PBS backup VLAN (redundant path) | yes (one-port NIC: P1 only, a recorded SPOF) | |
| M1 | node-1 OCP port 1 | SN2201 port 1 | Cat6 | mgmt + **corosync link0** | yes | |
| M2 | node-1 XCC | SN2201 port 2 | Cat6 | XCC/BMC | yes | |
| M3 | node-2 OCP port 1 | SN2201 port 3 | Cat6 | mgmt + **corosync link0** | yes | |
| M4 | node-2 XCC | SN2201 port 4 | Cat6 | XCC/BMC | yes | |
| M5 | node-3 OCP port 1 | SN2201 port 5 | Cat6 | mgmt + **corosync link0** | yes | |
| M6 | node-3 XCC | SN2201 port 6 | Cat6 | XCC/BMC | yes | |
| M7 | PBS host mgmt port | SN2201 port 7 | Cat6 | PBS admin only (no backup traffic) | yes | |

**Both corosync links are required** (00-overview assumption 11): link0 rides
M1/M3/M5; link1 rides the bond (F1–F3 + F5–F7). A node missing either is not
ready for 01 §7.

**Plan vs. diagram (M23):** the diagram was right and the previous version of
this table was wrong. It showed DAC-1 leg 2 landing on node-1 and said a
*second* DAC pair was needed for a 4th node. In fact one breakout DAC per
switch serves four nodes: leg *n* → node-*n*, leg 4 spare for node-4 —
2 DACs × 4 legs = 8 legs = 4 nodes × 2 links, the same arithmetic as
`docs/runbook-starting-point.md`. The diagram does not yet show the PBS
fabric ports (P1/P2) by name or both corosync links as required — listed for
the diagram regeneration (Brief M6).

Rules to enforce:

- **Each node's two 200G links land on DIFFERENT switches** (P1 → sn5610-a,
  P2 → sn5610-b). This is what makes the active-backup bond in §02 survive a
  switch failure — and, with link1 on that bond, what keeps corosync alive
  through a mgmt-switch failure.
- **The PBS host's backup traffic never rides the SN2201.** Its fabric NIC
  (P1/P2) carries every backup; its Cat6 (M7) is admin only.
- 2–4x Cat6 per node from the OCP 1GbE ports → SN2201 mgmt switch (BOM ships
  1/server; add per site policy).
- Label both ends of every DAC leg and Cat6 run. A mislabeled breakout leg is a
  future outage.

**Node-4 callout:** the 4th node uses leg 4 (`s3`) of the **same** two DACs
(F4, F8) — no second DAC pair is needed. Audit its legs the same way and add
its OCP/XCC Cat6 runs to the SN2201.

---

## 7. Switch baseline (Cumulus 5.x, NVUE)

Do this during bring-up only — changing breakout/MTU reloads `switchd` and resets
ports. Never on a live fabric.

### 7a. SN5610-A and SN5610-B (800GbE fabric)

On each SN5610 (SSH/console), first look — read-only. Confirm which ports
are 800G-capable on THIS switch (do not assume), and that the DAC and PBS
ports in the site file exist:

```bash
cat /etc/cumulus/ports.conf
nv show interface
```

Then configure — one fail-closed block, pasted from the **rendered** guide
(it carries the real port names and VLAN IDs). It works out which switch it
is on from the hostname and refuses anything else:

```bash
( set -euo pipefail
  : "${FABRIC_SWITCH_A}" "${FABRIC_SWITCH_B}" "${SWITCH_A_DAC_PORT}" "${SWITCH_B_DAC_PORT}" \
    "${PBS_SWITCH_A_PORT}" "${PBS_SWITCH_B_PORT}" "${FABRIC_MTU}" \
    "${CEPH_PUBLIC_VLAN}" "${CEPH_CLUSTER_VLAN}" "${PBS_VLAN}"
  case "$(hostname)" in
    "${FABRIC_SWITCH_A}") PORT="${SWITCH_A_DAC_PORT}"; PBSPORT="${PBS_SWITCH_A_PORT}" ;;
    "${FABRIC_SWITCH_B}") PORT="${SWITCH_B_DAC_PORT}"; PBSPORT="${PBS_SWITCH_B_PORT}" ;;
    *) echo "FATAL: hostname $(hostname) is neither ${FABRIC_SWITCH_A} nor ${FABRIC_SWITCH_B}" >&2; exit 1 ;;
  esac
  VLANS="${CEPH_PUBLIC_VLAN},${CEPH_CLUSTER_VLAN},${PBS_VLAN}"
  # 1) Break out the OSFP port carrying the node DACs: 1x800G -> 4x200G
  #    (new-format value is "4x", not "4x200G"). Legs s0-s2 = node-1..3, s3 = node-4/spare.
  nv set interface "$PORT" link breakout 4x
  nv set interface "${PORT}s0-3" link state up
  # 2) MTU: Cumulus default is already 9216 — this re-asserts it; never below 9000.
  nv set interface "${PORT}s0-3" link mtu "${FABRIC_MTU}"
  # 3) The node legs trunk the three VLANs tagged: Ceph public, Ceph cluster, PBS backup.
  nv set bridge domain br_default vlan "$VLANS"
  nv set interface "${PORT}s0-3" bridge domain br_default vlan "$VLANS"
  # 4) The PBS host's fabric port: access (untagged) on the PBS VLAN. If it is
  #    a breakout leg (swpNsM), break out its parent port first.
  case "$PBSPORT" in *s[0-9]*) nv set interface "${PBSPORT%s*}" link breakout 4x ;; esac
  nv set interface "$PBSPORT" link state up
  nv set interface "$PBSPORT" link mtu "${FABRIC_MTU}"
  nv set interface "$PBSPORT" bridge domain br_default access "${PBS_VLAN}"
  # 5) Apply (auto-save is ON by default -> /etc/nvue.d/startup.yaml); save explicitly too.
  nv config apply
  nv config save
)
```

- The bridge/VLAN/access syntax in steps 3–4 is **[unverified]** for the
  switch's shipped Cumulus 5.x — confirm with on-box `nv set interface swp1s0
  bridge domain br_default ?` before the first run, and adjust the block in
  the site copy if NVUE differs.
- Force 200G only if autonegotiation misbehaves:
  `nv set interface swp1s0-3 link speed 200G` (by hand, per leg range).

NVUE supports `?`, `-h` and tab completion (`nv sh int` works).

> The 3rd (spare) SN5610 is out of scope: leave it unconfigured/powered per site
> policy and document its state.

### 7b. SN2201 (1GbE management)

Baseline only — bring up the ports facing the nodes' OCP/XCC links and the
PBS host's mgmt port (cable plan M1–M7; `MGMT_SWITCH_PORTS` in the site file).
Paste from the rendered guide:

```bash
( set -euo pipefail
  : "${MGMT_SWITCH_PORTS}"
  nv set interface "${MGMT_SWITCH_PORTS}" link state up
  nv config apply
  nv show interface
)
```

MTU stays at the 1500 default on the mgmt switch (mgmt network is MTU 1500).

Reference: NVIDIA Cumulus "Switch Port Attributes" and "NVUE CLI" docs
(see docs/references.md).

---

## 8. Link verification — switch side

Before any OS: prove the links from the switches, which need nothing
resolved. On each SN5610 and on the SN2201 (read-only):

```bash
nv show interface    # PASS: node legs s0-s2 up at 200G (s3 too with node-4), PBS port up; SN2201 ports up at 1G
```

The host side — `ethtool` on the BF3 and mgmt ports, end-to-end MTU 9000 —
needs the kernel interface names and the §02 interfaces, so it runs in
**02 §1** (link speeds, right after the names are resolved) and **02 §5**
(MTU ping on every VLAN). Any failure there is a cabling/MTU fault — fix it
before §03.

---

## 00a acceptance checklist

Phase A (before 01):

- [ ] Rack, N+N power, XCC reachable on all nodes (§1)
- [ ] XCC/BMC firmware flashed first, then UEFI/adapters; levels recorded; factory XCC login changed, `XCC_USER` recorded (§2)
- [ ] Cable plan filled and signed off — every row, incl. the PBS fabric ports and both corosync links (§6)
- [ ] SN5610-A/B breakout + MTU 9216 + VLAN trunk (Ceph ×2 + PBS) and PBS access port applied and verified; SN2201 ports up (§7)
- [ ] Switch-side links up at speed (§8)

Phase B (on the installed PVE OS, from 01 §3g, before `pvecm create`):

- [ ] `storcli /c0/vall show` empty; `set jbod=on` applied and verified (§3)
- [ ] PM1743s at PCIe x4/32GT/s **and** absent from StorCLI on every node; by-id names recorded as `NODE<n>_OSD1_BYID`/`_OSD2_BYID` (§4)
- [ ] BF3 in NIC mode, both ports Ethernet, host power-cycled (§5) — link speeds are checked in 02 §1
