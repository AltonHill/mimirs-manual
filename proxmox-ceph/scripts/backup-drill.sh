#!/usr/bin/env bash
# =====================================================================
# Cerberus — scripts/backup-drill.sh
#
# GUIDED PBS restore drill. Implements runbook §07 drill (d) (§7.6).
#
# Flow (with a confirmation prompt at EVERY consequential step):
#   1. Resolve PBS connection details. The repository string is built by
#      cerberus_pbs_repository (variables.sh) — the SAME construction
#      runbook §06.6/§06.7 use, token form on the backup VLAN:
#          pve-backup@pbs!pve-token@<PBS_VLAN_IP>:<PBS_DATASTORE>
#      The token secret is read without echo into PBS_PASSWORD (the
#      environment variable proxmox-backup-client reads), never printed,
#      never on a command line.
#   2. List snapshots for a chosen VMID.
#   3. Restore ONE chosen disk archive to a SCRATCH path — never onto
#      live storage — and verify it with qemu-img info (file-level proof).
#   4. Optional boot test: qmrestore from PBS THROUGH THE REGISTERED PVE
#      STORAGE — the PBS volume ID $PBS_STORAGE_ID:backup/vm/<id>/<time>
#      (there is no vzdump archive in this design) — to a NEW vmid,
#      ISOLATED: --unique 1 (new MACs) and every NIC link_down=1 before
#      the first start, so the copy can never collide with the running
#      original on a production bridge. stderr is shown.
#   5. Print a summary report (timing = RTO evidence for the §07 sign-off
#      table, §7.8 item 10).
#
# VERIFY AGAINST A LIVE PBS BEFORE SIGN-OFF: the auth string, ACLs and
# the qmrestore volume-ID path cannot be proven from the repository. Run
# this drill once end to end on the real PBS before trusting its report.
#
# Destructive surface: restoring to a scratch path is safe; qmrestore
# creates a NEW VM only after explicit confirmation. The script will
# NEVER overwrite an existing VM or restore path.
#
# Usage: bash scripts/backup-drill.sh [--vars FILE] [--dry-run]
#   --dry-run: print the plan and the exact commands that WOULD run,
#              execute nothing.
#   --vars FILE: the site variables file (same convention as render.py).
#              Default: <track>/site-vars.sh. If neither exists, the script
#              exits 2 — it never sources the template variables.
# Env (or prompted): PBS_FINGERPRINT (or set in the site file),
#   PBS_PASSWORD (the token secret — environment or prompt only).
#   PBS_REPOSITORY, if already set, must EQUAL the constructed string, or
#   the script refuses (one construction, never two).
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
# Convention shared with render.py: --vars FILE names the site file;
# without it, <track>/site-vars.sh is used. If neither exists the script
# stops — it never falls back to runbook/00-overview/variables.sh, whose
# defaults would make every result meaningless.
TRACK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_VARS="$TRACK_DIR/runbook/00-overview/variables.sh"
if [ -z "$VARS_FILE" ]; then
  VARS_FILE="$TRACK_DIR/site-vars.sh"
  if [ ! -f "$VARS_FILE" ]; then
    echo "FATAL: no site variables file. Pass --vars FILE, or create the default:" >&2
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

confirm() {
  # confirm "prompt text" -> returns 0 only on literal 'yes'
  local ans
  read -rp "$1 [yes/NO] " ans
  [ "$ans" = "yes" ]
}

echo "=== Cerberus PBS restore drill (guided) ==="
echo "This drill restores a backup to a SCRATCH location — never onto live"
echo "storage — and reports timing for the §07 sign-off table."
echo "VERIFY AGAINST A LIVE PBS BEFORE SIGN-OFF (see the header)."
echo ""

# --- Step 0: connection details (set-or-fail; nothing overwritten) --------
REPO="$(cerberus_pbs_repository)" || { echo "FATAL: cannot build the PBS repository string (see above)." >&2; exit 2; }
AUTH_ID="$(cerberus_pbs_auth_id)"
if [ -n "${PBS_REPOSITORY:-}" ] && [ "$PBS_REPOSITORY" != "$REPO" ]; then
  echo "FATAL: PBS_REPOSITORY is already set to '$PBS_REPOSITORY'," >&2
  echo "       but the site file builds '$REPO'. One construction only —" >&2
  echo "       unset PBS_REPOSITORY (or fix the site file) and re-run." >&2
  exit 2
