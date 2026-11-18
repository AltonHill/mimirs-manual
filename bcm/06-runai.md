# 06 — NVIDIA Run:ai Deployment

> **Prerequisites:** 05 complete (healthy k8s + GPU Operator). DNS and TLS
> decisions from 01 finalized.

## 6.1 — Stage credentials and certificates

```bash
install -d -m 0700 /cm/images/runai
cd /cm/images/runai
```

Self-signed CA for the Run:ai ingress (prefer site PKI when available):

```bash
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout private.key -out ca.crt \
  -subj "/CN=${FQDN_RUNAI}" \
  -addext "subjectAltName=DNS:${FQDN_RUNAI},DNS:*.${FQDN_RUNAI},IP:${IP_RUNAI}"
chmod 0600 private.key
```

> **Field note:** the lab concatenated cert+key into `full-chain.pem`
> and the wizard accepted it — keep the private key separate
> (`chmod 0600`) and only merge if the wizard requires it.

NGC API key (from `https://org.ngc.nvidia.com/account/api-key`):

```bash
# Paste the key into ngc-api.key (mode 0600). Never commit the value.
vi /cm/images/runai/ngc-api.key
chmod 0600 /cm/images/runai/ngc-api.key
export NGC_API_KEY="$(cat /cm/images/runai/ngc-api.key)"
```

Expected layout:

```text
/cm/images/runai/ca.crt
/cm/images/runai/private.key
/cm/images/runai/ngc-api.key
```

## 6.2 — Run the installer

**Prepare the Run:ai system nodes first.** The backend placement in 6.5
schedules only onto nodes labeled `node-role.kubernetes.io/runai-system`
(`requiredDuringScheduling`). Nothing else in this runbook applies that
label. `cm-runai-setup` may set it too; this runbook has not verified
that. Apply it explicitly. That's harmless if the installer also does
it, and the dependency is no longer silent: without the label, 6.5's
`helm upgrade --atomic --wait` leaves backend pods Pending and rolls back
after 15 minutes.

```bash
# idempotent: --overwrite re-applies the same value on a re-run
for n in $K8S_CTRL; do
  kubectl label node "$n" node-role.kubernetes.io/runai-system=true --overwrite
done
# verify: every $K8S_CTRL node carries it — prints MISSING otherwise
for n in $K8S_CTRL; do
  v=$(kubectl get node "$n" -o jsonpath='{.metadata.labels.node-role\.kubernetes\.io/runai-system}')
  [ "$v" = true ] && echo "OK      $n" || echo "MISSING $n"
done
```

```bash
cm-runai-setup
```

| Prompt | Selection |
|---|---|
| Action | Deploy |
| Mode | Run:ai self-hosted |
| NGC API key | `/cm/images/runai/ngc-api.key` |
| FQDN | `$FQDN_RUNAI` |
| `.crt` / `.key` | `/cm/images/runai/ca.crt`, `/cm/images/runai/private.key` |
| Username | `$RUNAI_ADMIN_USER` |
| Password | `$RUNAI_ADMIN_PASSWORD` (enter securely) |
| Deploy cluster | Yes |
| Control-plane category | `$CAT_CP` |
| Version | `$RUNAI_VERSION` |
| Run:ai gateway IP | `$IP_RUNAI` |
| Kourier (inference) IP | `$IP_INFERENCE` |
| Save file | `/root/cm-runai-setup.conf` (default) |

Both DNS checks in the wizard must pass.

```bash
cm-runai-setup -c /root/cm-runai-setup.conf
```

Monitor (second session):

```bash
watch kubectl get pods -n runai-backend   # wait for Running/Completed
watch kubectl get pods -n runai
```

## 6.3 — Inference TLS **[restored + corrected]**

> The lab's inference-secret command had cert/key swapped
> (`--cert` pointed at the merged PEM, `--key` at the CRT). Corrected
> below — the client docs confirm this mapping.

```bash
cd /cm/images/runai
openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout inference.key -out inference.crt \
  -subj "/CN=${FQDN_INFERENCE}" \
  -addext "subjectAltName=DNS:${FQDN_INFERENCE},DNS:*.${FQDN_INFERENCE},IP:${IP_INFERENCE}"
chmod 0600 inference.key
kubectl create secret tls runai-cluster-inference-tls-secret -n runai \
  --cert=/cm/images/runai/inference.crt \
  --key=/cm/images/runai/inference.key
```

> **Expiry:** this certificate is good for **365 days** (`-days 365`);
> the 6.1 CA for 3650. Record both end dates in 10 with a renewal owner
> — the 11.3 handoff checks they are there:
> `openssl x509 -enddate -noout -in /cm/images/runai/inference.crt`.
> Renewal is this block again (new key, same secret name — delete the
> old `runai-cluster-inference-tls-secret` first, or `kubectl create …
> --dry-run=client -o yaml | kubectl apply -f -`).

