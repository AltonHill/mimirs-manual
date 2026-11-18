# 08 — Validation

> **Prerequisites:** 05–07 complete. 8.6 (dgx-vitals) is the health
> verdict the 11.3 handoff checklist gates on.

## 8.1 — Node and GPU health

On each GPU worker (or via BCM):

```bash
nvidia-smi
nvidia-smi -q            # ECC errors, retired pages, clocks
nvidia-smi topo -m       # NVLink/NVSwitch topology — compare against the BOM
nvidia-container-cli info
lspci -nn | grep -i -E 'nvidia|ethernet'
ip -br link
ip -br addr
```

```bash
cmsh -c "device list"                       # all nodes [OK]
kubectl get nodes -o wide                   # all Ready
```

Expected `nvidia-smi`: all GPUs visible, driver/CUDA matching the 04
baseline. Record actuals for the site.

- [ ] All expected GPU devices visible; no critical ECC/retired-page alerts
- [ ] NVLink/NVL topology matches the BOM
- [ ] Container runtime sees the GPUs (`nvidia-container-cli info`)

## 8.2 — DNS and service endpoints **[restored]**

```bash
nslookup $FQDN_RUNAI          # → $IP_RUNAI
nslookup $FQDN_KGATEWAY       # → $IP_KGATEWAY
nslookup $FQDN_INFERENCE      # → $IP_INFERENCE
curl -vk https://$FQDN_RUNAI/ | head -5
```

## 8.3 — Workload smoke test

```bash
kubectl get pods -n runai-backend
kubectl get pods -n runai
kubectl get pods -n gpu-operator
kubectl get storageclass
```

> **Field note [client]:** the client's platform validation and its
> known-working baseline are the reference for "healthy" — BCM 11,
> k8s 1.34, Run:ai 2.26.x, GPU Operator v26.3.1, NFS CSI 4.13.4. Any
> deviation from these versions is a deliberate decision; log it in 10.

## 8.4 — Node roles **[restored]**

Label nodes for workload scheduling:

```bash
kubectl get nodes
# GPU workers (add CPU-worker hostnames to the list); --overwrite: re-runnable
for n in $GPU_WORKERS; do
  kubectl label node "$n" node-role.kubernetes.io/worker=true --overwrite
done
```

- [ ] Every node labeled per its role; GPU nodes carry NVIDIA capacity
      labels (`nvidia.com/gpu.count` etc.)

## 8.5 — GPU burn-in

Run every GPU hot before handing the cluster over. The burn is a
PyTorch matmul loop (no dataset, no network) — if a GPU can't sustain
it, you want to know now, not mid-training.

**The container.** `nvcr.io/nvidia/pytorch:25.11-py3`, pinned. Why this
tag, from NVIDIA's own pages:

- B300 is compute capability **10.3** ([CUDA GPU compute capability
  table](https://developer.nvidia.com/cuda-gpus)); compiler support for
  `sm_103` first shipped in **CUDA 12.9** ([CUDA 12.9 release
  notes](https://docs.nvidia.com/cuda/archive/12.9.0/cuda-toolkit-release-notes/index.html)).
  The old pin, `24.10-py3`, is built on CUDA 12.6.2 ([24.10 release
  notes](https://docs.nvidia.com/deeplearning/frameworks/pytorch-release-notes/rel-24-10.html))
  — it predates `sm_103`, so on B300 it can fail with "no kernel image"
  instead of testing the GPUs.
- `25.11-py3` is built on CUDA 13.0.2 ([25.11 release
  notes](https://docs.nvidia.com/deeplearning/frameworks/pytorch-release-notes/rel-25-11.html)),
  and CUDA 13.0 Update 2 needs Linux driver **≥ 580.95.05** ([CUDA 13.0.2
  release notes](https://docs.nvidia.com/cuda/archive/13.0.2/cuda-toolkit-release-notes/index.html)),
  which is 04's driver baseline. A newer tag needs a newer driver: match
  the tag's CUDA to the driver 04 actually installed.
- **[unverified]** NVIDIA's [frameworks support
  matrix](https://docs.nvidia.com/deeplearning/frameworks/support-matrix/index.html)
  lists "NVIDIA Blackwell" (GB200 as its example) and does not name B300
  or `sm_103`. So the script below checks before it burns: it prints the
  GPU's compute capability and the container's compiled architectures,
  and stops with a clear message if a first kernel cannot launch —
  record that output for the site.

**Pull secret.** nvcr.io needs one; the Job references it
(`imagePullSecrets`). Created on stdin — the key never appears in argv
(`ps`, shell history). **[unverified]** `--from-file=…=/dev/stdin` is
assumed to read the piped value; if it doesn't, create the secret in an
interactive session from a mode-0600 file and delete the file after.

```bash
kubectl create namespace gpu-burnin --dry-run=client -o yaml | kubectl apply -f -
# NGC_API_KEY from your shell (06.1); bcm_require_secret refuses a placeholder
bcm_require_secret NGC_API_KEY && printf '{"auths":{"nvcr.io":{"username":"$oauthtoken","password":"%s","auth":"%s"}}}' \
    "$NGC_API_KEY" "$(printf '%s' "\$oauthtoken:$NGC_API_KEY" | base64 -w0)" \
  | kubectl create secret generic ngc-secret -n gpu-burnin \
      --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=/dev/stdin
kubectl get secret ngc-secret -n gpu-burnin    # exists (re-run: "already exists" is fine)
```

Label the GPU nodes (one label, used by the Job below). The pod count
comes from the variables: one pod per name in `$GPU_WORKERS`, never a
hard-coded number.

```bash
kubectl label nodes $GPU_WORKERS burnin/gpu=true --overwrite
BURNIN_NODES="$(wc -w <<<"$GPU_WORKERS")"
echo "burn-in: $BURNIN_NODES GPU node(s) from GPU_WORKERS"
test "$BURNIN_NODES" -ge 1 || echo "STOP: GPU_WORKERS is empty"
```

```yaml
# gpu-burnin.yaml — ConfigMap (the script) + Job (one pod per GPU node).
# ${BURNIN_NODES} is filled by the apply block below — from $GPU_WORKERS.
apiVersion: v1
kind: ConfigMap
metadata:
  name: gpu-burnin-script
  namespace: gpu-burnin
data:
  burn.py: |
    import os, sys, time, torch
    size = int(os.environ.get("BURN_SIZE", "8192"))
    dur = int(os.environ.get("BURN_SECS", "1800"))  # 30 min default
    devs = [f"cuda:{i}" for i in range(torch.cuda.device_count())]
    if not devs:
        sys.exit("burn: no GPUs visible to the container")
    cap = torch.cuda.get_device_capability(0)
    print(f"burn: compute capability {cap[0]}.{cap[1]}; container built for "
          f"{torch.cuda.get_arch_list()}", flush=True)
    try:
        (torch.ones(8, 8, device=devs[0]) @ torch.ones(8, 8, device=devs[0])).sum().item()
    except RuntimeError as e:
        sys.exit(f"burn: STOP — a first kernel would not launch on this GPU ({e}). "
                 f"The container does not support this architecture: pick a tag "
                 f"whose CUDA supports it (08.5), then re-run.")
    print(f"burn: {len(devs)} GPUs, {size}x{size} matmul, {dur}s", flush=True)
    ts = [torch.randn(size, size, device=d) for d in devs]
    torch.cuda.synchronize()
    end, it = time.time() + dur, 0
    while time.time() < end:
        for t in ts:
            t @ t
        torch.cuda.synchronize()
        it += 1
        if it % 20 == 0:
            print(f"iter {it}", flush=True)
    print(f"done: {it} iterations", flush=True)
---
apiVersion: batch/v1
kind: Job
metadata:
  name: gpu-burnin
  namespace: gpu-burnin
spec:
  parallelism: ${BURNIN_NODES}    # = words in $GPU_WORKERS; one pod per node
  completions: ${BURNIN_NODES}
  template:
    metadata:
      labels:
        app: gpu-burnin
    spec:
      restartPolicy: Never
      nodeSelector:
        burnin/gpu: "true"
      imagePullSecrets:
      - name: ngc-secret             # created above, in this namespace
      containers:
      - name: burn
        image: nvcr.io/nvidia/pytorch:25.11-py3   # pinned: CUDA 13.0.2, driver >= 580.95.05 (see above)
        command: ["python", "-u", "/burn/burn.py"]
        env:
        - name: BURN_SECS
          value: "1800"
        resources:
          limits:
            nvidia.com/gpu: 8     # adjust to the node's GPU count
        volumeMounts:
        - name: burn-script
          mountPath: /burn
      volumes:
      - name: burn-script
        configMap:
          name: gpu-burnin-script
```

```bash
# fill ${BURNIN_NODES} from the label block above, then apply
sed "s/\${BURNIN_NODES}/${BURNIN_NODES:?run the label block above first}/g" gpu-burnin.yaml > gpu-burnin.applied.yaml
grep -q 'BURNIN_NODES' gpu-burnin.applied.yaml \
  && echo "STOP: BURNIN_NODES not filled in gpu-burnin.applied.yaml" \
  || kubectl apply -f gpu-burnin.applied.yaml
kubectl get jobs -n gpu-burnin -w                        # wait for Complete
kubectl logs -n gpu-burnin -l app=gpu-burnin --tail=5    # capability line, iter counts, "done"
```

While it runs, watch thermals/power on a couple of nodes:

```bash
nvidia-smi dmon -s pucvmet   # power, util, clocks, temp per GPU
dmesg | grep -i xid          # expect: nothing
```

- [ ] All pods `Complete`; iteration counts consistent across nodes
      (an outlier node is suspect — investigate before handoff)
- [ ] No Xid errors; temps stabilize below throttling

### Via Run:ai

**GUI:** Workloads → New Training workload → name `gpu-burnin`,
project, image `nvcr.io/nvidia/pytorch:25.11-py3` (the pin above), 8 GPUs, command
`python -u /burn/burn.py` (mount the script via a ConfigMap-backed
volume, or paste the compact form into the command field). Same
`BURN_SECS` env.

**CLI (v2):** submit from a manifest —
`runai workload submit --file workload.yaml --project <project>`
(see `runai workload submit --help` and the CLI reference at
run-ai-docs.nvidia.com — flags move between releases, so verify
against the installed CLI). The Job YAML above is the source of truth
for image, command, and GPU count.

## 8.6 — Node vitals with dgx-vitals (the handoff gate)

The burn-in proves the GPUs can run hot; the library's companion tool
proves each node is healthy and files the evidence — full log, health
card and a tracker-ready summary per host. It lives in `dgx-vitals/` at
the library root (see its `README.md` and `QUICKSTART.md`). Run it from
the head node or a jump host that can SSH to every GPU worker's OS
address (Python 3.10+, stdlib only; the NCCL step needs `nccl-tests` on
the nodes).

```bash
# Run from the library root. One row per GPU worker, from the variables
# (hostname = the name BCM and the tracker use; address = its OS address).
paste -d, <(tr ' ' '\n' <<<"$GPU_WORKERS") <(tr ' ' '\n' <<<"$GPU_WORKER_IPS") \
  | { echo hostname,address; cat; } > vitals-hosts.csv
cat vitals-hosts.csv
# 1. Preflight — read-only; every host must say READY before step 2
python3 dgx-vitals/dgx-vitals.py --hosts vitals-hosts.csv --sudo-password --check
# 2. The wave
python3 dgx-vitals/dgx-vitals.py --hosts vitals-hosts.csv --sudo-password --out vitals-results
```

The run's last lines name its summary file and list every host whose
verdict is **NOT HEALTHY**, **INCOMPLETE** or **UNREACHABLE** (the
dgx-vitals 2.0 vocabulary; see `dgx-vitals/README.md`, "Verdicts").

- [ ] Every GPU worker's verdict is **HEALTHY** — or each exception has a
      documented waiver in 10 (host, verdict, reason, who accepted it).
      This is the gate 11.3 checks at handoff.
- [ ] The wave's results folder (summary, health cards, FULLLOGs) is
      copied off-host with the 11.1 backups
