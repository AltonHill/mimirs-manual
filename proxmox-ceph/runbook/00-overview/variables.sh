#!/usr/bin/env bash
# ============================================================================
# Cerberus — site variables (single source of truth for all runbook sections)
# ============================================================================
#
# HOW TO USE
#   1. This file is the pristine TEMPLATE — never edit it in place. Copy it
#      to the track root as site-vars.sh and fill in the site's real values
#      there (every default below — networks and IPs included — is a
#      template value, not just the ones marked PLACEHOLDER):
#         cp runbook/00-overview/variables.sh site-vars.sh
#      Keep site-vars.sh out of version control if it contains sensitive
#      site data.
#   2. Later sections and the node scripts source the SITE file. The
#      runbook blocks find it through CERBERUS_TRACK (the track root of the
#      repo copy on the node — set once per node, see the track README,
#      "Working-directory contract"), so they work from any directory:
#         ( set -euo pipefail
#           source "${CERBERUS_TRACK:?...}/site-vars.sh"
#           ... )
#         scripts/net-verify.sh [--vars FILE]   # default: <track>/site-vars.sh
#      The scripts fail closed if no site file is given or found — they
#      never fall back to this template.
#   3. Anything still PLACEHOLDER, or still identical to a template default
#      (cerberus_audit_placeholders reports both), MUST be resolved before
#      running the steps that consume it.
#
# CONVENTIONS
#   - Node names are customer-anonymized role names: node-1 / node-2 / node-3.
#     The optional 4th node reuses the same scheme (node-4, .14 suffixes).
#   - ${VAR:-default} form everywhere, so `set -u` sourcing never trips on
#     an unset variable. Export overrides from the environment win.
#     Exception: the host-resolved interface names (below) are deliberately
#     left undefined — under `set -u`, using one before it is exported fails.
#   - Networks below are PLACEHOLDER defaults from the design doc; change the
#     CIDR and every derived IP moves with it.
#
# Reference: docs/references.md (official docs index).

set -u

# ------------------------------------------------------------------ cluster
CLUSTER_NAME="${CLUSTER_NAME:-cerberus}"
CEPH_RELEASE="${CEPH_RELEASE:-tentacle}"   # PVE 9.2 default for new installs. Do NOT use Squid (estimated EOL 2026-12-02 per docs.ceph.com/en/latest/releases/, checked 2026-11-17 — re-check at deploy time).
DOMAIN="${DOMAIN:-PLACEHOLDER}"            # e.g. lab.example.com — used for FQDNs in /etc/hosts

# ------------------------------------------------------------------ nodes
# Hostnames must be FINAL before `pvecm create` — Proxmox does not support
# renaming/re-IPing a node after it joins a cluster.
NODE1_HOSTNAME="${NODE1_HOSTNAME:-node-1}"
NODE2_HOSTNAME="${NODE2_HOSTNAME:-node-2}"
NODE3_HOSTNAME="${NODE3_HOSTNAME:-node-3}"
NODE4_HOSTNAME="${NODE4_HOSTNAME:-node-4}"   # optional 4th node

# ------------------------------------------------- management network (1GbE)
# Physical path: Broadcom 5719 OCP 4x1GbE -> SN2201 mgmt switch(es).
# Carries: Proxmox management, corosync link0, XCC/BMC, PBS host admin
# (GUI/SSH). NOT backup traffic: that rides its own VLAN on the fabric
# (PBS_VLAN below), so the 02:00 backup window never shares this single
# 1GbE link with corosync link0.
MGMT_NET="${MGMT_NET:-10.10.10.0/24}"      # PLACEHOLDER
MGMT_GW="${MGMT_GW:-10.10.10.1}"           # PLACEHOLDER
MGMT_MTU="${MGMT_MTU:-1500}"
NODE1_MGMT_IP="${NODE1_MGMT_IP:-10.10.10.11}"
NODE2_MGMT_IP="${NODE2_MGMT_IP:-10.10.10.12}"
NODE3_MGMT_IP="${NODE3_MGMT_IP:-10.10.10.13}"
NODE4_MGMT_IP="${NODE4_MGMT_IP:-10.10.10.14}"   # optional

# XCC/BMC interfaces (same L2 as MGMT_NET; set in XCC, see 00a-physical-bringup)
NODE1_XCC_IP="${NODE1_XCC_IP:-10.10.10.21}"
NODE2_XCC_IP="${NODE2_XCC_IP:-10.10.10.22}"
NODE3_XCC_IP="${NODE3_XCC_IP:-10.10.10.23}"
NODE4_XCC_IP="${NODE4_XCC_IP:-10.10.10.24}"   # optional
XCC_USER="${XCC_USER:-USERID}"                # Lenovo factory login name — change it (00a §2); the password is never stored here

