#!/usr/bin/env bash
# =====================================================================
# Cerberus — scripts/preflight.sh
#
# READ-ONLY pre-flight check. Run LOCALLY on each node (node-1/2/3)
# AFTER the 01 install (PVE is on the node) and BEFORE 03 (Ceph init).
# That is the only window where every check below can pass: check [1]
# expects PVE to be installed already, check [8] expects Ceph not to be.
#
# Verifies:
#   1. PVE 9.x installed (pve-manager major version) — expected, since
#      this runs after 01
#   2. DNS resolves the peer node hostnames
#   3. NTP synchronized
#   4. >=2 interfaces at 200G link speed present (BlueField-3 ports)
#   5. MTU 9000 on the 200G interfaces (WARN only at this stage —
#      §02 sets final MTU on bond0/VLANs; net-verify.sh tests it for real)
#   6. >=2 BLANK NVMe devices (the OSD drives). by-id symlinks are
#      resolved to their device and de-duplicated; only a device with NO
#      partitions and NO holders (LVM/dm/md) is counted. A device that has
#      either is reported as present-but-NOT-counted, with the wipe step
#      named (runbook/03-ceph Step 5a) — a used drive is not an OSD
#      candidate until it is deliberately wiped, and the boot volume (if it
#      presents as NVMe) must never be. Test hooks (fake-device harness
#      only): CERBERUS_TEST_BYID_DIR, CERBERUS_TEST_SYSFS.
#   7. storcli: NO virtual drives on the 940-8i (JBOD/raw, never HW RAID)
#   8. Ceph NOT yet initialized (this runs before 03)
#
# Usage: bash scripts/preflight.sh [--vars <path>] [--dry-run]
#   --dry-run: print what would be checked, run nothing.
#   --vars <path>: the site variables file (same convention as render.py).
#              Default: <track>/site-vars.sh. If neither exists, the script
#              exits 2 — it never sources the template variables.
# Exit code: 0 if all pass, non-zero with a clear message on failure.
# =====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
VARS_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --vars) [ $# -ge 2 ] || { echo "--vars needs a path" >&2; exit 2; }; VARS_FILE="$2"; shift 2 ;;
    --vars=*) VARS_FILE="${1#--vars=}"; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

# --- site variables (fail closed: never the template) ---------------------
# Convention shared with render.py: --vars <path> names the site file;
# without it, <track>/site-vars.sh is used. If neither exists the script
# stops — it never falls back to runbook/00-overview/variables.sh, whose
# defaults would make every result meaningless.
TRACK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_VARS="$TRACK_DIR/runbook/00-overview/variables.sh"
if [ -z "$VARS_FILE" ]; then
  VARS_FILE="$TRACK_DIR/site-vars.sh"
  if [ ! -f "$VARS_FILE" ]; then
    echo "FATAL: no site variables file. Pass --vars <path>, or create the default:" >&2
    echo "         cp $TEMPLATE_VARS $TRACK_DIR/site-vars.sh   # then fill in the site's values" >&2
    echo "       Refusing to run against the template defaults." >&2
    exit 2
  fi
fi
if [ ! -f "$VARS_FILE" ]; then
  echo "FATAL: --vars file not found: $VARS_FILE" >&2
  exit 2
fi
if [ "$(realpath "$VARS_FILE")" = "$(realpath "$TEMPLATE_VARS")" ]; then
  echo "FATAL: $VARS_FILE is the pristine template, not a site file." >&2
  echo "       Copy it to $TRACK_DIR/site-vars.sh, fill it in, and pass that." >&2
  exit 2
fi
echo "site variables: $VARS_FILE"
# shellcheck source=/dev/null
source "$VARS_FILE"

NODES=("$NODE1_HOSTNAME" "$NODE2_HOSTNAME" "$NODE3_HOSTNAME")

