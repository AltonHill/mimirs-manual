# 99 — Role Mapping & Companyify Guide

## Role naming

This template names every machine and service by **role**. The table is
the canonical role → hostname/FQDN → IP pattern; the IP column shows the
template defaults from `00-variables.sh`.

| Template role | Template FQDN | IP (template default) | Notes |
|---|---|---|---|
| `bcm-headnode` | `bcm-headnode.$DOMAIN` | `$BCM_HEADNODE_IP` (10.10.15.10) | BCM head node |
| `k8s-ctrl-1` | `k8s-ctrl-1.$DOMAIN` | 10.10.15.50 | `$K8S_CTRL_IPS` |
| `k8s-ctrl-2` | `k8s-ctrl-2.$DOMAIN` | 10.10.15.51 | `$K8S_CTRL_IPS` |
| `k8s-ctrl-3` | `k8s-ctrl-3.$DOMAIN` | 10.10.15.52 | `$K8S_CTRL_IPS` |
| `gpu-worker-1` | `gpu-worker-1.$DOMAIN` | 10.10.15.53 | `$GPU_WORKER_IPS` |
| `gpu-worker-2` | `gpu-worker-2.$DOMAIN` | 10.10.15.54 | `$GPU_WORKER_IPS` |
| `gpu-worker-3` | `gpu-worker-3.$DOMAIN` | 10.10.15.55 | `$GPU_WORKER_IPS` |
| `gpu-worker-N` | `gpu-worker-N.$DOMAIN` | 10.10.15.53+ | extend `$GPU_WORKERS`/`$GPU_WORKER_IPS` per site |
| `dgx-<model>-N` | `dgx-<model>-N.$DOMAIN` | — (site-assigned) | per-type naming pattern, e.g. one name series per DGX model |
| `kgateway` | `kgateway.$DOMAIN` | `$IP_KGATEWAY` (10.10.13.200) | service IP (Usernet) |
| `runai` | `runai.$DOMAIN` | `$IP_RUNAI` (10.10.13.201) | service IP (Usernet) |
| `runai-inference` | `runai-inference.$DOMAIN` | `$IP_INFERENCE` (10.10.13.202) | service IP (Usernet) |

> **Provenance:** this file carries template names only. Source-system
> hostnames (lab or client) never enter it, or any other file in this
> library — roles are the identifiers.

## Companyify guide (pass 2)

To instantiate this template for a real site:

1. Copy the template directory.
2. Copy `00-variables.sh` to `site-vars.sh` and fill that in: `DOMAIN`, all IPs, node names (keep the
   role-based scheme or substitute the site's real hostnames —
   either works since every command references the variables).
3. Replace `example.internal` everywhere if not using `$DOMAIN`
   (it only appears in `00-variables.sh` defaults and prose).
4. Regenerate certificates (06.1, 06.3) — SANs embed FQDNs and IPs.
5. Delete this file (`99-role-mapping.md`) from the instantiated copy —
   it documents the template's role naming, not the deployment.

To reverse (template → real names for an existing site): the mapping is
mechanical — replace each `role.$DOMAIN` with the site's FQDN. Keep that
site mapping in the site's own copy, never in this template.