# ------------------------------------------- Ceph public network (client)
# Physical path: bond0 (active-backup over 2x 200G BlueField-3) tagged VLAN 100
# -> SN5610-A / SN5610-B breakout legs. MTU 9000 end to end.
# Carries: Ceph client traffic (MON/MGR/OSD client ports), VM/guest traffic
# (untagged on vmbr1, i.e. VLAN 100 — see 02 §3 for tagged guests), and
# corosync link1 (REQUIRED — the node's public IP is its ring1 address).
CEPH_PUBLIC_NET="${CEPH_PUBLIC_NET:-10.20.20.0/24}"   # PLACEHOLDER
CEPH_PUBLIC_VLAN="${CEPH_PUBLIC_VLAN:-100}"
CEPH_PUBLIC_MTU="${CEPH_PUBLIC_MTU:-9000}"
NODE1_PUBLIC_IP="${NODE1_PUBLIC_IP:-10.20.20.11}"
NODE2_PUBLIC_IP="${NODE2_PUBLIC_IP:-10.20.20.12}"
NODE3_PUBLIC_IP="${NODE3_PUBLIC_IP:-10.20.20.13}"
NODE4_PUBLIC_IP="${NODE4_PUBLIC_IP:-10.20.20.14}"     # optional

# -------------------------------------- Ceph cluster network (replication)
# Physical path: same bond0, tagged VLAN 200 -> SN5610-A / SN5610-B.
# Carries: OSD replication, recovery, backfill, heartbeat. No gateway.
CEPH_CLUSTER_NET="${CEPH_CLUSTER_NET:-10.20.30.0/24}" # PLACEHOLDER
CEPH_CLUSTER_VLAN="${CEPH_CLUSTER_VLAN:-200}"
CEPH_CLUSTER_MTU="${CEPH_CLUSTER_MTU:-9000}"
NODE1_CLUSTER_IP="${NODE1_CLUSTER_IP:-10.20.30.11}"
NODE2_CLUSTER_IP="${NODE2_CLUSTER_IP:-10.20.30.12}"
NODE3_CLUSTER_IP="${NODE3_CLUSTER_IP:-10.20.30.13}"
NODE4_CLUSTER_IP="${NODE4_CLUSTER_IP:-10.20.30.14}"   # optional

# ------------------------------------------- OSD drives (per node, by-id)
# The two PM1743s of each node, as /dev/disk/by-id/nvme-<model>_<serial>
# paths recorded in 00a §4. by-id on purpose (ruled): model + serial is how
# the 03 destructive wipe/create proves its target is a PM1743 and not the
# boot volume. Site data — fill per node; PLACEHOLDER stops 03 Step 5.
NODE1_OSD1_BYID="${NODE1_OSD1_BYID:-PLACEHOLDER}"
NODE1_OSD2_BYID="${NODE1_OSD2_BYID:-PLACEHOLDER}"
NODE2_OSD1_BYID="${NODE2_OSD1_BYID:-PLACEHOLDER}"
NODE2_OSD2_BYID="${NODE2_OSD2_BYID:-PLACEHOLDER}"
NODE3_OSD1_BYID="${NODE3_OSD1_BYID:-PLACEHOLDER}"
NODE3_OSD2_BYID="${NODE3_OSD2_BYID:-PLACEHOLDER}"
NODE4_OSD1_BYID="${NODE4_OSD1_BYID:-PLACEHOLDER}"   # optional
NODE4_OSD2_BYID="${NODE4_OSD2_BYID:-PLACEHOLDER}"   # optional

# ------------------------------------------------------- fabric switches
# Two NVIDIA SN5610 800GbE (Cumulus 5.x) carry the Proxmox storage fabric.
# InfiniBand belongs to the GPU side — out of scope for this runbook.
FABRIC_SWITCH_A="${FABRIC_SWITCH_A:-sn5610-a}"
FABRIC_SWITCH_B="${FABRIC_SWITCH_B:-sn5610-b}"
FABRIC_MTU="${FABRIC_MTU:-9216}"   # Cumulus default; verify, never set below 9000
# 800G OSFP ports on each switch that take the OSFP-800G -> 4x QSFP112-200G
# breakout DACs. VERIFY against each switch's own /etc/cumulus/ports.conf —
# SN5610 800G-capable port numbers are per-switch, do not assume.
SWITCH_A_DAC_PORT="${SWITCH_A_DAC_PORT:-swp1}"
SWITCH_B_DAC_PORT="${SWITCH_B_DAC_PORT:-swp1}"
# 1GbE management switch(es)
MGMT_SWITCH="${MGMT_SWITCH:-sn2201-mgmt}"
# SN2201 ports facing the nodes' OCP + XCC links and the PBS host's mgmt
# port (cable plan: ports 1-6 = node-1..3 OCP/XCC, 7 = PBS host). NVUE range.
MGMT_SWITCH_PORTS="${MGMT_SWITCH_PORTS:-swp1-7}"

