#!/bin/bash
# =====================================================================
# 00-variables.sh — the pristine TEMPLATE of site values (B300 runbook)
# ---------------------------------------------------------------------
# Never edit this tracked file in place. Copy it to the site file beside
# it and fill THAT in (bcm/site-vars.sh is gitignored, so site values
# never reach a clone's push remote):
#     cp 00-variables.sh site-vars.sh && nvim site-vars.sh
#     source site-vars.sh
# Every command in sections 01 → 16 references these variables, and every
# runbook.py pass reads site-vars.sh (or --vars FILE). The passes refuse
# this template; its shipped defaults describe the lab build.
#
# "Companyify" step: replacing these values (and DOMAIN) with the real
# site's values is the entire site-customization process.
# =====================================================================

# --- Identity ---
export DOMAIN="example.internal"          # DNS domain for the cluster
export CLUSTER_NAME="bcm-cluster"         # BCM cluster name
export CLIENT_ORG="Wayland Megacorp"      # Organization (docs/license)

# --- Networks (CIDR + gateway). Segmented layout is the default;
#     a flat single-/24 layout also works (see 01). ---
export NET_PROVISION="10.10.15.0/24"      # BCM internalnet (provisioning)
export GW_PROVISION="10.10.15.1"
export NET_STORAGE="10.10.12.0/24"        # Storagenet (Pure NFS)
export NET_USER="10.10.13.0/24"           # Usernet (services, user access)
export GW_USER="10.10.13.1"
export NET_OOB="10.10.16.0/24"            # OOB / hardware management
export GW_OOB="10.10.16.1"
# The site DHCP pool on the provisioning network, "FIRST-LAST" (01 "DHCP").
# Every static address in 01 must sit outside it. Empty = not decided yet:
# `scripts/runbook.py vars` FAILS a site file while it is empty (01's
# sign-off). No default on purpose: at fleet scale the statics run well
# past any guessed range.
export DHCP_POOL=""
# BCM HA only (13): the external virtual IP that reaches the ACTIVE head
# node. Empty = the site does not run BCM HA.
export BCM_HA_VIP=""
export NET_DNS1="10.10.11.53"
export NET_DNS2="10.10.11.54"
export DNS_IP="$NET_DNS1"                  # DNS server under test (14); override per site
# Admin source networks (14.1): the only networks allowed to SSH to the
# jumpbox. Shipped defaults are RFC 5737 documentation ranges — they match
# nothing real, so an unedited template can never open SSH to the world.
# 14.1 step 5 enables the firewall only when your session's source
# address (SSH_CLIENT, or under sudo/su the tty's login record, `who -m`)
# is inside one of them, and refuses if that source is missing, not an
# address, or outside. The one exception is a console session: no
# SSH_CLIENT and a real tty device that is not a pts (tty1, ttyS0, hvc0;
# "not a tty" never counts). A console under sudo also needs the
# explicit acknowledgement CONSOLE_CONFIRMED=yes, or it refuses.
# Set B to "" if the site has only one admin network.
export NET_ADMIN_SRC_A="192.0.2.0/24"      # e.g. the admins' VPN / bastion range
export NET_ADMIN_SRC_B="198.51.100.0/24"   # e.g. the NOC / on-site admin range

# --- Kubernetes networks (from cm-kubernetes-setup) ---
# kubeadm-conventional ranges: valid network addresses, outside the site
# /16 above, clear of each other. `scripts/runbook.py vars` and
# scripts/check.sh fail if either range overlaps a site network, the DNS
# hosts, the admin ranges, or the other range — but they can
# only see THIS plan: verify both against the customer's existing
# networks at intake (00a) before design close.
export K8S_SERVICE_NET="10.96.0.0"
export K8S_SERVICE_MASK="16"
export K8S_POD_NET="10.244.0.0"
export K8S_POD_MASK="16"

# --- Node roles (short names; FQDN = <name>.${DOMAIN}) ---
export BCM_HEADNODE="bcm-headnode"         # head node short name
export BCM_HEADNODE_IP="10.10.15.10"       # head node IP
export K8S_CTRL="k8s-ctrl-1 k8s-ctrl-2 k8s-ctrl-3"   # 10.10.15.50-52
export K8S_CTRL_IPS="10.10.15.50 10.10.15.51 10.10.15.52"
# --- GPU workers ---
# Hardware families: DGX A/H/B series are treated as interchangeable here
# (A100, H100/H200, B200/B300) — one category+image per node TYPE.
# HGX 8-GPU OEM nodes (Dell, HPE, Lenovo) and Cisco UCS GPU nodes follow
# the exact same pattern: add a category+image per type (see 03).
# Vera Rubin NVL72 rack-scale systems (NVIDIA's current name; the rack
# was first announced as "NVL144") — NVIDIA: "Production shipments of
# Vera Rubin are set to begin starting this fall" (2026-05-31 release).
# Same per-type pattern for provisioning, but the rack fabric and liquid
# cooling are their own design.
# Mixed GPU node types in one cluster: one category+image per type.
export GPU_WORKERS="gpu-worker-1 gpu-worker-2 gpu-worker-3"
export GPU_WORKER_IPS="10.10.15.53 10.10.15.54 10.10.15.55"
export GPU_NODE_TYPE="dgx-b300"            # e.g. dgx-b300, dgx-h200, hgx-dell, ucs
# Naming (03.1/03.2): images are per FAMILY — every DGX model shares
# runai-dgx-image (cloned from dgx-image); HGX/UCS families get their own
# (runai-hgx-dell-image, runai-ucs-image). Categories are per HOST TYPE,
# because each carries its type's by-path disk layout (03.4): one DGX
# type keeps runai-dgx-cat; a second DGX model adds runai-<type>-cat
# (e.g. runai-dgx-h200-cat) on the same runai-dgx-image.
export IMG_GPU="runai-dgx-image"           # per family: runai-<family>-image
export CAT_GPU="runai-dgx-cat"             # per host type once there is more than one

