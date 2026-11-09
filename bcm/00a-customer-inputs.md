# 00a — Customer Inputs (Design-Time Prerequisites)

> Everything the site has to hand over before design can close.
> Request this list at design kickoff: the build stalls exactly
> where an input is missing, and two of these rows gate 01's
> sign-off outright. Values land in `00-variables.sh` and the CSVs —
> credentials always as placeholder names, never real secrets in
> any document.

| Input | Feeds | Needed by |
|---|---|---|
| Cluster identity: DNS domain, cluster name, organization name | 00-variables, 01 | Design close |
| Network allocations: CIDRs and gateways for the provisioning, storage, user, and OOB networks | 01 | **01 sign-off gate** |
| DNS and time: upstream DNS servers, control of the cluster's DNS zone, Chrony/NTP sources | 01, 14 | **01 sign-off gate** |
| Kubernetes ranges: service and pod CIDRs | 05 | Design close |
| Node inventory: hostname, MAC, and BMC address for every node | 03 (inventory CSV) | Before provisioning |
| BMC access: reachability from the head node, credentials as placeholder names | 03 (IPMI) | Before provisioning |
| BCM entitlement key (NVIDIA licensing portal → Base Command Manager) | 02 | Before install day |
| NGC credentials | 04, 09 | Before image work |
| Run:ai entitlement, plus TLS certificates covering the gateway and inference FQDNs | 06 | Before 06 |
| Head-node substrate: Proxmox capacity and access for the head-node VM, or a baremetal assignment | 00b specs, 02 | Design close |
| GPU node types in the fleet (DGX model, or OEM HGX builder) | 03, 12 | Design close |
| Storage direction: the site's NFS export details, or go-ahead for head-node-served NFS | 07 | Design close |
| User account list: usernames and sudo flags | 15 (accounts CSV) | Before handoff |

## How to use this page

- Send it at kickoff, not at install. Every row is cheaper to
  produce during design than to discover mid-build.
- Mark each row received in the site copy as it lands; an unmarked
  row at design close becomes an entry in
  `00d-design-assumptions.md` — the build proceeds on faith, and
  the risk is on the record.