# --------------------------------- host interface names (resolve per node)
# Kernel interface names for the BlueField-3 200G ports and the OCP 1GbE port
# are host-specific. Resolve them in runbook/02-network §1 (PCI -> kernel
# mapping + physical port identification), then export BEFORE sourcing:
#     export BF3_P1=enp134s0f0np0 BF3_P2=enp134s0f1np0 MGMT_IF=enp1s0f0
# HOST-RESOLVED — INTENTIONALLY UNDEFINED. BF3_P1, BF3_P2 and MGMT_IF are
# deliberately NOT assigned in this file (not even as empty defaults): they
# stay undefined until discovered on the node and exported there. That keeps
# them out of render.py's substitution set, so the rendered guide carries
# ${BF3_P1} etc. through untouched, and the 02-network guard
# (cerberus_require_vars) refuses to generate config until they are set.
# Do not add defaults for them here or in a site copy.

# ------------------------------------------------------------- core services
DNS_SERVERS="${DNS_SERVERS:-10.10.10.2}"       # PLACEHOLDER, space-separated if several
NTP_SERVERS="${NTP_SERVERS:-pool.ntp.org}"     # PLACEHOLDER — prefer site NTP
MAILTO="${MAILTO:-ops@example.com}"           # PLACEHOLDER — backup/alert mail target

# ------------------------------------------------- Proxmox Backup Server
# Dedicated physical host (NOT one of the cluster nodes).
# Two addresses (H3):
#   PBS_HOST_IP  — its MGMT_NET address: admin only (GUI :8007, SSH).
#   PBS_VLAN_IP  — its address on the backup VLAN (fabric). Every backup
#                  and restore path uses THIS one: `pvesm add pbs --server`,
#                  the repository string, backup-drill.sh.
PBS_HOST_IP="${PBS_HOST_IP:-10.10.10.100}"    # PLACEHOLDER — PBS admin (mgmt) address
PBS_DATASTORE="${PBS_DATASTORE:-ceph-backups}"
PBS_DATASTORE_PATH="${PBS_DATASTORE_PATH:-/mnt/backup-cerberus}"   # on the PBS host: the datastore's filesystem path (06.3)
PBS_STORAGE_ID="${PBS_STORAGE_ID:-pbs-cerberus}"   # PVE storage ID for the PBS target (06.6)
# PBS user + API token. The auth-id is "$PBS_BACKUP_USER!$PBS_AUTH_NAME";
# build it with cerberus_pbs_auth_id (below), never by hand in a double-
# quoted string (interactive history expansion eats "!..."). PBS_AUTH_NAME
# is deliberately not called *TOKEN*: names matching the credential class
# are never substituted by render and trip its live-secret tripwire — the
# token's NAME is not a secret. The token's SECRET is PBS_PASSWORD: never
# in this file (render refuses to run if a live value is here); read it at
# run time with `read -rs PBS_PASSWORD; export PBS_PASSWORD`.
PBS_BACKUP_USER="${PBS_BACKUP_USER:-pve-backup@pbs}"
PBS_AUTH_NAME="${PBS_AUTH_NAME:-pve-token}"
PBS_FINGERPRINT="${PBS_FINGERPRINT:-PLACEHOLDER}"   # PBS TLS cert SHA256 (06.2) — public, not a secret
BACKUP_JOB_ID="${BACKUP_JOB_ID:-cerberus-nightly}"  # PVE backup job ID (06.6)