echo "=== Cerberus preflight (read-only) on $(hostname) ==="
echo "This script makes NO changes. It will check:"
echo "  [1] PVE 9.x is installed (expected — preflight runs after 01, before 03)"
echo "  [2] DNS resolves peer nodes: ${NODES[*]}"
echo "  [3] System clock is NTP-synchronized"
echo "  [4] At least 2 interfaces at 200G link speed (BlueField-3 ports)"
echo "  [5] MTU ${CEPH_PUBLIC_MTU} on the 200G interfaces (WARN if not yet set)"
echo "  [6] At least 2 BLANK NVMe devices (the OSD drives; partitioned/held devices are listed, not counted)"
echo "  [7] storcli: no virtual drives (JBOD only)"
echo "  [8] Ceph is NOT initialized on this host"
echo ""

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN: checks listed above, nothing executed."
  exit 0
fi

FAIL=0
fail() { echo "FAIL: $1" >&2; FAIL=1; }
pass() { echo "PASS: $1"; }
warn() { echo "WARN: $1"; }

# --- [1] PVE version ---------------------------------------------------
if command -v pveversion >/dev/null 2>&1; then
  PVE_MAJOR="$(pveversion | grep -oE 'pve-manager/[0-9]+' | head -1 | cut -d/ -f2 || true)"
  if [ "$PVE_MAJOR" = "9" ]; then
    pass "[1] PVE major version 9 ($(pveversion | head -1))"
  else
    fail "[1] pve-manager major version '$PVE_MAJOR' is not 9 — reinstall/upgrade before proceeding."
  fi
else
  fail "[1] 'pveversion' not found — preflight runs AFTER the 01 install (and before 03); install PVE per runbook/01-cluster-install first."
fi

# --- [2] DNS -----------------------------------------------------------
for n in "${NODES[@]}"; do
  if getent hosts "$n" >/dev/null 2>&1; then
    pass "[2] DNS resolves $n -> $(getent hosts "$n" | awk '{print $1}')"
  else
    fail "[2] DNS does not resolve '$n' — fix DNS/hosts before cluster join (hostnames are final after pvecm create)."
  fi
done

# --- [3] NTP -----------------------------------------------------------
if timedatectl show 2>/dev/null | grep -q 'NTPSynchronized=yes'; then
  pass "[3] Clock is NTP-synchronized"
else
  fail "[3] Clock is NOT NTP-synchronized (NTPSynchronized != yes) — fix time sync before corosync."
fi

# --- [4] 200G interfaces (BlueField-3 ports) ------------------------------
FAST_IFACES=()
for iface in /sys/class/net/*; do
  name="$(basename "$iface")"
  [ "$name" = "lo" ] && continue
  speed_file="$iface/speed"
  if [ -r "$speed_file" ]; then
    speed="$(cat "$speed_file" 2>/dev/null || echo 0)"
    if [ "$speed" -ge 200000 ]; then
      FAST_IFACES+=("$name")
    fi
  fi
done

if [ "${#FAST_IFACES[@]}" -ge 2 ]; then
  pass "[4] Found ${#FAST_IFACES[@]} interface(s) at 200G: ${FAST_IFACES[*]}"
else
  fail "[4] Expected >=2 interfaces at 200G, found: ${FAST_IFACES[*]:-<none>} — check BlueField-3 NIC mode (mlxconfig LINK_TYPE_P1/P2=2, NIC mode) and DAC seating."
fi

# --- [5] MTU on 200G interfaces (advisory at preflight stage) -------------
for iface in "${FAST_IFACES[@]}"; do
  mtu="$(cat "/sys/class/net/$iface/mtu")"
  if [ "$mtu" -eq "$CEPH_PUBLIC_MTU" ]; then
    pass "[5] $iface MTU=$mtu"
  else
    warn "[5] $iface MTU=$mtu (design wants $CEPH_PUBLIC_MTU) — §02 sets final MTU on bond0/VLANs; scripts/net-verify.sh will test it end-to-end."
  fi
done

# --- [6] NVMe OSD drives: count BLANK devices only ---------------------
# Each drive has SEVERAL by-id entries (eui.*, model_serial, _1 namespace
# aliases, -partN), so counting entries passes a single drive. Resolve
# every nvme-* symlink to its device, drop partitions, de-duplicate. Then
# a device counts only if it is BLANK: no partitions and no holders in
# sysfs (/sys/block/<dev>/<dev>pN, /sys/block/<dev>/holders/*). A
# partitioned or held device is present but NOT counted (M25 / ruled
# 2026-11-16): it is either in use (the boot volume, if it presents as
# NVMe) or a previously used drive that must be wiped deliberately first.
BYID_DIR="${CERBERUS_TEST_BYID_DIR:-/dev/disk/by-id}"
SYSFS="${CERBERUS_TEST_SYSFS:-/sys}"
NVME_DEVS=()
while IFS= read -r dev; do
  [ -n "$dev" ] && NVME_DEVS+=("$dev")
done < <(
  for l in "$BYID_DIR"/nvme-*; do
    [ -e "$l" ] || continue
    d="$(readlink -f "$l")"
    b="${d##*/}"
    case "$b" in
      nvme*n*p*) continue ;;   # partition, not a device
      nvme*n*) echo "$b" ;;
    esac
  done | sort -u
)
BLANK_DEVS=()
USED_DEVS=()
for b in "${NVME_DEVS[@]}"; do
  parts=0; holders=0
  for p in "$SYSFS/block/$b/$b"p*; do [ -e "$p" ] && parts=$((parts + 1)); done
  for h in "$SYSFS/block/$b/holders/"*; do [ -e "$h" ] && holders=$((holders + 1)); done
  if [ "$parts" -eq 0 ] && [ "$holders" -eq 0 ]; then
    BLANK_DEVS+=("$b")
  else
    USED_DEVS+=("$b (partitions: $parts, holders: $holders)")
  fi
