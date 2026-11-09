# 00b — Recommended Specs (Design-Time Sizing)

> Starting-point sizes for the machines around the GPUs: the BCM head
> node, the Kubernetes control plane, and the management / CPU worker
> nodes. These are the values we open the design conversation with —
> final sizing is worked out in the design session and signed off
> with the environment plan (01). GPU worker nodes are the BasePOD
> systems themselves (DGX / HGX) and are not sized here.

## The boilerplate

| Role | Cores | Memory |
|---|---|---|
| BCM head node (per node) | 8 | 16 GB |
| Kubernetes control plane | 8 or 16 | 32 GB |
| Management node / CPU worker | 8 or 16 | 32 GB |

## BCM head node

- **Storage: 500 GB minimum**, on top of the cores and memory above.
- The spec is the same however the head node is deployed — a single
  VM, a single baremetal host, or each member of an HA pair (13).
  Deployment form does not change the sizing.
- **The storage floor moved.** 250 GB was the older guidance;
  software images outgrow it. Start at 500 GB, and give image-heavy
  sites more.

## Kubernetes control plane

- 8 or 16 cores, 32 GB of memory. Both core counts are inside the
  standard envelope; the design session picks which one the site
  gets.

## Management nodes and CPU workers

- Default to the control-plane spec: 8 or 16 cores, 32 GB of memory.
  This covers DNS, Chrony, and jumpbox services (14) and CPU-only
  worker nodes.
- Scale down only when the node carries a single light service — a
  DNS-only or Chrony-only box does not need control-plane resources.
- Stacked roles or a large fleet argue for staying at (or above) the
  default; that call is made in the design session, not by formula.

## How to use this page

- Every number here is an opening position for the design
  conversation, not a quote. Fleet size, workload count, and image
  count move the final numbers.
- Record what the site actually settled on in 01 (environment & IP
  plan); log any deviation from these starting points in 10
  (decisions & issues).
