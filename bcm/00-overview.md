# B300 Deployment Runbook — Template

> **What this is:** a merged, executable deployment recipe for a BCM 11 +
> Kubernetes + NVIDIA Run:ai stack on DGX GPU nodes. It combines the
> Chaska lab's canonical step-by-step procedure (the prescription) with the
> fixes and additions discovered during the client deployment (the
> description). Every step is copy-pasteable and ordered; every section
> ends with a verification.
>
> **Hardware scope:** written for air-cooled DGX (A, H, and B series
> treated as interchangeable) — the bulk of deployments through next
> year. The same pattern covers HGX 8-GPU OEM nodes (Dell, HPE, Lenovo)
> and Cisco UCS GPU nodes: one BCM category + image per node type.
> NVL72 rack-scale (Vera Rubin, shipping since Sept 2026) is future
> scope — same provisioning pattern, but rack fabric and liquid cooling
> are their own design track. The client ran 2× Cisco UCS GPU nodes;
> the lab ran DGX A100 + DGX H200.
>
> **What this is not:** a copy of either source. Lab and client used
> different networks, hostnames, and IPs — all unified here under
> role-based names. See `99-role-mapping.md` for the translation tables.

## How to use this template

1. Fill in `00-variables.sh` for the site (the "companyify" step — it's the
   only file that changes per deployment).
2. `source 00-variables.sh` on the machine you're working from.
3. Work sections **01 → 16 in order**. Each section lists its
   prerequisites at the top. (13 is optional HA and runs before 05;
   14 is greenfield infrastructure and runs before 01.)
4. Every command block is copy-pasteable as written (variables already
   exported). **Verify** each section's checks before moving on.

The design-time front matter lives in the other `00-` pages:
[`00a-customer-inputs.md`](00a-customer-inputs.md) (what the site must provide),
[`00b-recommended-specs.md`](00b-recommended-specs.md) (sizing starting points),
[`00c-design-decisions.md`](00c-design-decisions.md) (the choices 01's sign-off ratifies),
and [`00d-design-assumptions.md`](00d-design-assumptions.md) (what design takes on faith).
All four are settled before 01's plan is signed off; none is an
execution step.

## Design-time front matter

| File | What it holds |
|---|---|
| [Customer inputs](00a-customer-inputs.md) | What the site must provide before design closes |
| [Recommended specs](00b-recommended-specs.md) | Design-time sizing: head node, control plane, management nodes |
| [Design decisions](00c-design-decisions.md) | The choices 01's sign-off ratifies |
| [Design assumptions](00d-design-assumptions.md) | Register of what design takes on faith |
| [`00-variables.sh`](00-variables.sh) | The companyify target — every site value, filled in first |

## Execution order

| # | Section | What happens |
|---|---------|--------------|
| 01 | [Environment & IP plan](01-environment-ip-plan.md) | Networks, DNS, host inventory — fill the blanks, get sign-off |
| 02 | [BCM head node install](02-bcm-headnode-install.md) | ISO install, licensing, OS upgrades, base config |
| 03 | [Images, categories, nodes](03-images-categories-nodes.md) | Software images, BCM categories, node provisioning, IPMI |
| 04 | [NVIDIA drivers](04-nvidia-drivers.md) | Driver install/repair inside images, container toolkit |
| 05 | [Kubernetes](05-kubernetes.md) | `cm-kubernetes-setup`, cluster bring-up, operator install |
| 06 | [Run:ai](06-runai.md) | Certs, `cm-runai-setup`, ingress/inference, validation, backups |
| 07 | [Storage](07-storage.md) | Head-node NFS mount + NFS CSI driver + StorageClasses |
| 08 | [Validation](08-validation.md) | GPU, platform, and workload smoke tests |
| 09 | [NIM Operator](09-nim-operator.md) | NGC secrets, NIMCache, model validation |
| 10 | [Decisions & issues](10-decisions-issues.md) | Deviation log — what differed from plan and why |
| 11 | [Backup, recovery, handoff](11-backup-recovery-handoff.md) | What to back up, how to roll back, handoff checklist |
| 12 | [Disk layouts](12-disk-layouts.md) | disklayout.xml selection, editing, by-path guidance |
| 13 | [BCM HA (optional)](13-bcm-ha.md) | `cmha-setup` — only if doing head-node HA; runs **before** 05 |
| 14 | [Greenfield infra](14-greenfield-infra.md) | Jumpbox, DNS (Technitium/site), Chrony — before 01 goes live |
| 15 | [User management](15-user-management.md) | BCM users from CSV, sudoers drop-in, image push |
| 16 | [InfiniBand / GPU fabric](16-infiniband.md) | OFED, subnet manager, IPoIB, fabric bandwidth check |

`scripts/generate-nodes.py` turns a CSV inventory into the `cmsh`
provisioning script for 03 — required at fleet scale (74+ nodes).
`scripts/generate-users.py` does the same for BCM accounts in 15
(including the sudoers drop-in via `--sudoers`).

`scripts/runbook.py` is the pass runner: composable sub-passes, one per
runbook job (`vars`, `images`, `nodes`, `users`). `--dry-run` is the
default — it prints exactly what would run; `--exec` asks for
confirmation first. New passes get added as the data supports them.
See `README.md` for the full usage picture.

`99-role-mapping.md` holds the lab→template and client→template identifier
translations, plus the companyify guide.

## Conventions

- `bash` blocks are run on the **BCM head node** unless labeled otherwise.
- `<PLACEHOLDER>` values must be replaced; UPPER_CASE names in code
  blocks are variables from `00-variables.sh`.
- **Field notes** (blockquote callouts) mark where the client deployment
  diverged from the lab procedure — read them, don't skip them.
- Sections 02–06 assume a fresh BCM 11 head node. For brownfield, start
  at the section matching your state and verify forward.

## Source map

- Lab procedure ("Chaska Runbook", 23 sections) → the prescriptive steps.
  All 23 are present; the 7 the client docs dropped are restored here and
  marked **[restored]**.
- Client deployment docs (Wayland-Complete, 18 sources) → fixes,
  additions, and working values. Marked **[client]** where they diverge.
- A prior completeness audit (`13 - Completeness and Source Mapping
  Report.md` in the client package) verified the client sources; this
  template builds on it rather than repeating it.