# ------------------------------------------- PBS backup VLAN (fabric, H3)
# Physical path: nodes — bond0 tagged VLAN PBS_VLAN (the same 2x200G DAC
# legs as Ceph); PBS host — its fabric NIC on dedicated SN5610 ports
# (PBS_SWITCH_A_PORT / _B_PORT, access VLAN PBS_VLAN). See 00a §6 cable plan.
# Carries: PVE -> PBS backup and restore traffic only. No gateway.
PBS_NET="${PBS_NET:-10.20.40.0/24}"           # PLACEHOLDER
PBS_VLAN="${PBS_VLAN:-300}"
PBS_MTU="${PBS_MTU:-9000}"                    # must match the PBS host's NIC MTU end to end
PBS_VLAN_IP="${PBS_VLAN_IP:-10.20.40.100}"    # PBS host on the backup VLAN
NODE1_PBS_IP="${NODE1_PBS_IP:-10.20.40.11}"
NODE2_PBS_IP="${NODE2_PBS_IP:-10.20.40.12}"
NODE3_PBS_IP="${NODE3_PBS_IP:-10.20.40.13}"
NODE4_PBS_IP="${NODE4_PBS_IP:-10.20.40.14}"   # optional
# SN5610 ports for the PBS host's fabric NIC (one per switch; the cable
# type follows the PBS NIC — site BOM item, verify). VERIFY against each
# switch's ports.conf, like SWITCH_*_DAC_PORT.
PBS_SWITCH_A_PORT="${PBS_SWITCH_A_PORT:-swp2s0}"
PBS_SWITCH_B_PORT="${PBS_SWITCH_B_PORT:-swp2s0}"

# --------------------------------------------- Ceph pool defaults (see 04)
CEPH_POOL_SIZE="${CEPH_POOL_SIZE:-3}"
CEPH_POOL_MIN_SIZE="${CEPH_POOL_MIN_SIZE:-2}"
POOL_VM_NAME="${POOL_VM_NAME:-vm-storage}"            # primary RBD pool for VM disks
# Capacity ceiling (04 "Capacity model"): the binding failure is ONE OSD —
# its sibling on the same host must absorb its data. Reference build
# (2x 3.84 TB per host): ceiling ~3.3 TB of VM data (~43% raw). Recompute
# both values for any other drive size/count, or a 4th node.
POOL_VM_TARGET_SIZE="${POOL_VM_TARGET_SIZE:-3T}"      # autoscaler HINT only (pveceph units are binary: 3T = 3 TiB ~ 3.3 TB)
POOL_VM_QUOTA_BYTES="${POOL_VM_QUOTA_BYTES:-3300000000000}"   # the GUARD: pool quota = the ceiling, 3.3 TB in bytes

# ------------------------------------------------- Proxmox HA defaults (see 05)
HA_RULE_NAME="${HA_RULE_NAME:-ha-prefer-any}"         # node-affinity rule name
# One format everywhere: space-separated numeric VMIDs, e.g. "100 101".
# The empty default protects NOTHING — 05 refuses to create a rule until
# it is set. (05 turns each id into vm:<id>; containers are out of scope.)
HA_PROTECTED_VMS="${HA_PROTECTED_VMS:-}"              # PLACEHOLDER — space-separated vmids, e.g. "100 101"

# ------------------------------------------------------------------ helpers
# Fail fast if any consumed variable is still a placeholder or empty.
# Usage: cerberus_require_vars NODE1_MGMT_IP BF3_P1 ...
cerberus_require_vars() {
    local missing=0 v val
    for v in "$@"; do
        val="${!v:-}"
        if [ -z "$val" ] || [ "$val" = "PLACEHOLDER" ]; then
            echo "FATAL: variable $v is unset or still PLACEHOLDER — resolve it in variables.sh" >&2
            missing=1
        fi
    done
    [ "$missing" -eq 0 ]
}

# Secret guard (H6 doctrine, parity with BCM's bcm_require_secret): refuse
# an unset, empty, PLACEHOLDER or <...> value for a credential-class name
# (PBS_PASSWORD, XCC_PASSWORD, ...). Prints NAMES only, never a value.
# Usage: cerberus_require_secret PBS_PASSWORD
cerberus_require_secret() {
    local name value rc=0
    for name in "$@"; do
        value="${!name-}"
        case "$value" in
            "" | PLACEHOLDER | *"<"*">"*)
                printf 'REFUSED: %s is not set to a real value in this shell (read -rs %s; export %s)\n' \
                    "$name" "$name" "$name" >&2
                rc=1 ;;
        esac
    done
    return "$rc"
}

