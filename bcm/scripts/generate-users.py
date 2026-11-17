#!/usr/bin/env python3
"""generate-users.py — BCM user accounts from a CSV inventory.

Lesson learned the hard way: keep the cmsh user commands MINIMAL.
Passing id, groupid, homedirectory, loginshell etc. causes weird
interactions with BCM's OpenLDAP backend (shell/ID/home weirdness).
Just add the user and set a temp password — let BCM assign the
defaults.

CSV columns, in this order (header row required):
    username,sudo

  username  login name, e.g. bcm-admin
  sudo      yes | no — whether the user gets the sudoers drop-in
            (section 15.2), not via cmsh

Temporary passwords live in a SEPARATE file, never in the accounts CSV,
never in the generated script, never on a command line:
    username,password          (header row required)
The file must be mode 0600 (or stricter) and must not be committed.

FAIL CLOSED (H6 class): generation refuses while any account has no
password, or a password that is still a placeholder (<...>, PLACEHOLDER,
CHANGEME, TEMP-...) — and the generated script re-checks at run time.

Usage:
    cp accounts-template.csv accounts.csv              # fill in real usernames
    (umask 077; nvim passwords.csv)                    # username,password — 0600
    ./generate-users.py --check accounts.csv           # validate accounts only
    ./generate-users.py --check accounts.csv --passwords passwords.csv
    ./generate-users.py accounts.csv --passwords passwords.csv > create-users.sh
    ./generate-users.py --sudoers accounts.csv > bcm-admins   # sudoers drop-in
    bash -n create-users.sh
    bash create-users.sh passwords.csv                 # on the BCM head node, as root

How the generated script behaves (and what it claims, in its header):
  - add-or-verify: an account that already exists is a NOTE, and its
    password is NOT reset — re-running is safe;
  - each password reaches cmsh on stdin (printf is a bash builtin, so it
    never appears in `ps` or shell history), never in argv;
  - every account is attempted; failures are counted and reported, and
    the script exits 1 at the end if any account failed.
"""

import argparse
import csv
import os
import re
import stat
import sys

COLUMNS = ["username", "sudo"]
PW_COLUMNS = ["username", "password"]
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# A password that is still a fill-in. Shared with the generated script.
PLACEHOLDER_PW_RE = re.compile(r"<[^<>]*>|PLACEHOLDER|CHANGEME|^TEMP-", re.I)
PLACEHOLDER_PW_ERE = r"<[^<>]*>|PLACEHOLDER|CHANGEME|^TEMP-"


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            fail(f"expected header exactly: {','.join(COLUMNS)}\n"
                 f"got: {','.join(reader.fieldnames or [])}")
        rows = [r for r in reader if any((r or {}).values())]
    return rows


def check(rows):
    errors, seen = [], set()
    for i, r in enumerate(rows, start=2):
        u = (r["username"] or "").strip()
        tag = f"line {i} ({u or '?'})"
        if not USER_RE.match(u):
            errors.append(f"{tag}: bad username '{u}'")
        if u in seen:
            errors.append(f"{tag}: duplicate username '{u}'")
        seen.add(u)
        if (r["sudo"] or "").strip().lower() not in ("yes", "no"):
            errors.append(f"{tag}: sudo must be yes|no")
    return errors


