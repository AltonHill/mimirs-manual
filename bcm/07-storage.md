# 07 — Storage

> **Prerequisites:** 05 complete (k8s healthy).
>
> Two plans, pick one:
> - **Simple (7A):** one default NFS StorageClass on a single share.
>   Fastest path; fine when one pool serves everything.
> - **Advanced (7B):** separate data / scratch / models StorageClasses
>   plus static PVs for pre-existing project and software shares.
>   This is the client's full design — use it when workloads need
>   different reclaim policies per data type.
>
> **Deciding on site — take 7B if any answer is yes, otherwise 7A:**
>
> | Question | Why it forces 7B |
> |---|---|
> | Must deleting a PVC **keep** the data for some workloads but **discard** it for scratch? | 7A has one class, one reclaim policy; 7B has `nfs-data`/`nfs-models` (Retain) and `nfs-scratch` (Delete) |
> | Are there **pre-existing** shares (project data, software trees) that pods must mount as they are? | Only 7B builds static PVs for data that already exists |
> | Should the NIM/model cache live on its own share (separate quota, sizing or performance)? | 7B's `nfs-models` is that share |
> | Will the storage team want per-type quotas or snapshot schedules (data vs scratch)? | Needs separate subdirectories/exports — 7B's layout |
>
> Record the answers and the choice in 10. Note the consequence for 09:
> 7A sets a default StorageClass, 7B does not (end of 7B).

## Design reference (both plans)

Logical layout from the client storage architecture:

```text
AI Pod storage
|-- User home service (NFS-backed homes, mounted on head node)
|-- Pure Storage NFS
|   |-- /shared/data      (datasets, Retain)
|   |-- /shared/scratch   (ephemeral, Delete)
|   |-- /shared/sw        (software, static PV)
|   `-- /shared/models    (model cache, Retain)
`-- Existing project/archive platform
    `-- /shared/proj      (pre-existing data, static PV, ReadOnlyMany)
```

Keep logical paths consistent (e.g. `/shared/...`) so researchers can
move between HPC and AI environments without relearning paths. The exact
NFS export names may differ from the logical mount paths.

**The exports, canonically:** the StorageClasses point at exports
derived from the variables, on `$NFS_SERVER`: `$NFS_SHARE` for 7A, and
`$NFS_SHARE/data`, `$NFS_SHARE/scratch`, `$NFS_SHARE/models` for 7B (the
template's `/data` gives `/data/data` and so on). The manual YAML below
and `scripts/runbook.py storage` produce **the same** classes, exports,
and default-class outcome. The `/shared/...` names above are logical
mount paths, not export names. If the site's exports are laid out
differently, change `NFS_SHARE` (or the pass), not one path's YAML.

> **Writing the YAML files from this page:** they carry `$NFS_SERVER` /
> `$NFS_SHARE`. The rendered guide has them filled in. From the
> unrendered template, either use `scripts/runbook.py storage`, which
> generates the same YAML from the variables, or let the shell expand
> them (`envsubst < nfs-sc.yaml > nfs-sc.rendered.yaml`). kubectl never
> expands variables. Each apply below refuses a file that still contains
> a literal `$NFS_SERVER` or `$NFS_SHARE`.

## 7.1 — Head-node NFS mount

```bash
mkdir -p $NFS_MOUNT
mount -t nfs ${NFS_SERVER}:${NFS_SHARE} $NFS_MOUNT
df -h $NFS_MOUNT

# Persist in /etc/fstab (back it up first):
cp -a /etc/fstab /etc/fstab.bak-$(date +%F)
printf '# Pure NFS\n%s:%s\t%s\tnfs\tdefaults,nfsvers=4.1,rsize=1048576,wsize=1048576,_netdev 0 0\n' \
  "$NFS_SERVER" "$NFS_SHARE" "$NFS_MOUNT" >> /etc/fstab
mount -a && df -h $NFS_MOUNT
```

## 7.2 — NFS CSI driver (both plans)

> **Automate it:** `scripts/runbook.py storage --plan 7a` (or `7b`)
> generates the StorageClass YAML from `$NFS_SERVER`/`$NFS_SHARE` and
> runs the driver install below. `--dry-run` prints everything first.

```bash
# re-runnable: upgrade --install converges an existing release
helm repo add csi-driver-nfs https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts --force-update
helm upgrade --install csi-driver-nfs csi-driver-nfs/csi-driver-nfs \
  --namespace kube-system --version $NFS_CSI_VERSION \
  --set externalSnapshotter.enabled=true \
  --set controller.runOnControlPlane=true
```

Validate:

```bash
watch kubectl --namespace=kube-system get pods --selector="app.kubernetes.io/instance=csi-driver-nfs"
kubectl -n kube-system get pod -o wide -l app=csi-nfs-controller
```

Node-level NFS check first (from the advanced plan — do this before
creating classes):

```bash
showmount -e $NFS_SERVER
# from each worker: mount -t nfs ${NFS_SERVER}:${NFS_SHARE} /mnt/test && umount /mnt/test
```

---

## 7A — Simple plan: one default StorageClass

```yaml
# nfs-sc.yaml — adjust server/share per site
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-csi
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: nfs.csi.k8s.io
parameters:
  server: $NFS_SERVER
  share: $NFS_SHARE
