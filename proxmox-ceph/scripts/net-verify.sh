#!/usr/bin/env bash
# =====================================================================
# Cerberus — scripts/net-verify.sh
#
# Non-destructive network verification for the 3-node fabric.
# Run from ANY one of the PVE nodes (it reaches the others over SSH).
# Checks [1] and [4] only ping from this node. Checks [2] and [3] are
# ACTIVE probes: they start a temporary listener / iperf3 server on
# the peer and write a uniquely-named temp file there, so they run
# only with --allow-remote-probes. Everything a probe starts is
# cleaned up by its own PID or unique name -- never by a broad
# process match, and nothing else on the peer is touched.
#
# Verifies:
#   1. End-to-end MTU on the Ceph public + Ceph cluster networks
#      (ping -M do with a 8972-byte payload => 9000-byte frames, DF set)
#   2. corosync UDP port range 5405-5412 reachable between all nodes, on
#      BOTH corosync networks: link0 (mgmt IPs) and link1 (Ceph public
#      IPs — link1 is mandatory). Probe datagrams; needs nc/netcat.
#   4. PBS backup VLAN: MTU ${PBS_MTU} end to end from this node to the
#      PBS host's VLAN address (PBS_VLAN_IP) — the path every backup and
#      restore takes (H3).
#   3. Optional: iperf3 bandwidth sanity on the 200G fabric (auto-skipped
#      if iperf3 is missing on either end; disable with --no-iperf3)
#
# Usage: bash scripts/net-verify.sh [--vars <path>] [--dry-run] [--no-iperf3] [--allow-remote-probes]
#   --dry-run:    print what would be tested, run nothing.
#   --no-iperf3: skip the optional bandwidth test.
#   --allow-remote-probes: run the active probes on the peers ([2]
#              starts a temporary UDP listener + temp file, [3] a
#              temporary iperf3 server). Without it, [2] reports
#              INCOMPLETE -- an unrun required probe is not a pass --
#              and [3] is skipped.
#   --vars <path>: the site variables file (same convention as render.py).
#              Default: <track>/site-vars.sh. If neither exists, the script
#              exits 2 — it never sources the template variables.
# Exit code: 0 if all pass; non-zero with a clear message on failure.
# =====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
WITH_IPERF3=1
WITH_REMOTE=0
VARS_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-iperf3) WITH_IPERF3=0; shift ;;
    --allow-remote-probes) WITH_REMOTE=1; shift ;;
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
PUBLIC_IPS=("$NODE1_PUBLIC_IP" "$NODE2_PUBLIC_IP" "$NODE3_PUBLIC_IP")
CLUSTER_IPS=("$NODE1_CLUSTER_IP" "$NODE2_CLUSTER_IP" "$NODE3_CLUSTER_IP")
MGMT_IPS=("$NODE1_MGMT_IP" "$NODE2_MGMT_IP" "$NODE3_MGMT_IP")
PBS_PAYLOAD=$(( PBS_MTU - 28 ))

# Payload size for MTU test: 9000 - 20 (IP) - 8 (ICMP) = 8972
MTU_PAYLOAD=$(( CEPH_PUBLIC_MTU - 28 ))
PROBE_PORT=5406   # inside 5405-5412, distinct from the live corosync port

echo "=== Cerberus network verification (non-destructive) ==="
echo "Local host: $(hostname)"
echo "This script makes NO configuration changes. It will test:"
echo "  [1] MTU ${CEPH_PUBLIC_MTU} end-to-end (DF ping, ${MTU_PAYLOAD}-byte payload)"
echo "      public  : ${PUBLIC_IPS[*]}"
echo "      cluster : ${CLUSTER_IPS[*]}"
echo "  [2] corosync UDP reachability (5405-5412) on both links"
echo "      link0 (mgmt IPs): ${MGMT_IPS[*]}"
echo "      link1 (public IPs): ${PUBLIC_IPS[*]}"
if [ "$WITH_IPERF3" -eq 1 ]; then
  echo "  [3] iperf3 bandwidth sanity on the 200G fabric (optional; skipped if missing)"