## 6.4 — Gateway listeners (kgateway) **[restored]**

> Run:ai on kgateway ships **without** the `https-workloads` listener —
> `*.runai.<domain>` (workspaces, applications) won't resolve until you
> add it. Same story for `*.runai-inference.<domain>` on the
> Knative/Kourier side. This was a real deployment fix.

Websocket upgrades for the gateway:

```yaml
# http-listener-policy.yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: HTTPListenerPolicy
metadata:
  name: runai-gateway-websocket
  namespace: runai-backend
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: runai-gateway
  upgradeConfig:
    enabledUpgrades:
    - websocket
```

```bash
kubectl apply -f http-listener-policy.yaml
```

> **Version note:** `HTTPListenerPolicy` was correct for the kgateway
> in this deployment, but kgateway **v2.5 removed the CRD** — newer
> versions use `ListenerPolicy` with the websocket config under
> `spec.default.httpSettings`. Check your installed kgateway version
> (`kubectl get crd | grep kgateway`) and use the matching API. The
> migration is mechanical (same fields, new parent).

Add the missing workloads listener. This is a **`kubectl patch`**, not an
`apply`. The fragment has no `apiVersion`/`kind`/`metadata`, so `apply`
rejects it.

> **WARNING — a merge patch replaces the whole `listeners` list.**
> `--type merge` (JSON merge patch) does not merge list items. Whatever
> list you send *becomes* the Gateway's listeners. A trimmed example that
> lists only `https-workloads` **deletes** `https` and every other
> listener the installer created, and takes down the Run:ai UI with them.
> Run:ai's listener stack is interdependent. The patch file must carry
> the **complete** list: every listener the Gateway has now, plus the new
> one. The steps below snapshot the live list, build the patch, and
> refuse to apply it if a dry run shows any current listener would
> disappear.

**1. Snapshot the live Gateway** (rollback copy + the names you must keep):

```bash
cd /cm/images/runai
kubectl get gateway runai-gateway -n runai-backend -o yaml > runai-gateway-before.yaml
printf '{"spec":{"listeners":%s}}\n' \
  "$(kubectl get gateway runai-gateway -n runai-backend -o jsonpath='{.spec.listeners}')" \
  > runai-gateway-listeners-before.json      # exact rollback patch
kubectl get gateway runai-gateway -n runai-backend \
  -o jsonpath='{range .spec.listeners[*]}{.name}{"\n"}{end}' | sort > listeners-live.txt
cat listeners-live.txt
```

**2. Write the patch file: the complete list.** The template shows the
listener set this deployment had (`https`), plus `https-workloads`. If
step 1 listed **any other listener**, copy its full entry from
`runai-gateway-before.yaml` into the list below *before* applying. The
shell fills in `$FQDN_RUNAI` while it writes the file (kubectl itself
never expands variables):

```bash
cat > runai-gateway-listeners-patch.yaml <<EOF
# merge patch for Gateway runai-gateway — COMPLETE listeners list
spec:
  listeners:
  - name: https
    protocol: HTTPS
    port: 443
    hostname: "${FQDN_RUNAI:?source site-vars.sh first}"
    tls:
      mode: Terminate
      certificateRefs:
      - kind: Secret
        name: runai-backend-tls
  - name: https-workloads
    protocol: HTTPS
    port: 443
    hostname: "*.${FQDN_RUNAI:?source site-vars.sh first}"
    tls:
      mode: Terminate
      certificateRefs:
      - kind: Secret
        name: runai-backend-tls
EOF
```

**3. Dry-run, prove nothing is dropped, then patch:**

```bash
kubectl patch gateway runai-gateway -n runai-backend --type merge \
  --patch-file runai-gateway-listeners-patch.yaml --dry-run=server \
  -o jsonpath='{range .spec.listeners[*]}{.name}{"\n"}{end}' | sort > listeners-after.txt
comm -23 listeners-live.txt listeners-after.txt > listeners-dropped.txt
if [ -s listeners-after.txt ] && [ ! -s listeners-dropped.txt ] && grep -qx https-workloads listeners-after.txt; then
  kubectl patch gateway runai-gateway -n runai-backend --type merge \
    --patch-file runai-gateway-listeners-patch.yaml
else
  echo "REFUSED — the patch would drop these listeners (add them to the patch file):"
  cat listeners-dropped.txt
fi
```

**4. Verify: query the Gateway, then hit the endpoint.**

