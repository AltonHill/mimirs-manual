# 03 — Software Images, Categories, Nodes & IPMI

> **Prerequisites:** 02 complete (licensed, upgraded head node; BCM
> networks created in 2.5).
>
> **Order inside this section:** 3.1–3.3 build categories and images;
> **3.4 provisions one node of each host type by hand and verifies its
> disk layout — the gate**; only then does 3.5 provision the fleet. This
> is the one deliberate exception to "work in order, one node type at a
> time": the first node of each type goes ahead of everyone else.
>
> **At scale (10+ nodes):** don't hand-write the commands below — use
> `scripts/generate-nodes.py` with a CSV inventory (see 3.0), or the
> `images` / `nodes` passes of `scripts/runbook.py` (see README). The
> manual commands in 3.1–3.6 remain the reference for what the tooling
> generates. Every pass and the generated provisioning script are
> re-runnable: an object that already exists is a NOTE, never an abort.

## 3.0 — Node provisioning from inventory (recommended at scale)

```bash
# 1. Emit a skeleton CSV (example: 3 ctrl + 4 workers + 74 GPUs):
scripts/generate-nodes.py --skeleton --ctrl 3 --workers 4 --gpus 74 > inventory.csv

# 2. Fill in real hostnames, IPs, MACs, BMC IPs in your spreadsheet,
#    export as CSV. Columns in order:
#    hostname,ip,mac,role,category,interface,bmc_ip
#    role is ctrl | worker | gpu ; bmc_ip empty for non-GPU nodes.

# 3. Validate (catches duplicate hostname/IP/MAC, bad fields):
scripts/generate-nodes.py --check inventory.csv

# 4. Generate the provisioning script and review it:
scripts/generate-nodes.py inventory.csv > provision-nodes.sh
bash -n provision-nodes.sh
less provision-nodes.sh   # review every command before running
# 5. Run it on the head node ONLY after the 3.4 gate has passed for every
#    host type in the inventory. The script checks that itself: before
#    its first add it runs runbook.py layoutgate over every category and
#    stops unless each carries a by-path layout:
#    bash provision-nodes.sh
```

`--check` refuses an unfilled skeleton (its placeholder MACs
`00:50:56:b3:xx:xx` are recognized). The skeleton follows the variables
and 01: hostnames in the `$GPU_WORKERS` form, control plane from `.50`,
GPU workers from `.53`, BMC addresses bounds-checked against `$NET_OOB`.

## 3.1 — Create categories

One category per node type. DGX A/H/B families are interchangeable in
this pattern — one category covers all DGX nodes of the same image. Add
a category per HGX OEM type or UCS as needed.

```bash
cmsh -c "category; add $CAT_CP ; commit"    # Run:ai control plane
cmsh -c "category; add $CAT_WRK ; commit"   # workers (VM or CPU)
cmsh -c "category; add $CAT_GPU ; commit"   # GPU workers (e.g. runai-dgx-cat)
# Additional GPU types, same pattern:
# cmsh -c "category; add runai-hgx-dell-cat ; commit"
# cmsh -c "category; add runai-ucs-cat ; commit"
```

> **Field note:** the lab used four categories (`runai-cp-cat`,
> `runai-wrk-cat`, plus one per GPU type) — one per GPU type is the
> pattern to keep. DGX vs HGX-8GPU (Dell/HPE/Lenovo) vs Cisco UCS GPU
> nodes differ only in category, image, and interface names — the steps
> are identical.

## 3.2 — Clone software images

One image per GPU node type (DGX families share one; HGX/UCS get their own):

```bash
cmsh -c "softwareimage; clone default-image $IMG_CP ; commit"
cmsh -c "softwareimage; clone default-image $IMG_WRK ; commit"
cmsh -c "softwareimage; clone dgx-image $IMG_GPU ; commit"
# HGX/UCS types: clone from the matching base image instead of dgx-image
# cmsh -c "softwareimage; clone default-image runai-hgx-dell-image ; commit"
```

Wait for each clone's ramdisk to generate (watch in `cmsh` — look for
"Initial ramdisk ... was generated successfully") before continuing.

Assign images to categories:

