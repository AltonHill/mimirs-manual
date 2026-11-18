# 05 — HA and fencing

Proxmox HA on the three nodes: hardware watchdog fencing FIRST, then HA rules (node-affinity — the 9.x replacement for deprecated groups), protected resources, maintenance procedures, and expected behavior on node loss.

> **READ THIS FIRST — the fencing model.** Proxmox HA has **no external fence agents**. There is no IPMI/STONITH/ipmilan integration in Proxmox, and there never has been (the old "Fencing" menu in the GUI is a display-only remnant). Fencing is **watchdog self-fencing**: if a node stops behaving, its own watchdog timer expires and the node power-cycles itself. Anyone expecting traditional external fencing must understand this up front — it changes what "fenced" means and how fast it happens. On Lenovo hardware the correct path is the **IPMI hardware watchdog**, configured below. The Lenovo XCC **cannot** be used as an external fence agent — no such configuration exists in Proxmox; the IPMI watchdog is the supported equivalent.

**Variables** — defined in the site file `site-vars.sh` at the track root (copied from the template [00-overview/variables.sh](../00-overview/variables.sh)); **do not redefine them here**. This section consumes:

- `HA_RULE_NAME` — the node-affinity rule's name (template `ha-prefer-any`);
- `HA_PROTECTED_VMS` — **one format everywhere: space-separated numeric VMIDs**, e.g. `"100 101"`. The template default is **empty, and empty protects nothing**: Step 2 refuses to run until the site names its VMs. (There is no fallback to "100 101" or any other guess.)
- `NODE1_HOSTNAME` … `NODE3_HOSTNAME` — the rule's node list.

Each block sources the site file itself (track README, "Working-directory contract").

Run all steps as `root`, on each node where noted.

---

## Prerequisites

- §01 cluster healthy, §04 pools in place (HA-protected VM disks should live on `vm-storage` — HA restarts a VM on another node; the disk must be reachable there, which means shared Ceph storage, not local-lvm).
- Every node can reach the XCC/BMC of its siblings (IPMI network up — §00a/§02).

---

## Step 1 — Hardware watchdog on Lenovo (do this FIRST, on each node)

By default Proxmox uses the Linux `softdog` software watchdog. On real server hardware, switch to the IPMI hardware watchdog **before** enabling HA — software watchdogs can't fence a truly hung kernel.

### 1a. Configure the watchdog module

On **each node**, in `/etc/default/pve-ha-manager`:

```ini
WATCHDOG_MODULE=ipmi_watchdog
```

and create `/etc/modprobe.d/ipmi_watchdog.conf`:

```ini
options ipmi_watchdog action=power_cycle
```

- `action=power_cycle` is the correct policy: when the watchdog fires, the BMC power-cycles the node (not a soft reboot, which a hung kernel may ignore).
- Regenerate the initramfs and reboot so the module loads early:

```bash
( set -euo pipefail      # no reboot if the initramfs rebuild fails
  update-initramfs -u -k all
  reboot
)
```

### 1b. Disable competing watchdogs

More than one watchdog fighting over the same timer is a classic source of phantom fences. On **each node**, disable:

- **NMI watchdog:** add `nmi_watchdog=0` to the kernel command line (`/etc/default/grub`, then `update-grub` + reboot), or `echo 0 > /proc/sys/kernel/nmi_watchdog` (runtime only, does not survive reboot).
- **Lenovo XCC auto-recovery / automatic server restart policies:** check XCC → Server Management / Recovery and turn off any OS-watchdog-equivalent auto-restart that would race the IPMI watchdog.
- Confirm no other watchdog module is loaded: `lsmod | grep -iE 'watchdog|softdog|iTCO|mei_wdt'` — expect only `ipmi_watchdog`.

### 1c. Verify the watchdog

On **each node**, after reboot:

```bash
ipmitool mc watchdog get
```

Proxmox expects to see a watchdog with a **~10 s countdown**. If the countdown reads something wildly different (or the watchdog is stopped), do not proceed — HA fencing timing depends on this. Then confirm the HA manager picked it up:

```bash
systemctl status pve-ha-lrm pve-ha-crm
journalctl -u pve-ha-lrm -u pve-ha-crm --since "10 min ago" | grep -i watchdog
```

---

## Step 2 — Put the protected VMs under HA, then create the node-affinity rule