```bash
# every listener present and Programmed=True (https + https-workloads + any kept):
kubectl get gateway runai-gateway -n runai-backend \
  -o jsonpath='{range .status.listeners[*]}{.name}{"  Programmed="}{.conditions[?(@.type=="Programmed")].status}{"\n"}{end}'
# nothing that was there before is gone (must print nothing):
kubectl get gateway runai-gateway -n runai-backend \
  -o jsonpath='{range .spec.listeners[*]}{.name}{"\n"}{end}' | sort | comm -23 listeners-live.txt -
# the base UI still answers, and a workload hostname now reaches the gateway
# (any HTTP status, even 404, means the https-workloads listener matched it;
# 000 = no TLS listener for that name):
curl -sk -o /dev/null -w 'base      %{http_code}\n' "https://$FQDN_RUNAI/"
curl -sk -o /dev/null -w 'workload  %{http_code}\n' \
  --resolve "workload-check.$FQDN_RUNAI:443:$IP_RUNAI" "https://workload-check.$FQDN_RUNAI/"
```

Rollback (restores the exact pre-patch list):
`kubectl patch gateway runai-gateway -n runai-backend --type merge --patch-file runai-gateway-listeners-before.json`.

For `*.runai-inference.$DOMAIN`: check what Knative/Kourier created
first — don't just attach it to `runai-gateway`:

```bash
kubectl get svc -n knative-serving -o wide
kubectl get routes.serving.knative.dev -A
kubectl get gateways -A
kubectl get httproutes -A
kubectl get secret runai-cluster-inference-tls-secret -n runai
```

The inference wildcard must resolve to the Kourier/Knative external
entry point with a certificate covering both the base inference
hostname and its wildcard (see 6.3). Create the missing Gateway +
HTTPRoute there if absent, referencing the inference TLS secret.

## 6.5 — Backend placement (NATS + gateway) **[restored]**

Pin the Run:ai backend's stateful services to the control plane so a
worker reboot can't take down NATS:

```bash
export RELEASE=runai-backend RELEASE_NS=runai-backend
# snapshot before changing anything:
helm history $RELEASE -n $RELEASE_NS > helm-history-before.txt
helm get values $RELEASE -n $RELEASE_NS -a > values-before.yaml
```

```yaml
# runai-backend-system-placement.yaml
global:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: node-role.kubernetes.io/runai-system
                operator: Exists
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
nats:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: node-role.kubernetes.io/runai-system
                operator: Exists
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
```

```bash
helm upgrade $RELEASE runai-backend/control-plane -n $RELEASE_NS \
  --version $RUNAI_VERSION --reuse-values \
  -f runai-backend-system-placement.yaml --dry-run --debug > backend-dryrun.txt
# review, then:
helm upgrade $RELEASE runai-backend/control-plane -n $RELEASE_NS \
  --version $RUNAI_VERSION --reuse-values \
  -f runai-backend-system-placement.yaml --atomic --wait --timeout 15m
kubectl rollout restart statefulset/runai-backend-nats -n runai-backend
kubectl rollout status statefulset/runai-backend-nats -n runai-backend --timeout=15m
# Never delete all NATS members simultaneously.
```

Keep the gateway **VIP** and the gateway **pods** off the GPU workers. These are
two different mechanisms, and the earlier text used one for the other:

- **VIP announcement:** the label
  `node.kubernetes.io/exclude-from-external-load-balancers` tells the
  LoadBalancer machinery (MetalLB's speakers, kube-proxy LB handling) not
  to announce or serve LoadBalancer IPs from that node. It controls
  **where `$IP_RUNAI` is reachable through**. It does **not** schedule or
  move pods, and restarting the Deployment doesn't move them either. It
  applies to *every* LoadBalancer service, the inference (Kourier) IP
  included. That is intended here: no service VIP lands on a GPU worker.

```bash
for n in $GPU_WORKERS; do
  kubectl label node "$n" node.kubernetes.io/exclude-from-external-load-balancers=true --overwrite
done
```

- **Pod placement:** the gateway Deployment is generated by the kgateway
  controller from the Gateway. A `kubectl patch deployment` would be
  reverted, so placement goes on kgateway's own knob, a
  `GatewayParameters` pod template referenced from the Gateway. It
  carries the same `runai-system` affinity and control-plane toleration
  as the backend above. The API group/version
  (`gateway.kgateway.dev/v1alpha1`) and the field path
  (`spec.kube.podTemplate` → `affinity`, `tolerations`) are verified
  against the kgateway 2.5 API reference
  (https://kgateway.dev/docs/envoy/latest/reference/api/, checked
  2026-11-17); the installed version may differ, so check the CRD first
  (`kubectl explain gatewayparameters.spec.kube.podTemplate`).
  **[unverified]** the proxy-pod label the verify step selects on
  (`gateway.networking.k8s.io/gateway-name`) — the reference does not
  name it; the step says so loudly if it matches nothing. If the Gateway already
  references a GatewayParameters (`.spec.infrastructure.parametersRef`,
  or on its GatewayClass), add the affinity to *that* object instead of
  creating a second one.

```bash
kubectl get gateway runai-gateway -n runai-backend -o jsonpath='{.spec.infrastructure}{"\n"}'   # existing parametersRef?
kubectl apply -f - <<'EOF'
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: runai-gateway-placement
  namespace: runai-backend
spec:
  kube:
    podTemplate:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: node-role.kubernetes.io/runai-system
                    operator: Exists
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
EOF
# spec.infrastructure is a map, not a list: this merge patch touches only parametersRef
kubectl patch gateway runai-gateway -n runai-backend --type merge -p \
  '{"spec":{"infrastructure":{"parametersRef":{"group":"gateway.kgateway.dev","kind":"GatewayParameters","name":"runai-gateway-placement"}}}}'
kubectl rollout status deployment/runai-gateway -n runai-backend --timeout=10m
# verify: every gateway pod is on a runai-system node (prints WRONG-NODE otherwise)
sys_nodes=$(kubectl get nodes -l node-role.kubernetes.io/runai-system -o name | sed 's|node/||')
gw_pods=$(kubectl get pods -n runai-backend -l gateway.networking.k8s.io/gateway-name=runai-gateway \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.nodeName}{"\n"}{end}')
[ -n "$gw_pods" ] || echo "NO GATEWAY PODS FOUND — check the label selector: kubectl get pods -n runai-backend --show-labels"
printf '%s\n' "$gw_pods" | while read -r pod node; do
  [ -n "$pod" ] || continue
  printf '%s\n' "$sys_nodes" | grep -qx "$node" && echo "OK         $pod on $node" || echo "WRONG-NODE $pod on $node"
done
```

Re-check the placement after any Run:ai upgrade: the installer owns the
Gateway and may reset `spec.infrastructure`.

Rollback: remove the labels; drop the placement with `kubectl patch gateway runai-gateway -n runai-backend --type json -p '[{"op":"remove","path":"/spec/infrastructure/parametersRef"}]'`; backend: `helm rollback $RELEASE "${GOOD_REVISION:?set from helm-history-before.txt}" -n $RELEASE_NS --wait --timeout 15m`.

## 6.6 — Validate

```bash
curl -vk https://$FQDN_RUNAI/
kubectl get runaiconfig -A
kubectl get pods -n runai-backend
kubectl get pods -n runai
nslookup $FQDN_RUNAI
```

- [ ] `https://$FQDN_RUNAI/` loads the Run:ai UI
- [ ] All `runai` / `runai-backend` pods Running
- [ ] DNS resolves `$FQDN_RUNAI` → `$IP_RUNAI`

## 6.7 — Back up the control plane

```bash
install -d -m 0700 /root/runai-backup && cd /root/runai-backup
set +x   # no tracing while secrets are in play

# Discover the live names first, then export the four values in this shell:
#   export PG_POD=... PG_SECRET=... PG_USER=... PG_DB=...
kubectl -n runai-backend get pods      # -> PG_POD
kubectl -n runai-backend get secrets    # -> PG_SECRET
# every use below is ${PG_*:?} — an unset value stops that command

kubectl get runaiconfig runai -n runai -o jsonpath='{.spec}' > runai_config_backup.yaml
helm get values runai-backend -n runai-backend > runai_control_plane_values.yaml
POSTGRES_PASSWORD=$(kubectl -n runai-backend get secret "${PG_SECRET:?export PG_SECRET first}" \
  -o jsonpath='{.data.postgres-password}' | base64 -d)
# The password reaches the pod on STDIN (printf builtin), never in argv —
# `env PGPASSWORD=... pg_dump` on the exec command line would show in `ps`
# on the head node. [unverified] the PostgreSQL pod has sh + cat (stock
# postgres images do); if not, open `kubectl exec -it ... -- bash`, export
# PGPASSWORD there by hand (read -rs), and run pg_dump in that session.
printf '%s' "$POSTGRES_PASSWORD" | kubectl -n runai-backend exec -i "${PG_POD:?export PG_POD first}" -- \
  sh -c 'PGPASSWORD="$(cat)" pg_dump -U "$1" "$2"' sh "${PG_USER:?export PG_USER first}" "${PG_DB:?export PG_DB first}" > runai_db_backup.sql
unset POSTGRES_PASSWORD
chmod 0600 runai_config_backup.yaml runai_control_plane_values.yaml runai_db_backup.sql
test -s runai_config_backup.yaml && test -s runai_db_backup.sql && echo "backups OK"
```

> The four `PG_*` values come from the live cluster (names are
> Helm-generated, so they're discovered, not templated). Never store
> dumps or decoded secrets in the docs.