```bash
cmsh -c "category; use $CAT_CP ; set softwareimage $IMG_CP ; commit"
cmsh -c "category; use $CAT_WRK ; set softwareimage $IMG_WRK ; commit"
cmsh -c "category; use $CAT_GPU ; set softwareimage $IMG_GPU ; commit"
```

## 3.3 — Update images

For each image, chroot in and update:

```bash
cm-chroot-sw-img /cm/images/$IMG_CP
apt update && apt update && apt upgrade -y
exit
# repeat for $IMG_WRK and $IMG_GPU
```

On GPU images only, enable the NVIDIA stack:

```bash
cm-chroot-sw-img /cm/images/$IMG_GPU
systemctl enable nvidia-fabricmanager nvidia-persistenced nvidia-dcgm
apt install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=containerd
exit
```

Pin the kernel per image, add the Mellanox module on GPU images (before
any node boots from them — 3.4's collection nodes are the first):

```bash
cmsh -c "softwareimage ; use $IMG_CP ; set kernelversion $KERNEL_VER ; commit"
cmsh -c "softwareimage ; use $IMG_GPU ; set kernelversion $KERNEL_VER ; commit"
cmsh -c "softwareimage; use $IMG_GPU; kernelmodules; add mlx5_core; commit"
```

> **Field note:** the lab notes also list enabling the NVIDIA DGX apt
> repo (`repo.download.nvidia.com/.../dgx-repo-files.tgz`) — commented
> out in the lab run. Uncomment if the image needs DGX-specific packages.

## 3.4 — Disk layout per host type: collect, build, verify (the fleet gate)

> **Gate:** no node of a host type is provisioned in 3.5 — and
> `provision-nodes.sh` from 3.0 does not run — until that type's
> **by-path** disk layout is built here, verified on one node, compared
> against a second node, and promoted to its category. Each host type
> (control plane, CPU workers, every GPU model) gets one *collection
> node*, provisioned by hand ahead of the fleet. Reference and
> rationale: [12](12-disk-layouts.md).
>
> **The gate is mechanical, and fails closed.** `scripts/runbook.py
> nodes --exec` and the generated `provision-nodes.sh` both read every
> inventory category's `disksetup` from BCM before the first `add`
> (`runbook.py layoutgate`, the same validator as below), and also the
> **device-level** `disksetup` of every inventory node that already
> exists in BCM — a device-level layout overrides its category, so
> provisioning would honor it. They refuse — non-zero exit, naming the
> category or device and the offending values, no node added — if any
> device is not a `/dev/disk/by-path/` value, or if a category has **no
> layout set** (nothing verified means not gated). A node with no
> device-level layout, or not yet defined, inherits its category's.
>
> **Why — field finding, DGX A100 install:** BCM's stock disksetup XMLs
> name drives by kernel device path (`/dev/nvme0n1`). One failing NVMe
> drive took a different time to initialize on each boot because of its
> I/O errors, so the kernel's `nvmeXnY` numbering changed from reboot to
> reboot. Dropping the bad drive from the kernel-path layout didn't hold,
> because the remaining names still moved: each reboot had about a
> **1-in-3 chance of matching the layout**. When `cm-kubernetes-setup`
> rebooted the nodes and they timed out against a disk setup their
> devices didn't match, it **cost days**. A by-path name is tied to the
> PCI slot, so it doesn't move.
>
> **Caution — field record, DGX B200 install:** BCM's `by-id` alias did
> not translate at install time (`Detecting device
> '/dev/disk/by-id/raiddisk-1': not found`), and where by-id did resolve,
> it picked the wrong disks. **by-path is the standard.** Don't use by-id
> or by-uuid: they name the drive, not the slot, so they change when a
> drive is replaced. Never use kernel names (`/dev/nvmeXnY`, `/dev/sdX`).
> `scripts/runbook.py disklayouts` refuses an XML that contains any of
> these, and `scripts/check.sh` fails on any shipped one.

> **Override — only where by-path genuinely fails in the installer.**
> If, on one host type, the node-installer cannot resolve by-path names
> (the B200 by-id failure, but for by-path), don't fall back to raw
> `cmsh` with a hand-made XML: that applies the layout outside every
> guard. Keep the case inside the tooling with the logged, named
> override:
>
> `scripts/runbook.py disklayouts … --allow-non-by-path --reason "…"`
>
> and the same two flags on the fleet gate (`runbook.py nodes`, or
> `generate-nodes.py` so the generated `provision-nodes.sh` carries
> them). Both flags are required — `--allow-non-by-path` without a
> non-empty `--reason` is refused. Every run prints a loud warning
> naming the reason and every non-by-path value it accepted, **and
> appends one line to `disk-layouts/OVERRIDES.log`** (beside the
> variables file): date, pass and mode, scope (category or device),
> the reason, and each waived value. If that line can't be written,
> the override is refused. The
> override never waives a malformed XML, an XML with no `<blockdev>`,
> or a category with no layout. Steps 5–6b still apply in full (the
> three-reboot check matters *more* on names that can move), and the
> reason goes into the deviation log (10) — `OVERRIDES.log` is the
> tooling's record, not a substitute for it. `scripts/check.sh` has no
> override: a shipped XML is by-path or the gate is red.

Per host type — the starting XML is BCM's default for the model:

| Host type | `LAYOUT_TYPE` | `LAYOUT_CAT` | BCM default XML (`LAYOUT_BASE_XML`) |
|---|---|---|---|
| Control plane | `cp` | `$CAT_CP` | `x86_64-slave-one-big-partition-xfs.xml` |
| CPU workers | `wrk` | `$CAT_WRK` | `x86_64-slave-one-big-partition-xfs.xml` |
| GPU (`$GPU_NODE_TYPE`) | `$GPU_NODE_TYPE` | `$CAT_GPU` | `x86_64-slave-raid0-8-cache-drives.xml` (DGX B300: 2 boot + 8 cache) |

**1. Pick the collection node and define it alone** (3.5's commands for
this one node, or a one-row inventory through 3.0). Give that one device
BCM's default XML for the model:

```bash
# Repeat 3.4 once per host type. Example: the GPU type.
export LAYOUT_TYPE="$GPU_NODE_TYPE"
export LAYOUT_CAT="$CAT_GPU"
export LAYOUT_BASE_XML="x86_64-slave-raid0-8-cache-drives.xml"
export LAYOUT_NODE="gpu-worker-1"     # the collection node for this type
mkdir -p disk-layouts
cmsh -c "device ; use ${LAYOUT_NODE:?} ; set disksetup ${LAYOUT_BASE_XML:?} ; commit"
```

Power the collection node on and PXE-boot it, watching its BMC console.
It comes up on the stock layout. That is acceptable for this one
attended node, because its disks are about to be laid out again.

**2. Collect** once it shows `UP` in `cmsh -c "device list"`:

```bash
ssh "${LAYOUT_NODE:?}" 'ls -l /dev/disk/by-path' | tee "disk-layouts/${LAYOUT_TYPE:?}-by-path.txt"
# whole NVMe drives only (skips -partN entries and virtual media such as sr0);
# for a non-NVMe host type (VM control plane) drop the *nvme* filter:
ssh "$LAYOUT_NODE" 'for p in /dev/disk/by-path/*nvme*; do case "$p" in *-part*) continue ;; esac; d=$(readlink -f "$p"); printf "%s  %s  %s\n" "$p" "$d" "$(lsblk -dno SIZE,MODEL "$d")"; done' \
  | tee "disk-layouts/$LAYOUT_TYPE-map.txt"
```

**3. Build the map:** by-path → kernel name (today) → size/model →
role. Assign roles by **size and model**, never by kernel name. The
default XML's assumption about which `nvmeXnY` is a boot drive can be
wrong on real hardware. (In the field record, the boot drives were not
`nvme0n1`/`nvme1n1`.) Example with **fictional** paths, DGX B300 pattern:

```text
# by-path (stable, slot)                      kernel today   size     role
/dev/disk/by-path/pci-0000:11:00.0-nvme-1   nvme3n1        1.9T     boot 1
/dev/disk/by-path/pci-0000:12:00.0-nvme-1   nvme6n1        1.9T     boot 2
/dev/disk/by-path/pci-0000:31:00.0-nvme-1   nvme0n1        3.8T     cache 1
...                                                                  (cache 2-7)
/dev/disk/by-path/pci-0000:38:00.0-nvme-1   nvme9n1        3.8T     cache 8
```

**4. Build the XML:** copy BCM's default and swap every kernel path for
its by-path value, role by role:

```bash
cp "/cm/local/apps/cmd/etc/htdocs/disk-setup/${LAYOUT_BASE_XML:?}" "disk-layouts/$LAYOUT_TYPE-by-path.xml"
nvim "disk-layouts/$LAYOUT_TYPE-by-path.xml"
```

- Each `<device>` ends up with **exactly one** `<blockdev>`: the by-path
  of the drive that holds that role. A stock XML may list several
  fallback `<blockdev>` lines per device. Delete the kernel-path
  fallbacks, or the problem comes back.
- Keep everything else (partitions, RAID members, filesystems) as BCM
  shipped it, unless the design says otherwise. 12.2 shows the structure.

**5. Validate and apply it to the collection node only, then reinstall
that node:**

```bash
scripts/runbook.py disklayouts --type "$LAYOUT_TYPE" --category "$LAYOUT_CAT" --node "$LAYOUT_NODE" --dry-run
scripts/runbook.py disklayouts --type "$LAYOUT_TYPE" --category "$LAYOUT_CAT" --node "$LAYOUT_NODE" --exec
# full install so the new layout is written (property name: BCM 11 manual)
cmsh -c "device ; use $LAYOUT_NODE ; set nextinstallmode full ; commit"
cmsh -c "device ; reboot -n $LAYOUT_NODE"
```

The pass refuses an XML with kernel names, by-id/uuid, or no
`<blockdev>` at all. It applies nothing in that case.

**6. Verify across reboots.** The field failure matched the layout on
about one boot in three, so one good boot proves nothing. Reboot the
collection node **three times**. After each boot, every mount point in
the XML must be mounted on the drives the map says:

```bash
ssh "$LAYOUT_NODE" 'findmnt -n -o TARGET,SOURCE / /boot /raid; cat /proc/mdstat'
cmsh -c "device ; reboot -n $LAYOUT_NODE"     # x3, re-checking after each boot
```

**6b. Compare a second node of the type** before you promote. By-path
names are PCI bus addresses, and bus numbering comes from firmware
enumeration: two nodes of one "type" with different BIOS/BMC levels or
a different riser or NIC population can number their slots
differently, and the XML from step 4 then names drives that node
doesn't have — or the wrong ones. Boot **one more node** of the type
on BCM's default XML, attended, exactly as in step 1, and diff its
by-path map against the collection node's (the kernel-name column is
dropped — it is expected to differ):