fi
PBS_REPOSITORY="$REPO"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN — the commands that WOULD run:"
  echo "  export PBS_REPOSITORY='$PBS_REPOSITORY'"
  echo "  export PBS_FINGERPRINT=(from the site file or prompt); PBS_PASSWORD=(prompt, hidden)"
  echo "  proxmox-backup-client snapshot list vm/VMID"
  echo "  proxmox-backup-client snapshot files vm/VMID/TIME"
  echo "  proxmox-backup-client restore vm/VMID/TIME ARCHIVE /srv/restore/VMID-disk.img"
  echo "  qemu-img info /srv/restore/VMID-disk.img"
  echo "  qmrestore '$PBS_STORAGE_ID:backup/vm/VMID/TIME' NEW_VMID --unique 1 --storage STORAGE   # only with confirmation"
  echo "  qm set NEW_VMID --netN \"(its current netN value),link_down=1\"   # every NIC, before the first start"
  echo "DRY RUN: nothing executed."
  exit 0
fi

if [ -z "${PBS_FINGERPRINT:-}" ] || [ "$PBS_FINGERPRINT" = "PLACEHOLDER" ]; then
  read -rp "PBS server fingerprint (SHA256, from §06.2): " PBS_FINGERPRINT
fi
cerberus_check_fingerprint "$PBS_FINGERPRINT" || exit 1
if [ -z "${PBS_PASSWORD:-}" ]; then
  read -rsp "PBS token secret for '$AUTH_ID' (not echoed): " PBS_PASSWORD
  echo ""
fi
cerberus_require_secret PBS_PASSWORD || exit 1

echo ""
echo "Plan:"
echo "  1. Export PBS_REPOSITORY='$PBS_REPOSITORY' (+ fingerprint, hidden token)"
echo "  2. List snapshots for a test VMID (you choose)"
echo "  3. Restore one disk archive to a scratch path (you choose); qemu-img info"
echo "  4. Optionally qmrestore from '$PBS_STORAGE_ID' to a NEW vmid, isolated, and boot it"
echo ""

export PBS_REPOSITORY
export PBS_FINGERPRINT
export PBS_PASSWORD

command -v proxmox-backup-client >/dev/null 2>&1 \
  || { echo "'proxmox-backup-client' not found. Run this on a PVE node." >&2; exit 1; }

# --- Step 1: choose VMID --------------------------------------------------
read -rp "VMID whose backup you want to restore (a TEST VM, not production): " VMID
[[ "$VMID" =~ ^[0-9]+$ ]] || { echo "VMID must be numeric. Aborting." >&2; exit 1; }

echo ""
echo "--- Step 1: listing snapshots for vm/$VMID ---"
SNAP_LIST="$(proxmox-backup-client snapshot list "vm/$VMID" 2>&1)" || {
  echo "Failed to list snapshots:"; echo "$SNAP_LIST"; exit 1;
}
echo "$SNAP_LIST"
TIMESTAMPS="$(echo "$SNAP_LIST" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' | sort -u || true)"
[ -n "$TIMESTAMPS" ] || { echo "No snapshots found for vm/$VMID. Aborting." >&2; exit 1; }
echo ""
echo "Available snapshots:"; echo "$TIMESTAMPS" | nl
read -rp "Pick a snapshot (number, or paste the timestamp): " SNAP_CHOICE
if [[ "$SNAP_CHOICE" =~ ^[0-9]+$ ]]; then
  SNAP_TS="$(echo "$TIMESTAMPS" | sed -n "${SNAP_CHOICE}p")"
else
  SNAP_TS="$SNAP_CHOICE"
fi
grep -qxF -- "$SNAP_TS" <<<"$TIMESTAMPS" || { echo "Invalid selection. Aborting." >&2; exit 1; }
SNAPSHOT="vm/$VMID/$SNAP_TS"
echo "Selected snapshot: $SNAPSHOT"

# --- Step 2: list archives, choose one ------------------------------------
echo ""
echo "--- Step 2: archives in $SNAPSHOT ---"
proxmox-backup-client snapshot files "$SNAPSHOT"
read -rp "Archive to restore (e.g. drive-scsi0.img): " ARCHIVE
[ -n "$ARCHIVE" ] || { echo "Archive required. Aborting." >&2; exit 1; }

# --- Step 3: restore to scratch path --------------------------------------
read -rp "Scratch restore path [/srv/restore/$VMID-disk.img]: " SCRATCH
SCRATCH="${SCRATCH:-/srv/restore/$VMID-disk.img}"
if [ -e "$SCRATCH" ]; then
  echo "ERROR: '$SCRATCH' already exists — refusing to overwrite. Choose another path." >&2
  exit 1
fi

echo ""
echo "About to restore:"
echo "  repository: $PBS_REPOSITORY"
echo "  snapshot  : $SNAPSHOT"
echo "  archive   : $ARCHIVE"
echo "  target    : $SCRATCH   (scratch only — NOT live storage)"
confirm "Start the restore?" || { echo "Aborted."; exit 1; }

mkdir -p "$(dirname "$SCRATCH")"
T_START="$(date +%s)"
proxmox-backup-client restore "$SNAPSHOT" "$ARCHIVE" "$SCRATCH"
T_RESTORE="$(date +%s)"