else
  echo "  [3] iperf3 bandwidth test: DISABLED by --no-iperf3"
fi
echo "  [4] PBS backup VLAN: MTU ${PBS_MTU} (DF ping, ${PBS_PAYLOAD}-byte payload) to ${PBS_VLAN_IP}"
if [ "$WITH_REMOTE" -eq 1 ]; then
  echo "  remote probes: ENABLED (--allow-remote-probes) -- [2]/[3] start temporary, self-cleaning probes on the peers"
else
  echo "  remote probes: NOT enabled -- [2] will report INCOMPLETE and [3] will skip (use --allow-remote-probes)"
fi
echo ""

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN: tests listed above, nothing executed."
  exit 0
fi

FAIL=0
INCOMPLETE=0
fail() { echo "FAIL: $1" >&2; FAIL=1; }
incomplete() { echo "INCOMPLETE: $1" >&2; INCOMPLETE=1; }
pass() { echo "PASS: $1"; }
warn() { echo "WARN: $1"; }

# --- [1] MTU end-to-end ---------------------------------------------------
echo "--- [1] MTU ${CEPH_PUBLIC_MTU} ping tests (do-not-fragment) ---"
MTU_TARGETS=("${PUBLIC_IPS[@]}" "${CLUSTER_IPS[@]}")
LOCAL_IPS="$(hostname -I)"
for target in "${MTU_TARGETS[@]}"; do
  if echo "$LOCAL_IPS" | grep -qw "$target"; then
    echo "  (skip $target — local address)"
    continue
  fi
  if ping -M do -s "$MTU_PAYLOAD" -c 4 -W 2 "$target" >/dev/null 2>&1; then
    pass "[1] $target accepts ${CEPH_PUBLIC_MTU}-byte frames (DF)"
  else
    fail "[1] $target REJECTED ${CEPH_PUBLIC_MTU}-byte frames — MTU mismatch on the path. Check switch MTU (${FABRIC_MTU}) and node interface/VLAN MTU (§02)."
  fi
done

# --- [2] corosync UDP reachability ----------------------------------------
echo ""
echo "--- [2] corosync UDP 5405-5412 reachability (link0 mgmt + link1 public) ---"
if ! command -v nc >/dev/null 2>&1; then
  fail "[2] 'nc' (netcat) not installed — cannot probe UDP. Install netcat and re-run, or verify corosync links after cluster join."
elif [ "$WITH_REMOTE" -eq 0 ]; then
  incomplete "[2] corosync UDP probes NOT RUN -- they start a temporary listener and temp file on each peer. Re-run with --allow-remote-probes; an unrun required probe is not a pass."
else
  PROBE_FILE="/tmp/cerberus-udp-probe.$$"
  for target in "${MGMT_IPS[@]}" "${PUBLIC_IPS[@]}"; do
    if echo "$LOCAL_IPS" | grep -qw "$target"; then
      echo "  (skip $target — local address)"
      continue
    fi
    # Listener on the target (background via ssh), probe from here, then check.
    ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$target" \
      "timeout 12 nc -u -l -p $PROBE_PORT > $PROBE_FILE 2>/dev/null & echo started" >/dev/null 2>&1 || true
    sleep 1
    echo "cerberus-udp-probe" | timeout 5 nc -u -w 2 "$target" "$PROBE_PORT" >/dev/null 2>&1 || true
    sleep 2
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$target" \
        "grep -q cerberus-udp-probe $PROBE_FILE && rm -f $PROBE_FILE" 2>/dev/null; then
      pass "[2] UDP datagram reached $target (port $PROBE_PORT in range)"
    else
      ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$target" "rm -f $PROBE_FILE" 2>/dev/null || true
      fail "[2] UDP datagram did NOT reach $target on port $PROBE_PORT — corosync knet traffic may be filtered. Check firewalls/ACLs on the path."
    fi
  done
  echo "  (Ensure UDP 5405-5412 is ACCEPTED in the node firewall for all peers.)"
