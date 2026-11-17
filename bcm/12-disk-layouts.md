# 12 — Disk Layouts

> **Reference section — the procedure runs inside 03.** Collecting the
> layout is the named exception to "work the sections in order": on the
> **first node of each host type**, the manual by-path collection
> ([03.4](03-images-categories-nodes.md)) runs **before fleet
> provisioning** (03.5), and fleet provisioning is gated on that type's
> verified XML. Don't wait until you reach 12: by then the fleet is
> already provisioned on whatever layout it was given. Changing
> disksetup on a provisioned node means reinstalling it.
>
> **The headline rule:** never use kernel device paths (`/dev/nvme0n1`,
> `/dev/sda`) in a disklayout XML. They can reorder across reboots.
> **Field finding, DGX A100 install:** one failing NVMe drive initialized
> at a different speed on each boot because of its I/O errors, so the
> `nvmeXnY` numbering changed from boot to boot. Dropping the bad drive
> from the kernel-path layout didn't hold. Each reboot had about a
> **1-in-3 chance of matching the layout**. When `cm-kubernetes-setup`
> (which reboots nodes) timed out against a disk setup the devices
> didn't match, it **cost days**. **[unverified]**: the field record
> doesn't say whether BCM's node-installer reinstalls (wipes) or just
> fails a node whose devices don't match its layout. Confirm against the
> BCM 11 admin manual before relying on either.
>
> **Automate it:** `scripts/runbook.py disklayouts` validates
> `disk-layouts/<type>-by-path.xml` (by-path only — kernel names and
> by-id/uuid are refused). It applies the file to one node (`--node`,
> the 03.4 test) or at category level. If the XML isn't there yet, the
> pass stops and prints the collection procedure, because the XML can
> only be built from live hardware. `scripts/check.sh` fails if any
> shipped disksetup XML names a kernel device. The fleet is gated
> mechanically: `runbook.py nodes --exec` and the generated
> `provision-nodes.sh` refuse unless every inventory category carries a
> by-path layout (03.4). For a host type where by-path genuinely fails
> in the installer, 03.4 documents the one logged, named override
> (`--allow-non-by-path --reason "…"`); `check.sh` has none.

## 12.1 — by-path, not by-id, not kernel names

| Scheme | Stable across reboots? | Survives drive replacement? |
|---|---|---|
| `/dev/nvme0n1`, `/dev/sda` (kernel) | **No** — enumeration order varies | N/A |
| `/dev/disk/by-id/nvme-SAMSUNG_...` | Yes | **No** — the ID names the drive, not the slot |
| `/dev/disk/by-path/pci-0000:11:00.0-nvme-1` | Yes | **Yes** — the path names the PCI slot |

Use **by-path**. It's tied to the slot, so a replacement drive in the
same slot keeps working with zero XML changes. **by-id and by-uuid are
not used:** they change when a drive is replaced. BCM's own by-id
aliases have also failed in the field. On a DGX B200 install, the
node-installer stopped at `Detecting device
'/dev/disk/by-id/raiddisk-1': not found` because the alias did not
translate at install time.