# PBS auth-id and repository — the ONE construction (H8), used by 06.6,
# 06.7 and scripts/backup-drill.sh alike. Token form, on the backup VLAN:
#     pve-backup@pbs!pve-token@<PBS_VLAN_IP>:<PBS_DATASTORE>
# Defined here (a sourced file, where "!" is not history-expanded) so no
# pasted command line ever contains a "!".
cerberus_pbs_auth_id() {
    cerberus_require_vars PBS_BACKUP_USER PBS_AUTH_NAME || return 1
    case "$PBS_BACKUP_USER" in
        *@pbs) ;;
        *) echo "FATAL: PBS_BACKUP_USER=$PBS_BACKUP_USER — expected a PBS-realm user (name@pbs)" >&2; return 1 ;;
    esac
    printf '%s!%s\n' "$PBS_BACKUP_USER" "$PBS_AUTH_NAME"
}
cerberus_pbs_repository() {
    local auth
    auth="$(cerberus_pbs_auth_id)" || return 1
    cerberus_require_vars PBS_VLAN_IP PBS_DATASTORE || return 1
    printf '%s@%s:%s\n' "$auth" "$PBS_VLAN_IP" "$PBS_DATASTORE"
}
# Fingerprint format check: 32 colon-separated hex pairs (SHA256).
cerberus_check_fingerprint() {
    local fp="${1-}"
    if [[ "$fp" =~ ^([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$ ]]; then
        return 0
    fi
    echo "FATAL: not a SHA256 fingerprint (expected 32 colon-separated hex pairs): '${fp}'" >&2
    return 1
}

# Audit the loaded values against the PRISTINE TEMPLATE's shipped defaults.
# The template's defaults are plausible-looking addresses, not the string
# PLACEHOLDER, so a literal-string test passes a site file nobody edited.
# Reports:
#   - anything still PLACEHOLDER or empty;
#   - every audited value (networks, gateways, IPs, PBS host, DNS, NTP,
#     the mail target, the pool sizing, the XCC login name) still
#     identical to its template default;
#   - the host-resolved names (must be exported per node).
# Template path: $CERBERUS_TEMPLATE, else found relative to this file
# (works from the template itself or from <track>/site-vars.sh).
# Returns non-zero if anything was reported.
cerberus_audit_placeholders() {
    local here tmpl v val dflt found=0
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    tmpl="${CERBERUS_TEMPLATE:-}"
    if [ -z "$tmpl" ]; then
        for tmpl in "$here/runbook/00-overview/variables.sh" "$here/variables.sh" \
                    "$here/../runbook/00-overview/variables.sh"; do
            [ -f "$tmpl" ] && break
        done
    fi
    if [ ! -f "$tmpl" ]; then
        echo "FATAL: pristine template not found (set CERBERUS_TEMPLATE) — cannot audit against defaults" >&2
        return 2
    fi
    echo "--- variables still PLACEHOLDER or empty ---"
    while read -r v; do
        val="${!v:-}"
        case "$val" in
            ""|PLACEHOLDER) printf '%-22s %s\n' "$v" "${val:-(empty)}"; found=1 ;;
        esac
    done < <(compgen -v | grep -E '^(NODE|MGMT|CEPH|FABRIC|SWITCH|PBS|DNS|NTP|MAILTO|DOMAIN|CLUSTER|POOL|HA|XCC|BACKUP)' | sort)
    echo "--- audited values still identical to the template default ($tmpl) ---"
    # Template defaults: source the template in a clean environment so no
    # exported override masks a default.
    while IFS='=' read -r v dflt; do
        val="${!v:-}"
        if [ -n "$val" ] && [ "$val" = "$dflt" ]; then
            printf '%-22s %s\n' "$v" "$val"; found=1
        fi
    done < <(env -i bash --noprofile --norc -c '
        source "$1" >/dev/null 2>&1 || exit 2
        compgen -v | grep -E "(_NET|_GW|_IP|_IPS)\$|^(PBS_HOST_IP|DNS_SERVERS|NTP_SERVERS|MAILTO|POOL_VM_TARGET_SIZE|POOL_VM_QUOTA_BYTES|XCC_USER)\$" \
          | grep -vE "^(BASH|COMP|HOST|MACH|OS|PPID|UID|EUID)" | sort \
          | while read -r n; do printf "%s=%s\n" "$n" "${!n}"; done' _ "$tmpl")
    echo "--- host-resolved (must be exported per node; undefined in the template) ---"
    for v in BF3_P1 BF3_P2 MGMT_IF; do
        if [ -n "${!v:-}" ]; then
            printf '%-22s %s\n' "$v" "${!v}"
        else
            printf '%-22s %s\n' "$v" "(not set — see 02-network §1)"; found=1
        fi
    done
    [ "$found" -eq 0 ]
}
