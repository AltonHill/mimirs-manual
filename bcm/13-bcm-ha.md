# 13 — BCM Head-Node High Availability (optional)

> **Prerequisites:** 02–03 complete (licensed head node, nodes provisioned).
> Run this **before** 05 (Kubernetes) — HA setup requires all cluster
> nodes powered off.
>
> The tool is `cmha-setup` (no dash). Reference:
> https://docs.nvidia.com/dgx-basepod/deployment-guide-dgx-basepod/latest/bcm-ha.html

## 13.1 — Prerequisites

- Second head-node hardware, cabled (dedicated failover interfaces, or
  reuse internalnet/managementnet — decide now).
- License covers both head nodes: `request-license` asks "Will this
  cluster use a high-availability setup with 2 head nodes?" — provide
  **both** head-node MACs. Use a LOM-port MAC on a non-removable NIC
  (BMC MACs do not work).
- External Virtual IP from the site survey — this VIP is how you reach
  the *active* head node after HA is up. Record it as `BCM_HA_VIP` in
  `site-vars.sh` (it ships empty: empty means no HA).

`cmha-setup` and `/cm/cm-clone-install` are interactive menus. In the
blocks below only the uncommented lines are commands; the `# ->` lines
are the screens you step through, in order — read them, don't paste
them.
- `cmha-setup` can also place `/cm/shared` and `/home` on external
  shared storage (NAS/DAS/DRBD) — decide before starting.

## 13.2 — Procedure

```bash
# 1. Verify head-node power control over the cluster nodes, every category:
for c in $CAT_CP $CAT_WRK $CAT_GPU; do cmsh -c "device ; power -c $c status"; done

# 2. Power OFF all cluster nodes (required before HA config) — every
#    category (add any extra GPU-type categories to the list):
for c in $CAT_CP $CAT_WRK $CAT_GPU; do cmsh -c "device ; power -c $c off"; done

# 3. On the primary head node as root:
cmha-setup
# -> Setup -> Configure
# -> verify license info / MACs -> CONTINUE
# -> enter the external VIP ($BCM_HA_VIP) -> NEXT
# -> enter the secondary head-node name -> NEXT
# -> failover network: configure dedicated interfaces,
#    or skip to reuse internalnet
# -> enter the secondary head node's IPs -> review summary -> Yes
# -> enter BCM root password
# Wizard clones the head node, updates shared internal/external
# interfaces, updates the failover object, restarts cmdaemon.
```

```bash
# 4. PXE-boot the secondary head node, select RESCUE in GRUB.
#    In the rescue environment on the secondary:
#    /cm/cm-clone-install --failover
#    -> YES -> specify the inband interface
#    -> press 'c' to save the disk layout -> 'y' to reboot
#    Then set the secondary to boot from hard drive
#    (disable PXE via BMC/BIOS).
```

```bash
# 5. Back on the primary — finalize:
cmha-setup
# -> Finalize -> NEXT -> CONTINUE -> root password
# Clones the MySQL/cmdaemon databases to the secondary.
# -> select REBOOT, wait for the secondary to return.
```

## 13.3 — Verify

```bash
cmsh -c "device list -f"
# both head nodes show [ UP ]
```

- [ ] `$BCM_HA_VIP` reaches the active head node (`ping -c 3 "$BCM_HA_VIP"`; Base View on `https://$BCM_HA_VIP:8081/base-view/`)
- [ ] Failover tested (planned failover, not just config)
