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
#   2. Later sections and the node scripts source the SITE file:
#         set -u
#         source ../../site-vars.sh          # from inside runbook/NN-*/
#         scripts/net-verify.sh [--vars <path>]   # default: <track>/site-vars.sh
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
CEPH_RELEASE="${CEPH_RELEASE:-tentacle}"   # PVE 9.2 default. Do NOT use Squid (EOL ~Sept/Oct 2026 — re-check docs.ceph.com at deploy time).
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
# Carries: Proxmox management, corosync link0, XCC/BMC, PBS traffic.
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

# ------------------------------------------- Ceph public network (client)
# Physical path: bond0 (active-backup over 2x 200G BlueField-3) tagged VLAN 100
# -> SN5610-A / SN5610-B breakout legs. MTU 9000 end to end.
# Carries: Ceph client traffic (MON/MGR/OSD client ports) + VM/guest traffic.
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

# ------------------------------------------------------- fabric switches
# Two NVIDIA SN5610 800GbE (Cumulus 5.x) carry the Proxmox storage fabric.
# A third SN5610 exists as SPARE (AI side) — out of scope for this runbook.
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
# Dedicated physical host (NOT one of the cluster nodes). Reachable on MGMT_NET.
PBS_HOST_IP="${PBS_HOST_IP:-10.10.10.100}"    # PLACEHOLDER
PBS_DATASTORE="${PBS_DATASTORE:-ceph-backups}"

# --------------------------------------------- Ceph pool defaults (see 04)
CEPH_POOL_SIZE="${CEPH_POOL_SIZE:-3}"
CEPH_POOL_MIN_SIZE="${CEPH_POOL_MIN_SIZE:-2}"
POOL_VM_NAME="${POOL_VM_NAME:-vm-storage}"            # primary RBD pool for VM disks
POOL_VM_TARGET_SIZE="${POOL_VM_TARGET_SIZE:-5T}"      # PLACEHOLDER — pg_autoscale target (planned usable)

# ------------------------------------------------- Proxmox HA defaults (see 05)
HA_RULE_NAME="${HA_RULE_NAME:-ha-prefer-any}"         # node-affinity rule name
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

# Audit the loaded values against the PRISTINE TEMPLATE's shipped defaults.
# The template's defaults are plausible-looking addresses, not the string
# PLACEHOLDER, so a literal-string test passes a site file nobody edited.
# Reports:
#   - anything still PLACEHOLDER or empty;
#   - every audited value (networks, gateways, IPs, PBS host, DNS, NTP)
#     still identical to its template default;
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
    done < <(compgen -v | grep -E '^(NODE|MGMT|CEPH|FABRIC|SWITCH|PBS|DNS|NTP|MAILTO|DOMAIN|CLUSTER|POOL|HA)' | sort)
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
        compgen -v | grep -E "(_NET|_GW|_IP|_IPS)\$|^(PBS_HOST_IP|DNS_SERVERS|NTP_SERVERS)\$" \
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
