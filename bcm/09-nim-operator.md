# 09 — NIM Operator

> **Prerequisites:** 06 complete (Run:ai healthy), and a **default
> StorageClass** the model-cache PVC can use. On **7A** that is
> `nfs-csi` (07 makes it the only default). **7B sets no default** —
> neither 07 nor `runbook.py storage --plan 7b` changes which class is
> the default — so a 7B site either sets one deliberately (and records
> it in 10) or edits `storageClass:` in 9.2 to a 7B class (`nfs-models`
> is the natural one). Check before 9.2: `kubectl get storageclass`
> shows the class 9.2 names.

## 9.1 — Secrets

```bash
kubectl create namespace nim-service
# NGC_API_KEY comes from the shell (06.1 exports it from ngc-api.key);
# bcm_require_secret refuses an unset value or a <PLACEHOLDER> value
# Both secrets are created on STDIN (printf builtin): the key never
# appears in argv (`ps`, shell history) — same form as 08.5. [unverified]
# --from-file=...=/dev/stdin reads the piped value; if not, use an
# interactive session and a mode-0600 file deleted afterwards.
bcm_require_secret NGC_API_KEY && printf '{"auths":{"nvcr.io":{"username":"$oauthtoken","password":"%s","auth":"%s"}}}' \
    "$NGC_API_KEY" "$(printf '%s' "\$oauthtoken:$NGC_API_KEY" | base64 -w0)" \
  | kubectl create secret generic ngc-secret -n nim-service \
      --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=/dev/stdin
bcm_require_secret NGC_API_KEY && printf '%s' "$NGC_API_KEY" \
  | kubectl create secret generic ngc-api-secret -n nim-service --from-file=NGC_API_KEY=/dev/stdin
# Optional: HuggingFace
# kubectl create secret -n nim-service generic hf-api-secret \
#   --from-literal=HF_TOKEN="<HF-TOKEN>"
```

## 9.2 — Deploy a model cache (example)

```yaml
# nim-cache-example.yaml — swap the model for the site's needs
apiVersion: apps.nvidia.com/v1alpha1
kind: NIMCache
metadata:
  name: llama-3-1-8b-instruct
spec:
  source:
    ngc:
      modelPuller: nvcr.io/nim/meta/llama-3.1-8b-instruct:1.3.3
      pullSecret: ngc-secret
      authSecret: ngc-api-secret
      model:
        engine: tensorrt_llm
        tensorParallelism: "1"
  storage:
    pvc:
      create: true
      storageClass: nfs-csi        # 7A default; a 7B site: its deliberate default, or nfs-models
      size: "50Gi"
      volumeAccessMode: ReadWriteMany
  resources: {}
```

```bash
kubectl apply -n nim-service -f nim-cache-example.yaml
kubectl get -n nim-service pvc,pv
kubectl get nimcaches.apps.nvidia.com -n nim-service
kubectl get pods -n nim-service
```

## 9.3 — Validate and clean up

```bash
kubectl logs -n nim-service -l app=nimbus 2>/dev/null | tail -20
# or the specific job pod:
kubectl get pods -n nim-service
kubectl get nimcaches.apps.nvidia.com -n nim-service      # -> NIMCACHE name
kubectl describe nimcache "${NIMCACHE:?set NIMCACHE to a name from the list above}" -n nim-service   # check conditions
# Remove when done testing:
kubectl delete nimcache "${NIMCACHE:?set NIMCACHE}" -n nim-service
```

- [ ] NIM cache reaches Ready; model servable through the inference
      endpoint (06.3)
