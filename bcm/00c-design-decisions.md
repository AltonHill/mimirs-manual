# 00c — Design Decisions (Design-Time Choices)

> The choices the design session makes and 01's sign-off ratifies.
> Sizing lives in `00b-recommended-specs.md`; this page is the other
> half of design — what was picked, from which options, and where
> the build executes it. Mark the chosen option in the site copy
> during the session.

| Decision | Options | Standard / default | Executed in |
|---|---|---|---|
| Head-node topology | Single VM on Proxmox · single baremetal · BCM HA on 2× baremetal · BCM HA on 2× VMs | Single VM on Proxmox | 02 (13 if BCM HA) |
| Head-node HA layer | Proxmox HA under a single VM · BCM HA (`cmha-setup`, quorum disk, MySQL replication) | Proxmox HA — no cmha, no quorum disk | 13 |
| Fleet scale | Worker count; the reference build is 128 nodes in blocks of 32 | — | 01 |
| Control plane | Node count and etcd placement | 3 nodes, etcd stacked on all three (odd count) | 05 |
| Node sizing | Per `00b-recommended-specs.md` | The boilerplate values | 00 |
| Storage plan | 7A: one default StorageClass · 7B: data / scratch / models classes | By workload mix | 07 |
| Network plan | Stock 4-network plan · extended fabrics (GPU east-west, InfiniBand, IPMI as extra rows) | Stock, unless the fabrics are physically present | 01, 16 |
| Node-type mix | One BCM category + one software image per GPU node type | Follows the fleet inventory | 03 |
| Inference exposure | Run:ai inference gateway in scope or not | — | 06 |

## Notes

- **Scale past the reference build is a design event, not a
  bigger install.** Layouts beyond 128 nodes need their own
  IP-planning pass before 01 can sign off.
- A decision revisited after sign-off is a deviation: it goes in
  10 (decisions & issues) with the reason, and this page's site
  copy is updated to match. The template copy never changes.