def check_passwords(rows, pw_path):
    """Problems with the passwords file — names only, never values."""
    errors = []
    try:
        st = os.stat(pw_path)
    except OSError as e:
        return [f"passwords file {pw_path}: {e.strerror}"]
    if stat.S_ISREG(st.st_mode) and st.st_mode & 0o077:
        errors.append(f"passwords file {pw_path} is mode "
                      f"{stat.S_IMODE(st.st_mode):04o} — must be 0600 or stricter "
                      f"(chmod 600 {pw_path})")
    with open(pw_path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != PW_COLUMNS:
            return errors + [f"passwords file: expected header exactly: "
                             f"{','.join(PW_COLUMNS)}"]
        pw = {}
        for r in reader:
            u = (r.get("username") or "").strip()
            if u:
                if u in pw:
                    errors.append(f"passwords file: duplicate entry for '{u}'")
                pw[u] = r.get("password") or ""
    for r in rows:
        u = r["username"].strip()
        p = pw.get(u)
        if p is None:
            errors.append(f"{u}: no password in the passwords file")
        elif not p:
            errors.append(f"{u}: empty password")
        elif PLACEHOLDER_PW_RE.search(p):
            errors.append(f"{u}: password is still a placeholder — refusing")
        elif re.search(r'[,"\\\s]', p):
            errors.append(f"{u}: password contains a comma, quote, backslash or "
                          f"whitespace — the generated script reads simple CSV "
                          f"and quotes it for cmsh; choose another")
        elif p == u:
            errors.append(f"{u}: password equals the username")
    extra = sorted(set(pw) - {r["username"].strip() for r in rows})
    if extra:
        errors.append(f"passwords file has entries for unknown accounts: "
                      f"{', '.join(extra)}")
    return errors


SCRIPT_HEAD = r'''#!/bin/bash
# Generated by generate-users.py — REVIEW, then run as root on the BCM head node:
#     bash create-users.sh /path/to/passwords.csv
# Minimal switches on purpose: BCM/OpenLDAP assigns id, home, and shell defaults.
# Behavior (exactly what this script does):
#   - refuses to start unless the passwords file is mode 0600-or-stricter and
#     every account below has a non-placeholder password in it;
#   - add-or-verify: an existing account is a NOTE and its password is NOT reset;
#   - passwords go to cmsh on stdin (printf builtin), never on a command line;
#   - every account is attempted; failures are counted, and the script exits 1
#     at the end if any account failed.
set -uo pipefail

PWFILE="${1:?usage: bash create-users.sh PASSWORDS_FILE (username,password; mode 0600)}"
[ -f "$PWFILE" ] || { echo "FATAL: $PWFILE not found" >&2; exit 2; }
case "$(stat -c %a "$PWFILE")" in
    600|400) ;;
    *) echo "FATAL: $PWFILE must be mode 0600 (chmod 600 $PWFILE)" >&2; exit 2 ;;
esac

pw_for() {  # username -> password (never echoed by this script)
    awk -F, -v u="$1" 'NR > 1 && $1 == u { sub(/^[^,]*,/, ""); print; exit }' "$PWFILE"
}

ACCOUNTS=(__ACCOUNTS__)
missing=0
for u in "${ACCOUNTS[@]}"; do
    pw="$(pw_for "$u")"
    if [ -z "$pw" ] || printf '%s' "$pw" | grep -Eqi '__PLACEHOLDER_ERE__'; then
        echo "FATAL: $u has no real password in $PWFILE (empty or placeholder)" >&2
        missing=$((missing + 1))
    fi
done
unset pw
[ "$missing" -eq 0 ] || { echo "FATAL: $missing account(s) without a real password — nothing created" >&2; exit 2; }

existing="$(cmsh -c "user; list")" || { echo "FATAL: cmsh user list failed" >&2; exit 2; }
failed=0
for u in "${ACCOUNTS[@]}"; do
    if printf '%s\n' "$existing" | awk '{print $1}' | grep -qx "$u"; then
        echo "NOTE: $u already exists — left as is (password NOT reset)"
        continue
    fi
    pw="$(pw_for "$u")"
    if printf 'user\nadd %s\nset password "%s"\ncommit\n' "$u" "$pw" | cmsh -q; then
        echo "OK:   $u created"
    else
        echo "FAIL: $u (cmsh rc=$?)" >&2
        failed=$((failed + 1))
    fi
    unset pw
done

cmsh -c "user; list"
if [ "$failed" -ne 0 ]; then
    echo "$failed account(s) FAILED — see above" >&2
    exit 1
fi
echo "all ${#ACCOUNTS[@]} account(s) present"
'''


def main():
    ap = argparse.ArgumentParser(description="Generate BCM user-creation commands from CSV.")
    ap.add_argument("csv", help="account inventory CSV")
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--passwords", default=None,
                    help="username,password file (mode 0600); required to generate "
                         "the creation script")
    ap.add_argument("--sudoers", action="store_true",
                    help="emit the /etc/sudoers.d/bcm-admins drop-in instead")
    a = ap.parse_args()

    rows = load(a.csv)
    errors = check(rows)
    if not errors and a.passwords:
        errors += check_passwords(rows, a.passwords)
    if not errors and not (a.check or a.sudoers or a.passwords):
        errors.append("--passwords FILE is required to generate the creation "
                      "script (temporary passwords never live in the CSV or "
                      "the script)")
    if errors:
        print(f"{len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {len(rows)} users validated"
          + (" (passwords file checked)" if a.passwords else ""), file=sys.stderr)
    if a.check:
        return

    if a.sudoers:
        admins = [r["username"].strip() for r in rows
                  if r["sudo"].strip().lower() == "yes"]
        print("# BCM-managed administrative users — least privilege: list only admins")
        for u in admins:
            print(f"{u} ALL=(ALL:ALL) ALL")
        return

    accounts = " ".join(r["username"].strip() for r in rows)
    print(SCRIPT_HEAD.replace("__ACCOUNTS__", accounts)
          .replace("__PLACEHOLDER_ERE__", PLACEHOLDER_PW_ERE), end="")


if __name__ == "__main__":
    main()
