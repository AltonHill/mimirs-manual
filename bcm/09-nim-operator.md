# 09 — NIM Operator

> **Prerequisites:** 06 complete (Run:ai healthy), 07 complete (NFS CSI
> default StorageClass available for model cache PVCs).

## 9.1 — Secrets

```bash
kubectl create namespace nim-service
# NGC_API_KEY comes from the shell (06.1 exports it from ngc-api.key);
# bcm_require_secret refuses an unset value or a <PLACEHOLDER> value
bcm_require_secret NGC_API_KEY && kubectl create secret -n nim-service docker-registry ngc-secret \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password="$NGC_API_KEY"
bcm_require_secret NGC_API_KEY && kubectl create secret -n nim-service generic ngc-api-secret \
  --from-literal=NGC_API_KEY="$NGC_API_KEY"
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
      storageClass: nfs-csi
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