> **Do NOT use `ha-manager groupadd`.** Groups are deprecated in PVE 9.x in favor of HA rules. Old guides and forum posts still teach `groupadd` — this runbook does not. The replacement is `node-affinity`.

**Order matters:** the resources are added to HA **first**, and only then does the rule reference them (whether `rules add` accepts resources not yet under HA is **[unverified]** — this order never asks). One block, run on **any one** node (HA config is cluster-wide); it validates the VMID list, skips (with a NOTE) anything already under HA, and converts the list to the rule's `vm:<id>` form itself:

```bash
( set -euo pipefail
  source "${CERBERUS_TRACK:?export CERBERUS_TRACK=/path/to/proxmox-ceph first (track README)}/site-vars.sh"
  cerberus_require_vars HA_RULE_NAME NODE1_HOSTNAME NODE2_HOSTNAME NODE3_HOSTNAME
  [ -n "${HA_PROTECTED_VMS// /}" ] \
    || { echo "REFUSED: HA_PROTECTED_VMS is empty — nothing would be protected. Set it in site-vars.sh (space-separated VMIDs, e.g. \"100 101\")." >&2; exit 1; }
  resources=""
  for id in $HA_PROTECTED_VMS; do
    [[ "$id" =~ ^[0-9]+$ ]] || { echo "FATAL: '$id' in HA_PROTECTED_VMS is not a numeric VMID (format: \"100 101\")" >&2; exit 1; }
    qm config "$id" >/dev/null || { echo "FATAL: VM $id does not exist" >&2; exit 1; }
    if grep -qxE "vm: ?$id" <<<"$(ha-manager config)"; then
      echo "NOTE: vm:$id is already under HA — left as is"
    else
      ha-manager add "$id" --state started
    fi
    resources="${resources:+$resources,}vm:$id"
  done
  ha-manager rules add node-affinity "$HA_RULE_NAME" \
    --nodes "$NODE1_HOSTNAME,$NODE2_HOSTNAME,$NODE3_HOSTNAME" \
    --resources "$resources"
  ha-manager rules list
)
```

(`ha-manager config` lists each resource as a bare service ID line — `vm:100` in the ha-manager(1) 9.2.14 example output, while `resources.cfg` itself writes `vm: 100`; the already-present check accepts both forms. Verified against https://pve.proxmox.com/pve-docs/ha-manager.1.html (9.2.14). If the rule already exists, `rules add` fails and stops the block: inspect it with `ha-manager rules list`.)

- `--state started` means HA actively keeps the VM running: it starts it if stopped and restarts it on another node if its host fails. Other states: `enabled`, `disabled`, `ignored`, `stopped` (see `man ha-manager`). Change later with `ha-manager set vm:ID --state STATE`.
- `--nodes` — the site's three hostnames; the VMs prefer these nodes in listed order and restart on the next available one.
- `--resources` — built from `HA_PROTECTED_VMS` (`"100 101"` → `vm:100,vm:101`). Containers (`ct:ID`) are out of scope for this list; add them by hand with the same order (add, then rule).
- Useful variants: add `--affinity negative` to *keep resources off* the listed nodes; add a `resource-affinity` rule with `--affinity negative` to keep two VMs on different nodes (e.g. a VM and its replica). List and remove with `ha-manager rules list` / `ha-manager rules remove NAME`.

> **node-4 callout:** adding the 4th node does not change MONs (they stay at 3, §03) but you **do** update HA rules: add `$NODE4_HOSTNAME` to `--nodes` in each rule if the new node should host protected VMs, or leave rules untouched to keep the 4th node as compute/rebalance capacity only.

## Step 3 — Verify the HA resources

- Verify:

```bash
ha-manager status
```

Expect each resource listed with its current node and state `started`. `--verbose` adds rule evaluation detail when something looks wrong.

---

## Step 4 — Maintenance procedures (PVE 9.2)

Planned maintenance must not look like a failure to the HA manager, or your rolling reboot will migrate half the cluster.

### Cluster-wide HA disarm (firmware days, whole-cluster work)

New in PVE 9.2 (Roadmap, 9.2 release notes: "The HA stack can now be disarmed cluster-wide via the new `disarm-ha` and `arm-ha` CRM commands") — disarm the entire cluster before disruptive maintenance, re-arm after. `disarm-ha` **requires** a resource mode (ha-manager(1), 9.2.14): `freeze` keeps every resource in its current state but applies no new commands or state changes and reacts to no failures; `ignore` removes the resources from HA tracking so you can manage them by hand. This runbook uses `freeze`:

```bash
ha-manager crm-command disarm-ha freeze   # releases all watchdogs; resources keep running, frozen
# ... do the maintenance ...
ha-manager crm-command arm-ha         # re-arm afterwards
```

While disarmed, the HA manager stops reacting: VMs stay where they are, nobody gets restarted, no fences fire. **Forgetting to re-arm is the failure mode** — add "arm-ha" to the maintenance checklist as its own line item.

### Single-node maintenance

```bash
ha-manager crm-command node-maintenance enable node-2
# ... patch / reboot node-2 ...
ha-manager crm-command node-maintenance disable node-2
```

Enabling maintenance on a node migrates its HA resources to the other available nodes (chosen by the HA rules and the CRS mode); disabling it moves back every service that was on the node when maintenance was enabled (ha-manager(1), 9.2.14, "Node Maintenance Mode"). This is not new in 9.2 — the earlier "(new in 9.2)" label was wrong. Do the work, then disable maintenance.

---

## Step 5 — Expected behavior on node loss

What actually happens when node-2 dies uncleanly (power loss, kernel hang), with the config above:

1. **Detection:** the surviving nodes' corosync/CRM notices node-2 is gone (membership change, typically within tens of seconds).
2. **Fencing:** a node that has lost quorum stops feeding its **own watchdog**; once it expires (the ~10 s IPMI countdown, armed while the node runs HA resources) the BMC power-cycles it. This is the fence — it guarantees node-2 can't write to Ceph while its VMs are being restarted elsewhere. The survivors' CRM waits until the dead node's HA lock has expired — by which time its watchdog must have fired — before recovering its services. (If the node is truly dead — PSU failure — there's nothing to fence; the CRM proceeds on quorum.) Only §07 drill (e), the isolation drill, observes this happening; a powered-off node (drill b) cannot fence.
3. **Recovery:** the CRM restarts node-2's HA resources on node-1/node-3 per the node-affinity rule.

