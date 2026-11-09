# 00d — Design Assumptions (Design-Time Register)

> What the design takes on faith, written down while taking it.
> An assumption nobody recorded is a surprise with a date on it;
> a recorded one is a managed risk. The register is written during
> the design session and reviewed at 01's sign-off gate.

| # | Assumption | If it proves wrong | Checked at |
|---|---|---|---|
| A1 | Air-cooled DGX (A, H, B series) are interchangeable for this pattern; OEM HGX nodes follow it with their own category + image | NVL72-class rack-scale systems break the pattern — rack fabric and liquid cooling become their own design track | Design review |
| A2 | A Proxmox substrate with HA is available to host the head-node VM | Topology decision reopens: baremetal, or BCM HA with its quorum and replication overhead | Design close |
| A3 | The head node can serve NFS over the provisioning network unless the site provides storage | Storage plan (07) changes shape, and possibly the network plan with it | 01 sign-off |
| A4 | Disk-layout XMLs can be finalized during the build | They cannot be written from a desk — 12's collection step needs live hardware, so layouts land later than the paper design | 03, 12 |
| A5 | Network allocations handed over at design time are final | Renumbering after 01 sign-off invalidates certificates, DNS, and installer configs downstream | 01 gate |
| A6 | BCM, NGC, and Run:ai entitlements are procured before install day | The schedule slips; the design does not change | Kickoff |

## Register rules

- **Append, don't rewrite.** A new assumption gets the next ID.
  One that stops being true is struck through and superseded by a
  new entry — the history is the point.
- **A broken assumption moves.** When one proves wrong during the
  build, it becomes a deviation in 10 (decisions & issues) citing
  its register ID, so the paper trail runs design → build without
  a gap.
- Inputs still missing at design close (see
  `00a-customer-inputs.md`) are recorded here as assumptions until
  they arrive.
