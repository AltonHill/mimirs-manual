# 02 — BCM Head Node Installation

> **Prerequisites:** 01 complete (IP plan approved). Physical/VM head node
> with the BCM 11 ISO attached.

## 2.1 — Install from ISO

1. Get entitlements: `https://ui.licensing.nvidia.com/`
   → Entitlements → Base Command Manager → Actions → View Key.
2. Download the ISO: `https://customer.brightcomputing.com/download-iso`
   - Product key: `$BCM_PRODUCT_KEY`
   - Version: BCM `$BCM_VERSION`, Arch x86_64/amd64, Linux Ubuntu 24.04,
     Hardware NVIDIA DGX.
3. Boot the head node from the ISO and complete the installer with the
   IP plan from 01 (provisioning interface on `$NET_PROVISION`).

## 2.2 — License the head node

```bash
request-license
```

`request-license` is **interactive**: it prompts for each field below,
and nothing expands a shell variable typed at its prompts. Print the non-secret answers
first, in your shell, and type them in. The product key is a credential
— paste it from the site's vault, never from a file:

```bash
printf 'Organization  %s\nCluster Name  %s\n' "$CLIENT_ORG" "$CLUSTER_NAME"
```

Provide when prompted (typed at the prompts — not a script):

```text
Product Key     $BCM_PRODUCT_KEY
Country         <COUNTRY>
State           <STATE>
Locality        <LOCALITY>
Organization    $CLIENT_ORG
Org Unit        <ORG-UNIT>
Cluster Name    $CLUSTER_NAME
```

Answer yes to the remaining prompts to generate and install the license.

## 2.3 — OS upgrades

```bash
# Run update twice to consolidate repos, then upgrade
apt update && apt update && apt upgrade -y
reboot
```

```bash
# Validate the new kernel (record it — images pin to it in 03)
uname -r
```

## 2.4 — Base configuration

Convenience aliases (append to `~/.bashrc`):

```bash
cat >> ~/.bashrc <<'EOF'
# BCM aliases
alias catlist='cmsh -c "category list"'
alias devlist='cmsh -c "device list"'
alias imglist='cmsh -c "softwareimage list"'
alias netlist='cmsh -c "network list"'
alias userlist='cmsh -c "user list"'
alias reboot-watch='while true; do devlist; sleep 5; clear; done'
# Kubernetes aliases
alias k='kubectl'
alias taintlist='kubectl get nodes -o jsonpath={range\ .items[*]}{.metadata.name}{"\t"}{.spec.taints}{"\n"}{end}'
# Run:ai aliases
alias validate-runai='kubectl get pods -n runai-backend && kubectl get pods -n runai && kubectl get pods -n gpu-operator'
# log collectors: a local copy at a reviewed commit (block below), never main piped to bash
alias get-clusterlogs='bash /root/runai-utils/runai_log_collector/start.sh'
alias get-gpulogs='bash /root/runai-utils/gpu_operator_log_dump/start.sh'
EOF
source ~/.bashrc
```

The two log-collector aliases run a **local copy** of the Run:ai
professional-services utilities, checked out at a commit you have read
— not whatever `main` serves at the moment of an incident (L10). Pin
the commit once — as `RUNAI_UTILS_COMMIT` in `site-vars.sh` (it ships
empty) — record it in 10, and re-pin deliberately:

```bash
git clone https://github.com/runai-professional-services/utilities.git /root/runai-utils
git -C /root/runai-utils log --oneline -5      # read what you are about to trust
: "${RUNAI_UTILS_COMMIT:?set RUNAI_UTILS_COMMIT in site-vars.sh to the reviewed commit hash, then source it}"
git -C /root/runai-utils checkout --detach "$RUNAI_UTILS_COMMIT"
```

> **Field note:** the lab's alias set also included per-category reboot
> aliases (`reboot-cp`, `reboot-dgx`, `reboot-all`) and NIM helpers —
> add them if you want; they aren't required.

Allow MAC-based device resolution. The edit is a real command: back up,
add the setting only if no active `AdvancedConfig` line exists, and
**never** rewrite an existing one automatically. That line may carry
other entries; add the value inside its braces by hand.

```bash
CMD_CONF=/cm/local/apps/cmd/etc/cmd.conf
cp -a "$CMD_CONF" "$CMD_CONF.bak-$(date +%F-%H%M%S)"
if grep -Eq '^[[:space:]]*AdvancedConfig[[:space:]]*=.*"DeviceResolveAnyMAC=1"' "$CMD_CONF"; then
  echo "NOTE: DeviceResolveAnyMAC=1 already set — nothing to change"
elif grep -Eq '^[[:space:]]*AdvancedConfig[[:space:]]*=' "$CMD_CONF"; then
  echo "STOP: an active AdvancedConfig line exists; add \"DeviceResolveAnyMAC=1\" inside its braces by hand:"
  grep -En '^[[:space:]]*AdvancedConfig' "$CMD_CONF"
else
  printf '%s\n' 'AdvancedConfig = { "DeviceResolveAnyMAC=1" }' >> "$CMD_CONF"
fi
```

Verify the **value**, not just the key, and restart only if it holds:

```bash
CMD_CONF=/cm/local/apps/cmd/etc/cmd.conf
n=$(grep -Ec '^[[:space:]]*AdvancedConfig[[:space:]]*=' "$CMD_CONF")
if [ "$n" -eq 1 ] && grep -Eq '^[[:space:]]*AdvancedConfig[[:space:]]*=.*"DeviceResolveAnyMAC=1"' "$CMD_CONF"; then
  echo "OK: AdvancedConfig carries DeviceResolveAnyMAC=1"
  systemctl restart cmd && systemctl is-active cmd
else
  echo "FAIL: want exactly one active AdvancedConfig line containing \"DeviceResolveAnyMAC=1\" (found $n) — cmd NOT restarted"
fi
```

> **[unverified]** The stock `cmd.conf` carries a commented
> `AdvancedConfig` example. Appending the directive at the end is treated
> here as equivalent to placing it after that comment. Confirm against
> the BCM 11 admin manual if CMDaemon doesn't pick it up after the
> restart.

## 2.5 — Create the BCM networks (01's plan)

The networks pass runs here, after BCM is installed. `cmsh` exists only
from this point on, so in 01 the pass was dry-run only. `--exec`
refuses to start if `cmsh` isn't on PATH. The pass is add-or-verify: a
network that already exists (`internalnet` from the installer, or
anything from an earlier run) gets a NOTE and is aligned to the plan, so
re-running it is safe.

```bash
scripts/runbook.py networks --networks networks.csv --dry-run   # review (from 01)
scripts/runbook.py networks --networks networks.csv --exec
cmsh -c "network; list"       # every row of the plan present, values as approved
```

(No `networks.csv` from 01? Drop `--networks`. The pass then uses the
stock 4-network plan from `site-vars.sh`.)

## Verification

- [ ] `cmsh -c "device list"` shows the head node healthy
- [ ] The value check above printed `OK: AdvancedConfig carries DeviceResolveAnyMAC=1`
- [ ] `cmsh -c "network; list"` matches 01's approved plan (2.5)
- [ ] `uname -r` matches `$KERNEL_VER` (or record the actual version and
      update `site-vars.sh` — 03 pins images to it)
