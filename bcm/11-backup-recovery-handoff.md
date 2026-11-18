# 11 — Backup, Recovery & Handoff

## 11.1 — What to back up (and when)

| Artifact | Command | When |
|---|---|---|
| Run:ai cluster config | `kubectl get runaiconfig runai -n runai -o jsonpath='{.spec}' > runai_config_backup.yaml` | Post-install, pre-change |
| Run:ai Helm values | `helm get values runai-backend -n runai-backend > runai_control_plane_values.yaml` | Post-install, pre-change |
| PostgreSQL dump | Safe pattern in 06.7 | Pre-upgrade, weekly |
| BCM config | CMDaemon database backups + `cmd.conf` off-host (block below) | Pre-change |
| Head-node VM (standard topology) | Proxmox snapshot (11.2) | Before 02.5, 03, 05, 06 and every upgrade |
| `site-vars.sh` + `/root/cm-*-setup.conf` | Copy off-host (no secrets: credentials stay placeholders) | After every section |

Store backups off the head node, mode `0600`. Never commit secrets.

**BCM config.** CMDaemon keeps its own database backups on the head node
— **[unverified]** for BCM 11: the path `/var/spool/cmd/backup/` and its
daily schedule are from earlier Bright/BCM releases' admin manuals; check
the BCM 11 Administrator Manual ("backups") on first contact (see the
library's verification checklist). Copy them off-host with the CMDaemon
config:

```bash
ls -l /var/spool/cmd/backup/                    # expect recent dated dumps; empty = STOP, see the manual
tar -czf "/root/bcm-config-$(date +%F).tgz" /var/spool/cmd/backup /cm/local/apps/cmd/etc
chmod 0600 "/root/bcm-config-$(date +%F).tgz"   # then copy off the head node
```

Restoring CMDaemon's database is a BCM-support procedure (the manual's
"restoring from a backup"); on the standard topology the faster,
tested path is the VM snapshot (11.2).

**Test-restore the Run:ai DB dump** (11.3 asks for it). Restore into a
throwaway PostgreSQL pod in a scratch namespace — never into the live
database. The image's major version must be the live server's or newer
(the first line reads it). **[unverified]** that the cluster can pull
the `postgres` image (an air-gapped site mirrors it first) and that the
Run:ai dump restores into a stock image without its extensions; the
table count is the evidence either way:

```bash
cd /root/runai-backup
kubectl -n runai-backend exec "${PG_POD:?export PG_POD first (06.7)}" -- postgres --version
PG_IMAGE="${PG_IMAGE:?set PG_IMAGE to postgres:MAJOR matching the line above}"
kubectl create namespace pg-restore-test
kubectl -n pg-restore-test run pg --image="$PG_IMAGE" --env=POSTGRES_HOST_AUTH_METHOD=trust
kubectl -n pg-restore-test wait --for=condition=Ready pod/pg --timeout=180s
kubectl -n pg-restore-test exec pg -- psql -U postgres -c "CREATE ROLE \"${PG_USER:?export PG_USER first (06.7)}\""
kubectl -n pg-restore-test exec -i pg -- psql -U postgres -v ON_ERROR_STOP=1 -q < runai_db_backup.sql \
  && kubectl -n pg-restore-test exec pg -- psql -U postgres -tAc \
       "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')"
kubectl delete namespace pg-restore-test
```

Pass: the restore exits 0 and the table count is non-zero. Record the
count and the date in 10.

## 11.2 — Rollback notes

- **Before 05, standard topology (head node is a Proxmox VM):** roll the
  VM back to the snapshot taken before the step that went wrong (block
  below, run on a Proxmox node). Snapshots need snapshot-capable storage
  (Ceph RBD is). A rollback rewinds the head
  node only: anything it did after the snapshot (devices added, nodes
  provisioned, images changed) is gone from its database while the nodes
  still exist — so snapshot before each provisioning wave and roll back
  only to the snapshot just before the step being undone.
- **Before 05, baremetal topologies:** BCM-level rollback — reinstall
  the head node from ISO (02), re-run sections in order.
- **05–06:** `cm-kubernetes-setup` / `cm-runai-setup` are not cleanly
  reversible; snapshot the head-node VM and back up first (11.1).

On a Proxmox node (standard topology). The snapshot name says which step
it guards:

```bash
qm list                                         # find the head-node VM's id
HN_VMID="${HN_VMID:?set HN_VMID to the head-node VM id from qm list}"
qm snapshot "$HN_VMID" pre-05 --description "before cm-kubernetes-setup"
qm listsnapshot "$HN_VMID"
# undo (head node only; see the note above): qm rollback "$HN_VMID" pre-05
```
- **NIM / storage:** `kubectl delete` the created resources; StorageClasses
  are non-destructive to switch between plans (07: 7A / 7B).

## 11.3 — Operational handoff checklist

- [ ] 01 IP plan signed off; DNS forward+reverse verified (08.2)
- [ ] All 01–16 verification boxes checked (13 and 16 only where the site
      runs BCM HA / InfiniBand; 14 where the site is greenfield)
- [ ] **Health verdict gate (dgx-vitals, 08.6):** the wave's summary lists
      no host as **NOT HEALTHY**, **INCOMPLETE** or **UNREACHABLE** (the
      dgx-vitals 2.0 verdict vocabulary) — every GPU node reads
      **HEALTHY** — or each exception carries a documented waiver
      (host, verdict, reason, who accepted it) in the 10 deviation log
- [ ] Backups in 11.1 exist and are restorable (the DB dump test-restored
      per 11.1, table count recorded in 10)
- [ ] Certificate end dates recorded in 10 with a renewal owner — the
      inference certificate (6.3) expires after **365 days**, the ingress
      CA (6.1) after 3650 (`openssl x509 -enddate -noout -in …`)
- [ ] Credentials rotated from install-time values; real values in the
      site's vault — never in `site-vars.sh`, the docs or the rendered guide
- [ ] Administration endpoints documented (below)
- [ ] 10 deviation log reviewed for open items

## 11.4 — Administration endpoints **[restored]**

| Service | URL |
|---|---|
| BCM head node | `https://$BCM_HEADNODE.$DOMAIN:8081/base-view/` |
| Run:ai | `https://$FQDN_RUNAI/` |
| Run:ai inference | `https://$FQDN_INFERENCE/` |
| DGX BMCs | `https://<bmc-ip>/` (per 01 inventory) |
| Proxmox VE (head-node hypervisor, standard topology) | `https://<proxmox-node>:8006/` |