```bash
export LAYOUT_NODE2="gpu-worker-2"    # a second node of the same type
cmsh -c "device ; use ${LAYOUT_NODE2:?} ; set disksetup ${LAYOUT_BASE_XML:?} ; commit"
# power on + PXE-boot it, watching its BMC console; once it shows UP:
ssh "$LAYOUT_NODE2" 'for p in /dev/disk/by-path/*nvme*; do case "$p" in *-part*) continue ;; esac; d=$(readlink -f "$p"); printf "%s  %s  %s\n" "$p" "$d" "$(lsblk -dno SIZE,MODEL "$d")"; done' \
  | tee "disk-layouts/$LAYOUT_TYPE-map-$LAYOUT_NODE2.txt"
diff <(awk '{$2=""; print}' "disk-layouts/$LAYOUT_TYPE-map.txt") \
     <(awk '{$2=""; print}' "disk-layouts/$LAYOUT_TYPE-map-$LAYOUT_NODE2.txt") \
  && echo "MATCH: $LAYOUT_NODE2 has the same by-path map — the XML holds for the type"
# every device the XML names must exist on the second node too:
grep -o '/dev/disk/by-path/[^<]*' "disk-layouts/$LAYOUT_TYPE-by-path.xml" | sort -u \
  | while read -r p; do ssh "$LAYOUT_NODE2" "test -e '$p'" || echo "MISSING on $LAYOUT_NODE2: $p"; done
```

