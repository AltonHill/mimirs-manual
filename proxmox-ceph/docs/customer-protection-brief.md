# Customer Protection Brief — "You Are Protected"

Customer-facing high points for a hyperconverged Proxmox VE + Ceph deployment.
Anonymized and template-ready — adapt the numbers (node count, drive count, warranty
terms) per site. This is the *what it means for you* companion to the runbook's
*how we build it*. Every line below describes the design as built; where the
design has a limit, the limit is stated.

## Every write, three copies

Every VM disk write is stored on **all three physical servers** before it is
acknowledged. One server can fail completely — power loss, dead board, anything —
with **zero data loss**: the other two already hold every committed write.

## Automatic recovery of your VMs

Proxmox HA watches the cluster and restarts protected VMs from a failed server on
the surviving servers automatically — roughly **2–5 minutes** end to end, plus the
VM's own boot time. No one has to be paged to start the recovery. A hardware
watchdog on every server makes a server that drops out of the cluster
power-cycle itself before its VMs are started elsewhere, so a VM never runs
twice (validated by the isolation drill at acceptance).

## Storage that keeps serving — and its honest limits

- **A failed drive:** VMs keep running. Ceph rebuilds the lost drive's copies
  onto the other drive in the same server, automatically. That rebuild needs
  free space, which is why planned storage is capped (below).
- **A failed server:** VMs keep running on the two remaining copies — readable
  and writable. Each server holds one copy, and with one of three servers down
  there is no other server to rebuild the third copy on, so the cluster runs **degraded on two copies until the server is
  repaired or replaced**; it does not heal on its own in the meantime. Treat a
  server outage as urgent: while degraded, a further drive failure can pause
  the affected VMs' storage until a copy returns.
- **Planned capacity: 3.3 TB of VM data** (about 43% of the 23 TB raw), modelled
  on a single-drive failure. The cap is enforced: at 3.3 TB the VM storage pool
  stops accepting new writes, so the cluster always keeps the room it needs to
  recover from a failed drive.

## Separate networks for separate jobs

- Storage traffic runs on two 200G links per server, one to each of two
  fabric switches — losing a switch or a link fails over to the other.
- Cluster membership (corosync) runs on **two required links**: one on the
  1GbE management network and one on the 200G fabric, on physically separate
  paths. Losing either one does not cost the cluster its quorum.
- Backups travel on **their own network (a dedicated VLAN)** on the fabric,
  never on the link the cluster uses to keep quorum.

## Backups beyond hardware failure

Proxmox Backup Server on its own dedicated host, with a retention policy and
tested restores. Hardware resilience covers dead servers and drives; backups
cover ransomware, human error, and data corruption — the failures replication
copies faithfully to all three servers.

## Redundancy, component by component

- N+N redundant power supplies per server
- Mirrored boot drives per server
- Dual 200G fabric links per server, spread across two switches
- Two required cluster-membership links on separate physical paths
- 3-server quorum: the cluster keeps running and making decisions with any one
  server down

What is single by design: the 1GbE management switch (its loss costs remote
management access and cluster link 0, not quorum — link 1 carries on) and the
backup server (its loss stops new backups, not the running VMs). Neither is in
the live data path.

## Enterprise hardware, enterprise support

Server-class hardware with next-business-day on-site warranty — when something
does fail, parts and hands arrive fast, and the cluster keeps serving
meanwhile, on two copies until the repair is done.
