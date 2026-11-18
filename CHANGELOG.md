# Changelog — Mimir's Manual (library)

The library's own record, newest first. Each track keeps its older history
too (`bcm/CHANGELOG.md`); this file starts with the review-and-repair
program that took the library from "reads well" to "runs as written".
The library version stays `1.0.0` until the program closes.

## The M-program — review and repair (r13 → M7), 2026-11-16 to 2026-11-17

A read-only review of r12 found 2 critical, 11 high and 27 medium problems:
places where an operator following the text exactly would get hurt,
stranded, or handed a promise the build does not keep. Every finding got
a ruling; this entry is what changed, in operator terms.

### r13 — anonymization (Briefs M2 + M2A)

- **Was wrong:** the role-mapping page carried source-system hostnames,
  while saying it never had; a real place name stood in as the lab's name;
  the leak scan matched only strings that could never occur, so it always
  passed.
- **Changed:** source columns and names removed everywhere; the leak scan
  now reads a deny-list kept **outside** the repo, scans the whole tree
  (including .docx text and PDF metadata), and reports NOT RUN — never a
  pass — when the list is missing.

### r14 — variables and guards (Brief M3)

- **Was wrong:** the Kubernetes service and pod ranges swallowed every
  site network (and each other); the Cerberus renderer blanked interface
  names, so a rendered §02 config cut a node off its management network;
  the Cerberus scripts silently checked the template's addresses instead of
  the site's.
- **Changed:** kubeadm-conventional K8s ranges plus a CIDR overlap guard
  that fails closed; the renderer never substitutes an empty value; the
  Cerberus scripts take `--vars` and stop without a site file; a lint fails
  any `<placeholder>` inside a pasteable command.

### r15 — BCM correctness (Brief M4)

- **Was wrong:** disk layouts were set with kernel device names before the
  section that forbids them; the render baked credentials into the
  deliverable; the jumpbox hardening block locked you out when pasted; the
  Run:ai gateway "patch" could not apply; several passes died on "already
  exists".
- **Changed:** by-path layouts collected on one node per host type and
  gated before fleet provisioning; credentials never substituted (the guide
  keeps `$NAME`); lockout-safe jumpbox ordering; a complete-list merge patch
  for the gateway; passes are add-or-verify.

### r16 — BCM guard repairs (Brief M4A)

- **Changed:** the jumpbox guard survives `sudo` and asks for an explicit
  console acknowledgement; the fleet disk-layout gate is mechanical, with a
  logged, named override for a host type where by-path fails; `runbook.py
  vars` reports every value still equal to the shipped default; `check.sh
  --site` gives a teammate on site a meaningful pass/fail without the
  deny-list.

### r17 — Cerberus correctness (Brief M5)

- **Was wrong:** corosync's only required link shared a 1GbE port with the
  nightly backup; the capacity plan (~5 TB, 65% of raw) modelled the wrong
  failure; the PBS restore path could not authenticate; the "fencing drill"
  powered the node off, so fencing was never observed.
- **Changed:** corosync **link1 is required** (on the fabric); PBS backups
  ride **their own VLAN**; capacity is planned on the single-OSD failure —
  **3.3 TB, ≈43% of raw**, enforced by a pool quota (writes block at the
  ceiling); node loss described truthfully (degraded on two copies, no
  rebuild on 3 hosts); one token-form PBS repository string; a real
  isolation drill; every state-changing block pastes as one fail-closed
  unit.

### M7 — cold start (this release)

- **Was wrong:** no single path through the library — four quickstarts,
  section orders left to inference, no time estimates, jargon undefined,
  nothing said how the repo reaches the nodes; the unverifiable claims
  sat in a review appendix nobody on site would read; several vendor
  claims were wrong when checked (the BCM `networks` pass set a property
  BCM networks don't have; `disarm-ha` without its required mode; the
  BlueField-2 NIC-mode recipe on a BlueField-3; Squid's EOL date); and
  the review's 17 Lows, of which only 10 were still live at r18 (L5, L9
  and L11–L15 had already been fixed by M2A/M4/M5) — the live ones
  included a `--exec` that silently stayed a dry run, renders that kept
  stale files and broken links, a CSV row that lost a field, an
  unpinned pipe-to-shell, a certificate with no renewal reminder.