done
for u in "${USED_DEVS[@]}"; do
  warn "[6] present but NOT counted: /dev/$u — not blank. If this is a Ceph drive from an earlier build, wipe it with runbook/03-ceph Step 5a (DESTRUCTIVE; never the boot volume)."
done
if [ "${#BLANK_DEVS[@]}" -ge 2 ]; then
  pass "[6] ${#BLANK_DEVS[@]} blank NVMe device(s) (BOM: 2 OSD drives/node):"
  for b in "${BLANK_DEVS[@]}"; do
    echo "        /dev/$b  $(lsblk -dno MODEL,SIZE "/dev/$b" 2>/dev/null | tr -s ' ' || true)"
  done
else
  fail "[6] Expected >=2 BLANK NVMe devices, found ${#BLANK_DEVS[@]} blank of ${#NVME_DEVS[@]} NVMe device(s) — partitioned/held devices are listed above (wipe step: runbook/03-ceph Step 5a); if drives are missing, check backplane cabling (direct-to-CPU x4, NOT behind the 940-8i)."
fi

# --- [7] storcli: no virtual drives --------------------------------------
if command -v storcli >/dev/null 2>&1; then
  VDRIVES="$(storcli /c0/vall show 2>/dev/null | grep -cE '^[[:space:]]*[0-9]+/' || true)"
  if [ "$VDRIVES" -eq 0 ]; then
    pass "[7] storcli /c0/vall: no virtual drives (JBOD/raw OK)"
  else
    fail "[7] storcli shows $VDRIVES virtual drive(s) on /c0 — OSD drives must be JBOD, NEVER hardware RAID. Delete virtual drives before Ceph."
  fi
  echo "      (Verify the PM1743s do NOT appear under 'storcli /c0/pall show' — they must be direct-to-CPU, not behind the 940-8i.)"
else
  fail "[7] 'storcli' not installed — cannot verify JBOD state of the 940-8i. Install StorCLI before proceeding (see docs/references.md)."
fi

# --- [8] Ceph not initialized --------------------------------------------
if [ -f /etc/ceph/ceph.conf ]; then
  fail "[8] /etc/ceph/ceph.conf exists — Ceph appears initialized. Preflight runs before 03."
elif ceph -s >/dev/null 2>&1; then
  fail "[8] 'ceph -s' succeeded — Ceph appears initialized. Preflight runs before 03."
else
  pass "[8] Ceph not initialized (expected before 03)"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "PREFLIGHT: ALL CHECKS PASSED on $(hostname)."
  exit 0
else
  echo "PREFLIGHT: FAILURES DETECTED on $(hostname) — resolve before proceeding." >&2
  exit 1
fi