echo ""
echo "--- Step 3: verifying restored image ---"
qemu-img info "$SCRATCH"
ls -lh "$SCRATCH"

# --- Step 4: optional boot test — qmrestore from PBS, isolated --------------
echo ""
echo "--- Step 4 (optional): qmrestore from PBS to a NEW vmid, isolated, and boot ---"
BOOT_OK="skipped"; T_BOOT="$T_RESTORE"
VOLID="$PBS_STORAGE_ID:backup/$SNAPSHOT"
if command -v qm >/dev/null 2>&1 && command -v qmrestore >/dev/null 2>&1; then
  read -rp "New VMID for the restored VM (must NOT exist; empty to skip): " NEW_VMID
  if [ -z "$NEW_VMID" ]; then
    echo "Skipped (no VMID given)."
  elif ! [[ "$NEW_VMID" =~ ^[0-9]+$ ]]; then
    echo "Skipped ('$NEW_VMID' is not a numeric VMID)."
  elif qm status "$NEW_VMID" >/dev/null 2>&1; then
    echo "Skipped (VM $NEW_VMID already exists — never overwritten)."
  else
    echo "Checking that PVE sees the backup volume through storage '$PBS_STORAGE_ID' ..."
    if ! pvesm list "$PBS_STORAGE_ID" --vmid "$VMID" | awk '{print $1}' | grep -qxF -- "$VOLID"; then
      echo "ERROR: volume '$VOLID' is not listed by 'pvesm list $PBS_STORAGE_ID --vmid $VMID'." >&2
      echo "       Output was:" >&2
      pvesm list "$PBS_STORAGE_ID" --vmid "$VMID" >&2 || true
      exit 1
    fi
    read -rp "Target storage for the restored disks [local-lvm]: " RESTORE_STORAGE
    RESTORE_STORAGE="${RESTORE_STORAGE:-local-lvm}"
    echo "This creates VM $NEW_VMID from $VOLID on storage $RESTORE_STORAGE,"
    echo "with NEW MAC addresses (--unique 1) and every NIC link_down=1 — the copy"
    echo "boots with no network, so it can never collide with the running original."
    if confirm "Run qmrestore -> VM $NEW_VMID?"; then
      # stderr is shown — a failure explains itself.
      qmrestore "$VOLID" "$NEW_VMID" --unique 1 --storage "$RESTORE_STORAGE"
      NICS="$(qm config "$NEW_VMID" | grep -E '^net[0-9]+:' || true)"
      while IFS= read -r line; do
        [ -n "$line" ] || continue
        key="${line%%:*}"; val="${line#*: }"
        case ",$val," in *",link_down=1,"*) continue ;; esac
        qm set "$NEW_VMID" "--$key" "$val,link_down=1"
      done <<<"$NICS"
      if qm config "$NEW_VMID" | grep -E '^net[0-9]+:' | grep -vq 'link_down=1'; then
        echo "FATAL: a NIC of VM $NEW_VMID is still linked — NOT starting it. Fix it by hand (link_down=1) first." >&2
        exit 1
      fi
      qm set "$NEW_VMID" --onboot 0
      echo "VM $NEW_VMID restored, isolated (all NICs link_down=1, new MACs)."
      if confirm "Start VM $NEW_VMID now (isolated) and time the boot?"; then
        qm start "$NEW_VMID"
        echo "Log in on the VM's console (PVE GUI -> $NEW_VMID -> Console)."
        read -rp "Press Enter once the restored VM has booted to a login (or type 'skip'): " BOOT_OK
        BOOT_OK="${BOOT_OK:-booted}"
        T_BOOT="$(date +%s)"
      fi
    else
      echo "Skipped qmrestore."
    fi
  fi
else
  echo "'qm'/'qmrestore' not available on this host — the boot test must be done from a PVE node."
  BOOT_OK="n/a"
fi

# --- Step 5: report ---------------------------------------------------------
echo ""
echo "================ RESTORE DRILL REPORT ================"
echo "Date            : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "Repository      : $PBS_REPOSITORY"
echo "Source VM       : $VMID"
echo "Snapshot        : $SNAPSHOT"
echo "Archive         : $ARCHIVE"
echo "Scratch target  : $SCRATCH"
echo "Restore duration: $(( T_RESTORE - T_START ))s"
echo "Boot test       : $BOOT_OK"
if [ "$BOOT_OK" != "skipped" ] && [ "$BOOT_OK" != "n/a" ] && [ "$BOOT_OK" != "skip" ]; then
  echo "RTO (list->boot): $(( T_BOOT - T_START ))s"
fi
echo "======================================================"
echo "Copy the RTO line into the §07 sign-off table (§7.8, item 10)."
echo "Scratch image left at $SCRATCH — delete it when the drill is signed off:"
echo "  rm -f '$SCRATCH'"
echo "Restored test VM (if any) is isolated; destroy it after sign-off: qm destroy NEW_VMID --purge"
