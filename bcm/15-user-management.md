# 15 — BCM User Accounts & Sudo Access

> **Prerequisites:** 02 (BCM head node up). Run as `root` on the head node.
> BCM users are backed by BCM's LDAP — accounts created here resolve on
> all managed nodes.
>
> **Keep it minimal.** Passing `id`, `groupid`, `homedirectory`,
> `loginshell` etc. on the `user add` line causes weird interactions
> with BCM's OpenLDAP backend (shell/ID/home weirdness). Just add the
> user and set a temp password — BCM assigns sane defaults.

## 15.1 — Prepare the account list

Copy the template and fill in real usernames (columns in order):

```bash
cp scripts/accounts-template.csv accounts.csv
```

```text
username,sudo
bcm-admin,yes
ops-01,yes
field-01,no
vendor-01,no
```

- **Dedupe first.** The source data for this section had one username
  twice and another with conflicting IDs — decide the authoritative
  list before creating anything.
- `sudo` = `yes` puts the user in the sudoers drop-in (15.2).

Temporary passwords go in a **separate** file (`username,password`,
mode `0600`), never in `accounts.csv`, the generated script, or a
command line. Create it with a restrictive umask and pick each
temporary password per the site's policy:

```bash
(umask 077; printf 'username,password\n' > passwords.csv)
nvim passwords.csv          # one "username,password" line per account in accounts.csv
ls -l passwords.csv         # expect -rw-------
```

Generate. The generator **refuses** while any account has no password,
or one that is still a placeholder (`<…>`, `PLACEHOLDER`, `CHANGEME`,
`TEMP-…`), or while the file is readable by group or others:

```bash
scripts/generate-users.py --check accounts.csv --passwords passwords.csv
scripts/generate-users.py accounts.csv --passwords passwords.csv > create-users.sh
bash -n create-users.sh   # syntax check
less create-users.sh      # review before running — it contains no passwords
bash create-users.sh passwords.csv
```

What the generated script does, and its header says the same:

- It re-checks the passwords file at run time (mode, no placeholders)
  and creates nothing if any account lacks a real password.
- Add-or-verify: an account that already exists gets a NOTE and its
  password is **not** reset, so a re-run is safe.
- Each account is still the minimal `user; add …; set password …;
  commit`, but it reaches `cmsh` on **stdin** (`printf … | cmsh -q`; the
  `printf` builtin never shows up in `ps` or shell history), not as a
  `-c` argument. **[unverified]**: piping commands into `cmsh` on stdin
  is assumed to behave like an interactive session. If it doesn't, set
  each password in an interactive `cmsh` session instead.
- Every account is attempted. Failures are counted and reported, and
  the script exits 1 at the end if any account failed.

> **Password handling:** BCM forces a change on first login. Delete
> `passwords.csv` once the accounts are created (`shred -u
> passwords.csv`). Never store real passwords in docs, tickets, chat, or
> the repo.

Verify:

```bash
cmsh -c "user; list"
getent passwd bcm-admin      # from a managed node
id bcm-admin
```

## 15.2 — Sudo access (in the software image)

Generate the drop-in from the same CSV:

```bash
scripts/generate-users.py --sudoers accounts.csv > bcm-admins
cat bcm-admins   # review: only admins should be listed
```

Install it into **every** image the nodes boot (control plane, CPU
workers, GPU), from the head node, **outside** any chroot. A file on the
head node isn't visible from inside `cm-chroot-sw-img`, so the copy
targets each image's path directly:

```bash
# run from the directory holding bcm-admins
ok=1
for img in $IMG_CP $IMG_WRK $IMG_GPU; do
  d="/cm/images/$img/etc/sudoers.d"
  if [ -d "$d" ] && install -o root -g root -m 0440 bcm-admins "$d/bcm-admins" \
       && visudo -cf "$d/bcm-admins"; then
    echo "OK   $img"
  else
    echo "FAIL $img ($d)"; ok=0
  fi
done
[ "$ok" -eq 1 ] && echo "sudoers drop-in installed in all images" || echo "STOP: fix the FAIL lines before pushing"
# expected per image: .../etc/sudoers.d/bcm-admins: parsed OK
```

(Additional GPU types have their own images: add them to the loop.)

Alternative for larger teams: one LDAP group + one rule
(`%bcm-admins ALL=(ALL:ALL) ALL`) instead of per-user lines.

Push to the nodes (one at a time, wait for `[ COMPLETED ]`). Every
node of every category needs it. Add CPU-worker hostnames to the list:

```bash
for n in $K8S_CTRL $GPU_WORKERS; do
  cmsh -q -c "device; imageupdate -n $n -w"
done
```

## 15.3 — Validate per user

```bash
# TEST_USER: an account from accounts.csv · TEST_NODE: any managed node
ssh "${TEST_USER:?set TEST_USER to the account under test}@${TEST_NODE:?set TEST_NODE to a managed node}"   # change the temp password on first login
sudo -l && sudo whoami   # (on the node) expect: root
```

## Troubleshooting

- **User not found on node:** `cmsh -c "user; list"`, then check the
  node is on the intended image and LDAP/NSS is healthy.
- **Sudo syntax error:** `visudo -cf /etc/sudoers.d/bcm-admins`;
  drop-in must be `root:root` mode `0440`.
- **Weird shell/home/ID after creation:** you passed too many options
  to `user add` — delete the user and re-create with the minimal form
  in 15.1.