reclaimPolicy: Retain
volumeBindingMode: Immediate
allowVolumeExpansion: true
mountOptions:
  - nfsvers=4.1
  - hard
```

```bash
grep -q '[$]NFS_' nfs-sc.yaml && echo "STOP: nfs-sc.yaml has unexpanded variables" || kubectl apply -f nfs-sc.yaml
kubectl get storageclass
kubectl describe storageclass nfs-csi
```

Make it the only default (replacing `local-path`, if the cluster has it).
`runbook.py storage --plan 7a` runs these same two steps:

```bash
kubectl get storageclass local-path >/dev/null 2>&1 \
  && kubectl patch storageclass local-path -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}' \
  || echo 'NOTE: no local-path StorageClass — nothing to un-default'
# verify: exactly one default, and it is nfs-csi
test "$(kubectl get storageclass --no-headers | grep -c '(default)')" -eq 1 \
  && kubectl get storageclass --no-headers | grep '(default)' | grep -q '^nfs-csi ' \
  && echo 'OK: exactly one default StorageClass (nfs-csi)' \
  || { echo 'FAIL: default StorageClass is not exactly nfs-csi'; kubectl get storageclass; }
```

Validate end to end:

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-nfs-test
spec:
  accessModes: [ReadWriteMany]
  resources: { requests: { storage: 2Gi } }
  storageClassName: nfs-csi
EOF
kubectl get pvc pvc-nfs-test
# write/read/persistence test with a pod, then clean up:
kubectl delete pvc pvc-nfs-test
```

---

## 7B — Advanced plan: data / scratch / models + static PVs **[restored]**

> The client's full implementation. Use when scratch must auto-clean,
> data must never auto-delete, and pre-existing project/software shares
> need mounting.

```yaml
# nfs-data.yaml — Retain: conservative for authoritative data
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-data
provisioner: nfs.csi.k8s.io
parameters:
  server: $NFS_SERVER
  share: $NFS_SHARE/data
reclaimPolicy: Retain
volumeBindingMode: Immediate
allowVolumeExpansion: true
mountOptions:
  - nfsvers=4.1
  - hard
---
# nfs-scratch.yaml — Delete: scratch auto-cleans (approve first)
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-scratch
provisioner: nfs.csi.k8s.io
parameters:
  server: $NFS_SERVER
  share: $NFS_SHARE/scratch
reclaimPolicy: Delete
volumeBindingMode: Immediate
allowVolumeExpansion: true
mountOptions:
  - nfsvers=4.1
  - hard
---
# nfs-models.yaml — Retain: model cache
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-models
provisioner: nfs.csi.k8s.io
parameters:
  server: $NFS_SERVER
  share: $NFS_SHARE/models
reclaimPolicy: Retain
volumeBindingMode: Immediate
allowVolumeExpansion: true
mountOptions:
  - nfsvers=4.1
  - hard
```

```bash
grep -q '[$]NFS_' nfs-data.yaml nfs-scratch.yaml nfs-models.yaml \
  && echo "STOP: unexpanded variables in the 7B YAML" \
  || kubectl apply -f nfs-data.yaml -f nfs-scratch.yaml -f nfs-models.yaml
kubectl get storageclass
```

7B sets no default class: neither this page nor `runbook.py storage
--plan 7b` changes which StorageClass is the default. **Anything that
relies on a default class needs one** — 09's model-cache PVC does: on
a 7B site either set a default deliberately (and record it in 10) or
point 09.2's `storageClass:` at a 7B class (`nfs-models`).

Static PV for pre-existing project data (no provisioning — it already exists):

```yaml
# project-static.yaml — adjust server/share/capacity per site
apiVersion: v1
kind: PersistentVolume
metadata:
  name: project-pv
spec:
  capacity:
    storage: 5Pi   # confirm actual capacity before applying
  accessModes:
    - ReadOnlyMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: project-static
  mountOptions:
    - nfsvers=4.1
    - hard
  csi:
    driver: nfs.csi.k8s.io
    volumeHandle: project-existing
    volumeAttributes:
      server: $NFS_SERVER
      share: /shared/proj   # pre-existing export — the site's real path
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: project
  namespace: <project-namespace>
spec:
  accessModes:
    - ReadOnlyMany
  resources:
    requests:
      storage: 5Pi
  storageClassName: project-static
  volumeName: project-pv
```

Same pattern for the software share (`/shared/sw`) — static PV/PVC,
`ReadOnlyMany`, `Retain`.

Cross-node validation (advanced plan):

```bash
# dynamic scratch PVC + pod test, then a pod on a second node
# mounting the same claim to prove cross-node visibility
kubectl get pvc -A
kubectl get pv
```

## Verification (both plans)

- [ ] `kubectl get storageclass` shows the intended classes (default as intended)
- [ ] Test PVC binds; a test pod writes, reads back, and survives pod restart
- [ ] `showmount -e $NFS_SERVER` reachable from worker nodes
- [ ] (7B) Static project/software PVs bound; `Retain` on data/models confirmed