Collect the mapping per host type (once per hardware type, on that
type's collection node — the steps are in 03.4):

```bash
# on the collection node of each type:
ls -l /dev/disk/by-path
```

Record the by-path → kernel name → size → role map. Assign roles by
size and model, never by kernel name. Example: DGX B300 (2 boot + 8
cache), **fictional** addresses — build yours from YOUR `ls -l` output:

```text
# slot/purpose map (example — fictional PCI addresses)
/dev/disk/by-path/pci-0000:11:00.0-nvme-1  ->  1.9TB boot drive 1
/dev/disk/by-path/pci-0000:12:00.0-nvme-1  ->  1.9TB boot drive 2
/dev/disk/by-path/pci-0000:31:00.0-nvme-1  ->  3.8TB cache 1
/dev/disk/by-path/pci-0000:32:00.0-nvme-1  ->  3.8TB cache 2
...                                             (cache 3-7)
/dev/disk/by-path/pci-0000:38:00.0-nvme-1  ->  3.8TB cache 8
```

## 12.2 — Build per-model XMLs

Copy BCM's default XML for the model and swap every kernel path for the
by-path value of the drive in that role. Each `<device>` ends up with
exactly one by-path `<blockdev>`. Delete any kernel-path fallback
`<blockdev>` lines the stock file lists. Naming:
`disk-layouts/<type>-by-path.xml` (what the pass looks for).

Structure (DGX B300 pattern — 2 boot + 8 cache; **fictional** paths):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<diskSetup>
  <device>
    <blockdev>/dev/disk/by-path/pci-0000:11:00.0-nvme-1</blockdev>
    <partition id="efi" partitiontype="esp">
      <size>100M</size><type>linux</type><filesystem>fat</filesystem>
      <mountPoint>/boot/efi</mountPoint>
    </partition>
    <partition id="boot1"><size>4G</size><type>linux raid</type></partition>
    <partition id="slash1"><size>max</size><type>linux raid</type></partition>
  </device>
  <!-- ... second boot drive (pci-0000:12:00.0), then one <device> per
       cache drive with a single max-size "linux raid" partition each ... -->
  <raid id="boot">
    <member>boot1</member><member>boot2</member>
    <level>1</level><filesystem>ext2</filesystem><mountPoint>/boot</mountPoint>
  </raid>
  <raid id="slash">
    <member>slash1</member><member>slash2</member>
    <level>1</level><filesystem>ext4</filesystem><mountPoint>/</mountPoint>
  </raid>
  <raid id="raid">
    <member>raid1</member><member>raid2</member>
    <member>raid3</member><member>raid4</member>
    <member>raid5</member><member>raid6</member>
    <member>raid7</member><member>raid8</member>
    <level>0</level><filesystem>ext4</filesystem><mountPoint>/raid</mountPoint>
  </raid>
</diskSetup>
```

Apply it through the pass. It validates the file first, then applies it
to one node (03.4 step 5) or the category (03.4 step 7):

```bash
scripts/runbook.py disklayouts --type "$GPU_NODE_TYPE" --category "$CAT_GPU" --dry-run
# the pass runs: cmsh -c "category ; use $CAT_GPU ; set disksetup /abs/path/disk-layouts/$GPU_NODE_TYPE-by-path.xml ; commit"
```

> **Field note:** the stock BCM templates use kernel paths. The
> standing recommendation is to never use them: copy BCM's default XML
> for each model, swap in the by-path values, and keep that XML per host
> type with the runbook as the category disksetup.

## 12.3 — Layout selection reference

| Node type | Pattern |
|---|---|
| Control plane / CPU workers | One big XFS partition (still by-path — 03.4 covers every host type) |
| DGX (per model) | 2 boot drives RAID1 (`/boot`, `/`) + cache drives RAID0 (`/raid`) |

Disk layouts live in the **CMDaemon database**; the XML files under
`/cm/local/apps/cmd/etc/htdocs/disk-setup/` are default templates:

```bash
cmsh -c "disksetup list"
cmsh -c "disksetup ; use ${LAYOUT:?set LAYOUT to a layout name from the list} ; show"
# per-node override (the 03.4 collection node) / revert to category:
cmsh -c "device ; use ${NODE:?set NODE to the device name} ; set disksetup ${LAYOUT:?set LAYOUT} ; commit"
cmsh -c "device ; use ${NODE:?set NODE} ; clear disksetup ; commit"
```

Changing `disksetup` flags the node restart-required.

- [ ] Every custom layout uses by-path; no kernel device names, no by-id/uuid
      (`scripts/check.sh` disksetup guard and the disklayouts pass both enforce it)
- [ ] Layout validated on one node of each type before rolling to the fleet
      (the 03.4 gate: collection node reinstalled on it, 3 clean reboots)