**Realistic timing expectations** (ballpark — §07 failure testing measures the real numbers on this hardware):

- Watchdog fence: the isolated node's watchdog fires and the BMC power-cycles it **~1–2 minutes** after it loses quorum; it is booting again **~2–4 minutes** after the failure (§07 drill (e) measures both).
- VM restart on a survivor: begins once the CRM confirms the node is gone and fenced, typically **~1–2 minutes** after failure detection; the VM then boots normally (add its own boot time).
- Total service interruption for a protected VM: roughly **2–5 minutes** end to end (plus the guest's own boot). §07 drills (b) and (e) use these same numbers as their expectations. Ceph I/O continues throughout on the surviving two copies (§04: size=3/min_size=2, degraded, no rebuild target until the node returns) — storage does not stall, only the VM's compute restarts.

What HA does **not** do: it does not live-migrate a dead node (that's impossible — the source is gone); it restarts. It does not protect VMs on local storage; it does not replace backups (§06).

---

## Verification checklist

- [ ] `/etc/default/pve-ha-manager` + `/etc/modprobe.d/ipmi_watchdog.conf` in place on all 3 nodes, `ipmi_watchdog` loaded, no competing watchdog modules
- [ ] `ipmitool mc watchdog get` shows ~10 s countdown on all 3 nodes
- [ ] `HA_PROTECTED_VMS` set (space-separated VMIDs); each VM added to HA **before** the rule
- [ ] `ha-manager rules list` shows the `$HA_RULE_NAME` node-affinity rule with all 3 nodes
- [ ] `ha-manager status` shows each protected VM `started` on its node
- [ ] `disarm-ha` → `arm-ha` cycle tested once (confirm arm-ha re-arms; watch `ha-manager status` resume)

## References

Full link list lives in [docs/references.md](../../docs/references.md); the ones this section leans on:

- https://pve.proxmox.com/pve-docs/ha-manager.1.html — ha-manager(1): rules (node-affinity/resource-affinity), add/set, crm-command arm/disarm (notes groupadd deprecation).
- https://pve.proxmox.com/pve-docs/chapter-ha-manager.html — High Availability chapter: fencing model, hardware watchdog configuration.