# --- Service IPs (Usernet; MetalLB / gateway addresses) ---
export IP_KGATEWAY="10.10.13.200"          # Kubernetes gateway (Kgateway)
export IP_RUNAI="10.10.13.201"             # Run:ai control-plane ingress
export IP_INFERENCE="10.10.13.202"         # Run:ai inference (Kourier)
export FQDN_KGATEWAY="kgateway.${DOMAIN}"
export FQDN_RUNAI="runai.${DOMAIN}"
export FQDN_INFERENCE="runai-inference.${DOMAIN}"

# --- BCM software images and categories ---
export IMG_CP="runai-cp-image"
export IMG_WRK="runai-wrk-image"
export CAT_CP="runai-cp-cat"
export CAT_WRK="runai-wrk-cat"
# (GPU image/category vars are defined with the GPU workers above.)
export KERNEL_VER="6.8.0-142-generic"      # validate with uname -r on image

# --- Storage ---
export NFS_SERVER="10.10.12.250"           # Pure NFS server (Storagenet)
export NFS_SHARE="/data"
export NFS_MOUNT="/mnt/data"

# --- Credentials: placeholders HERE, always ---
# Credential class (H6): every name containing PASSWORD, API_KEY, TOKEN,
# SECRET, PRODUCT_KEY or LICENSE_KEY. Policy: credentials never appear in
# a deliverable, placeholder or real.
#   - Leave the placeholders below in the site file too. Supply the real values
#     in your shell at run time:  read -rs BMC_PASSWORD; export BMC_PASSWORD
#     (the `${NAME:-<...>}` form keeps a value you exported before
#     sourcing this file).
#   - `runbook.py render` never substitutes these names — the rendered
#     guide keeps `$BMC_PASSWORD`. A real value in the variables FILE
#     (even as a ${NAME:-value} default) makes render REFUSE (exit 3,
#     nothing written); one held only in your shell is warned about by
#     name and the render continues.
#   - Commands that consume one are written
#     `bcm_require_secret NAME && <command>`, so a placeholder can never
#     be sent to a real tool (see the helper at the end of this file).
export BCM_PRODUCT_KEY="${BCM_PRODUCT_KEY:-<BCM-PRODUCT-KEY>}" # from ui.licensing.nvidia.com
export NGC_API_KEY="${NGC_API_KEY:-<NGC-API-KEY>}"             # from org.ngc.nvidia.com/account/api-key
export BMC_USER="dgxadmin"
export BMC_PASSWORD="${BMC_PASSWORD:-<BMC-PASSWORD>}"
export RUNAI_ADMIN_USER="admin@${DOMAIN}"
export RUNAI_ADMIN_PASSWORD="${RUNAI_ADMIN_PASSWORD:-<RUNAI-ADMIN-PASSWORD>}"
export PG_PASSWORD="${PG_PASSWORD:-<POSTGRES-PASSWORD>}"

# --- Versions (known-working baseline; the reference list is the 08.3 field note) ---
export BCM_VERSION="11"
export K8S_VERSION="1.34"
# Site pins (M8 R5 — moved here from the 14.1 and 02 blocks, which used to
# set them block-locally). Both ship EMPTY: the blocks never had a value,
# only a stop-if-unset guard, and that guard still stops them until the
# site file sets one. The helm release for 14.1's tooling (match the plan
# in 10; the bare version, no leading "v"):
export HELM_VERSION=""
# The reviewed commit of the Run:ai professional-services utilities (02):
# the full hash you read with `git log` before trusting it; record it in 10.
export RUNAI_UTILS_COMMIT=""
export RUNAI_VERSION="2.26.x"
export GPU_OPERATOR_VERSION="v26.3.1"
export NET_OPERATOR_VERSION="26.1.1"
export NFS_CSI_VERSION="4.13.4"

# --- Helper ---------------------------------------------------------------
# bcm_require_secret NAME... — succeeds only if every named credential is
# set in THIS shell to a real value (not empty, not a <PLACEHOLDER>).
# Prints names only, never values. Use: bcm_require_secret BMC_PASSWORD && cmsh ...
bcm_require_secret() {
    local name value rc=0
    for name in "$@"; do
        value="${!name-}"
        case "$value" in
            "" | *"<"*">"*)
                printf 'REFUSED: %s is not set to a real value in this shell (read -rs %s; export %s)\n' \
                    "$name" "$name" "$name" >&2
                rc=1 ;;
        esac
    done
    return "$rc"
}