- **Changed:**
  - **Front door:** one numbered cold-start path (A design → B Cerberus →
    C head-node VM → D BCM → E deliverables) with per-section time
    **estimates**, a glossary, a versions line, each component's own
    Python floor, and the companion's provenance (canonical `01fb620`).
  - **Verification checklist:** `docs/verification-checklist.md` lists
    every claim the library takes on trust — verified (with the vendor
    source), corrected, or still unverified with how it clears — plus a
    read-only BCM first-contact block for the first head node.
  - **Vendor corrections:** `runbook.py networks` sets `baseaddress` +
    `netmaskbits` (BCM 11 Admin Manual); 05 runs `disarm-ha freeze`;
    00a sets BlueField-3 NIC mode with the one documented parameter; 05's
    HA check accepts `ha-manager config`'s `vm:100` form; Squid's EOL is
    2026-12-02.
  - **Renderers (both tracks):** a fresh directory every time (`--force`
    replaces only a previous render); links resolve inside the
    deliverable. Cerberus `render.py` now reads `site-vars.sh` by default
    and renders the template only when named — the BCM rule.
  - **Gates:** the Cerberus gate **fails** without `pdftotext`; the BCM
    gate fails with a named message without PyYAML.
  - **Small fixes:** `--exec` before the pass name now executes;
    networks CSV refuses an unquoted comma and drift from the site file;
    kernel pins check the kernel exists, in every image; one container
    toolkit; pinned, checksum-verified kubectl/helm; Run:ai log tools
    from a reviewed commit; certificate expiry in the handoff; head-node
    VM snapshots for rollback; a DB test-restore procedure; `DHCP_POOL`,
    `BCM_HA_VIP`, `PBS_DATASTORE_PATH` variables.
  - **Design brief** Version 1.1 (date unchanged), regenerated.

### M6 — promises and cross-references

- **Was wrong:** the 128-node diagram claimed every value came from the
  variables file while showing NFS on the head node and provisioning on
  the BMC network; the customer briefs still promised self-healing after a
  node loss, zero downtime, "no single points of failure" and ~5 TB; their
  .docx/.pdf had no generator, so they could only drift; cross-references
  pointed at sections and files that do not exist; the GPU burn-in was
  hard-wired to 3 nodes on a container too old for B300; the IB check
  expected one rate for every device; the front-door quickstart could not
  finish; BCM operators still edited the tracked template; the companion
  validation tool was never wired into the BCM flow.
- **Changed:**
  - **Diagrams:** the 128-node picture is labelled a reference design;
    NFS is the external Pure server on storagenet, provisioning rides
    internalnet, mgmtnet is OOB/BMC. The Cerberus architecture and cable
    diagrams show the PBS VLAN and both corosync links.
  - **Customer briefs:** corrected to the built design (separate networks
    and two required cluster links, PBS on its own VLAN, 3.3 TB, no
    rebuild after a node loss, what is single by design). The design
    brief's .docx and .pdf are regenerated by `proxmox-ceph/docs/build-
    briefs.py`, which also verifies the three formats agree;
    `check.sh` re-checks it.
  - **Cross-references:** a dangling-reference lint in **both** tracks'
    `check.sh` (section numbers, file names, pass names, section ranges);
    the rot it found is fixed (pass lists name all 8 passes, one section
    range, the PG dump at 06.7, the handoff covers 01–16, vCenter gone).
  - **BCM site file:** copy `bcm/00-variables.sh` to `bcm/site-vars.sh`
    (gitignored) and fill that in. Every `runbook.py` pass and
    `generate-nodes.py` read it (or `--vars FILE`, before or after the
    pass name), stop without it, and refuse the template.
  - **BCM render tripwire:** a live credential in the variables **file**
    now makes render refuse (exit 3, nothing written) — including one
    hidden as a `${NAME:-value}` default; one held only in your shell is
    still a warning.
  - **Passwords off the command line:** §03's BMC password, §06.7's
    database dump and §08/§09's NGC secrets reach their tools on stdin.
  - **Burn-in (08.5):** one pod per `$GPU_WORKERS` name; NGC pull secret
    wired; container pinned to `pytorch:25.11-py3` (CUDA 13.0.2, driver ≥
    580.95.05), with a first-kernel check that stops cleanly on an
    unsupported GPU.
  - **InfiniBand (16):** expected rate per device class from NVIDIA's DGX
    guides (ConnectX-7: 400; DGX B300's ConnectX-8: 800).
  - **Companion:** BCM 08.6 runs dgx-vitals; the 11.3 handoff gates on its
    verdicts (no host NOT HEALTHY, INCOMPLETE or UNREACHABLE, or a
    documented waiver).
  - **Skeleton inventories** use RFC 7042 documentation MACs; the older
    skeleton pattern is still refused.
  - **Front door:** each track renders to its own directory, with `--vars`.