- **Match** (no diff output, no `MISSING`): promote (step 7). After
  promotion, clear the second node's override and give it a full
  install so it lands on the category layout (step 7's `clear
  disksetup` line with `$LAYOUT_NODE2`, then step 5's `nextinstallmode
  full` + reboot).
- **Mismatch that reflects a variant** — the difference follows a
  firmware level, riser/NIC population or hardware revision that other
  nodes share (check BIOS/BMC versions and `lspci` on both): **the type
  splits.** Give the variant its own host type: a new `LAYOUT_TYPE`
  (e.g. `dgx-b300-rev2`), its own category (3.1) carrying the same
  image, and its own pass through 3.4 with this second node as its
  collection node. Move the variant's rows in the inventory to the new
  category. If the site's plan is to bring firmware to one level first,
  do that, then collect again — a firmware update can renumber buses
  too.
- **Mismatch on one node only** — a drive in a different slot, a
  missing or failed drive, a riser fault: fix the hardware if it is a
  fault and compare again. If the node is genuinely different and
  alone, **it gets its own collection**: build its XML from its own map
  (steps 2–4), apply it with `disklayouts --node "$LAYOUT_NODE2"`,
  verify with step 6's three reboots, and leave it as that device's
  override (don't `clear disksetup`). Record it in the deviation log
  (10). The fleet gate validates that device-level layout too (by-path
  only), but only this step proves it matches the node's drives.

If the type has only one node, there is nothing to compare: record
that in the gate line below.

**7. Promote to the category** and drop the node's override (it now
inherits the category layout):

```bash
scripts/runbook.py disklayouts --type "$LAYOUT_TYPE" --category "$LAYOUT_CAT" --exec
cmsh -c "device ; use $LAYOUT_NODE ; clear disksetup ; commit"
```

- [ ] **Gate, per host type:** `disk-layouts/<type>-by-path.xml` was
      built from that type's collection node, passes the disklayouts
      validation, and the collection node was reinstalled on it and
      survived 3 reboots with every mount correct; the category carries it
- [ ] **Second node, per host type (6b):** a second node's by-path map
      matched the collection node's and every XML device exists on it —
      or the type was split / the node given its own collection, and
      logged in 10 — or the type has one node only (noted here)
- [ ] Any `--allow-non-by-path` use is in `disk-layouts/OVERRIDES.log`
      and in the deviation log (10) with its reason
- [ ] Every host type in the inventory has passed the gate, so 3.5 may
      begin — `scripts/runbook.py layoutgate --category <each>` reports
      every category passed

## 3.5 — Provision nodes (the fleet)

> **Gate:** 3.4 has passed for every host type you are about to
> provision. At scale, run 3.0's `provision-nodes.sh` (or
> `runbook.py nodes --exec`) now: both refuse unless every category in
> the inventory carries a by-path layout. They are add-or-verify: the
> collection nodes already exist, and they note and align them. The
> hand-typed commands below have no such check — run
> `scripts/runbook.py layoutgate --category "$CAT_GPU"` (and each other
> category) first.

Add each node — add, provisioning interface, and category in one shot
(repeat per node; interface name per hardware):

```bash
# Control plane (example)
cmsh -c "device ; add physicalnode k8s-ctrl-1 10.10.15.50 ens33 ; set provisioninginterface ens33 ; set category $CAT_CP ; commit"
# GPU worker (example — DGX interface names differ, e.g. enp225s0f0np0)
cmsh -c "device ; add physicalnode gpu-worker-1 10.10.15.53 enp225s0f0np0 ; set provisioninginterface enp225s0f0np0 ; set category $CAT_GPU ; commit"
```

> Re-running an `add` for a node that exists (a 3.4 collection node, say)
> reports that it already exists. That is expected. Continue with the
> `set` lines, which bring the node into line.

Set interfaces to DHCP and record MACs. Per-node values come from the 01
inventory: set them in the shell first. Each use is written
`${NAME:?…}`, so a value you forgot to set stops that command and is
never sent to `cmsh` as a blank:

```bash
# NODE_MAC=00:11:22:33:44:55   (this node's provisioning MAC, from the 01 inventory)
cmsh -c "device ; use k8s-ctrl-1 ; interfaces ; use ens33 ; set dhcp yes ; commit"
cmsh -c "device ; use k8s-ctrl-1 ; set mac ${NODE_MAC:?set NODE_MAC to this node MAC from the 01 inventory} ; commit"
```

DGX nodes with dual provisioning NICs — add the second interface and
drop the boot flag from the first:

```bash
# NODE_IFACE2 / NODE_MAC2: the second provisioning NIC's name and MAC
cmsh -c "device ; use gpu-worker-1 ; interfaces ; add physical ${NODE_IFACE2:?set NODE_IFACE2 to the second NIC name} ; commit"
cmsh -c "device ; use gpu-worker-1 ; interfaces ; use ${NODE_IFACE2:?set NODE_IFACE2} ; set mac ${NODE_MAC2:?set NODE_MAC2 to the second NIC MAC} ; commit"
cmsh -c "device ; use gpu-worker-1 ; interfaces ; remove bootif ; commit"
```

The disk layout comes from the category (3.4) — set nothing per node.

## 3.6 — IPMI / BMC networking **[restored]**

> This section existed only in the lab runbook — the client docs dropped
> it. It is required for DGX power control via BCM.

```bash
# mgmtnet (the OOB network from 01) was created by the networks pass in
# 2.5 — verify it here, never re-add it. Expect base $NET_OOB, gateway
# $GW_OOB, management allowed: yes.
cmsh -c "network; use mgmtnet; show"
# Missing or wrong? Re-run the pass (add-or-verify; it converges values):
#   scripts/runbook.py networks --dry-run   then   --exec
# (The lab split this into mgmtnet + ipminet and then configured the wrong
# object — ipminet instead of mgmtnet. The template consolidates BMCs onto
# the single OOB network from the 01 plan.)

# BMC interfaces — repeat per GPU node, BMC IPs from the 01 inventory
# (3.0's provision-nodes.sh already adds ipmi0 for every GPU row):
cmsh -c "device ; use gpu-worker-1 ; interfaces ; add bmc ipmi0 ${BMC_IP:?set BMC_IP to this node BMC address from the 01 inventory} mgmtnet ; commit"

# BMC credentials. The value comes from your shell, never from a file:
#   read -rs BMC_PASSWORD; export BMC_PASSWORD
# bcm_require_secret (00-variables.sh) refuses an unset value or a
# <PLACEHOLDER>, so the placeholder can never become the fleet's BMC password.
# NOTE: BCM 11 docs show `set password` prompting interactively
# (enter/retype). If the one-liner below prompts instead of accepting
# the value, run the bmcsettings block in an interactive cmsh session.
bcm_require_secret BMC_PASSWORD && cmsh -c "partition ; use base ; bmcsettings ; set username $BMC_USER ; set password $BMC_PASSWORD ; commit"
# inspect effective settings without entering the submode:
# cmsh -c "partition ; use base ; bmcsettings --show"
```

> **B300 note:** `bmcsettings` supports `set firmwaremanagemode` with
> values including `b200`, `b300`, `gb200`, `h100` — use it for DGX B300
> firmware management via BCM.

Validate (BMC IPs from your 01 inventory):

```bash
cmsh -c "device ; use gpu-worker-1 ; power status"
# -E reads the password from IPMI_PASSWORD, so it stays out of the process list:
bcm_require_secret BMC_PASSWORD && IPMI_PASSWORD="$BMC_PASSWORD" \
  ipmitool -I lanplus -H "${BMC_IP:?set BMC_IP}" -U "$BMC_USER" -E chassis power status
```

> `power status` can report failure for the head node's *own* BMC (the
> head node can't always reach its own BMC over the network) — that does
> not mean power control is broken for the rest of the cluster.

## Verification

- [ ] 3.4 gate passed for every host type before any fleet node was provisioned
- [ ] `cmsh -c "softwareimage list"` shows all images on `$KERNEL_VER`
- [ ] `cmsh -c "device list"` shows every node with category, MAC, and IP
- [ ] `cmsh -c "device ; use <gpu-node> ; power status"` responds via BMC