fi

# --- [3] iperf3 bandwidth sanity (optional) --------------------------------
echo ""
echo "--- [3] iperf3 bandwidth sanity (optional) ---"
if [ "$WITH_IPERF3" -eq 0 ]; then
  echo "  Skipped (--no-iperf3)."
elif [ "$WITH_REMOTE" -eq 0 ]; then
  echo "  Skipped (starting an iperf3 server on the peer needs --allow-remote-probes)."
else
  if ! command -v iperf3 >/dev/null 2>&1; then
    warn "[3] iperf3 not installed locally — skipping bandwidth test (install iperf3 to enable)."
  else
    IPERF_PORT=15201
    IPERF_PIDFILE="/tmp/cerberus-iperf3.$$.pid"
    for target in "${PUBLIC_IPS[@]}"; do
      if echo "$LOCAL_IPS" | grep -qw "$target"; then
        echo "  (skip $target — local address)"
        continue
      fi
      if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$target" "command -v iperf3" >/dev/null 2>&1; then
        warn "[3] iperf3 missing on $target — skipping that leg."
        continue
      fi
      ssh -o BatchMode=yes "root@$target" "if [ -f $IPERF_PIDFILE ]; then kill \"\$(cat $IPERF_PIDFILE)\" 2>/dev/null; rm -f $IPERF_PIDFILE; fi; iperf3 -s -D -p $IPERF_PORT --pidfile $IPERF_PIDFILE >/dev/null 2>&1" || true
      sleep 1
      RESULT="$(timeout 20 iperf3 -c "$target" -p "$IPERF_PORT" -t 10 -f g 2>/dev/null | grep -E 'receiver' | tail -1 || true)"
      ssh -o BatchMode=yes "root@$target" "if [ -f $IPERF_PIDFILE ]; then kill \"\$(cat $IPERF_PIDFILE)\" 2>/dev/null; rm -f $IPERF_PIDFILE; fi" || true
      GBPS="$(echo "$RESULT" | grep -oE '[0-9]+\.[0-9]+ Gbits/sec' | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)"
      if [ -n "${GBPS:-}" ]; then
        # Sanity floor: 50 Gbits/sec (a quarter of a 200G leg — generous;
        # iperf3 single-stream rarely saturates 200G). Below = investigate.
        if awk "BEGIN{exit !($GBPS >= 50)}"; then
          pass "[3] -> $target: ${GBPS} Gbits/sec (>= 50 floor OK)"
        else
          warn "[3] -> $target: ${GBPS} Gbits/sec is LOW for a 200G leg — check link negotiation, DAC seating, CPU affinity."
        fi
      else
        warn "[3] iperf3 to $target produced no result — check iperf3 on both ends."
      fi
    done
  fi
fi

# --- [4] PBS backup VLAN -------------------------------------------------
echo ""
echo "--- [4] PBS backup VLAN: MTU ${PBS_MTU} to ${PBS_VLAN_IP} ---"
if ping -M do -s "$PBS_PAYLOAD" -c 4 -W 2 "$PBS_VLAN_IP" >/dev/null 2>&1; then
  pass "[4] PBS host $PBS_VLAN_IP accepts ${PBS_MTU}-byte frames (DF) on VLAN ${PBS_VLAN}"
else
  fail "[4] PBS host $PBS_VLAN_IP unreachable or REJECTED ${PBS_MTU}-byte frames — check bond0.${PBS_VLAN} on this node (§02), the PBS host's NIC MTU, and the SN5610 PBS ports (00a §6/§7)."
fi

echo ""
if [ "$FAIL" -ne 0 ]; then
  echo "NET-VERIFY: FAILURES DETECTED — resolve before cluster join/Ceph init." >&2
  exit 1
elif [ "$INCOMPLETE" -ne 0 ]; then
  echo "NET-VERIFY: INCOMPLETE — required probes were not run; this is not a pass." >&2
  exit 1
else
  echo "NET-VERIFY: ALL CHECKS PASSED."
  exit 0
fi
