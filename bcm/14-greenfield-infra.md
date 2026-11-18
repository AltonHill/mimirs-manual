# 14 — Greenfield Support Infrastructure

> Jumpbox, DNS, and time sync for a fresh site — before 01's IP plan
> goes live. These are the boxes the cluster depends on but that aren't
> part of the cluster itself.

## 14.1 — Jumpbox (admin host)

A hardened Ubuntu 24.04 host for admin access. All BCM/k8s admin work
happens here — never from a laptop over VPN if you can avoid it.

**Which network.** "Management" is ambiguous in this track: BCM's
`mgmtnet` (`$NET_OOB`) is the **OOB/BMC** network, not a general admin
LAN. The jumpbox needs to reach (1) the head node's SSH and Base View,
(2) the BMCs on `$NET_OOB` for IPMI/console work, and (3) the service
addresses on `$NET_USER`. It must **not** sit on `internalnet`
(`$NET_PROVISION`) — that is BCM's provisioning network, where BCM
answers DHCP/PXE. Typical placement: an interface on the site's admin
network plus a route (or a second interface) to `$NET_OOB`. Record the
choice in 10.

> **Lockout-safe order — don't reorder it.** Allow rule in → key login
> **verified in a second session** → only then disable password auth →
> only then enable the firewall. Keep your first session open until the
> end. Each step checks its own precondition and refuses if it isn't met.
> SSH is allowed only from `$NET_ADMIN_SRC_A` / `$NET_ADMIN_SRC_B`
> (site-vars.sh). Their shipped defaults are documentation ranges that
> match nothing real, so step 5 refuses to enable the firewall until you
> set them to the networks you actually connect from.

**1. Base packages.**

```bash
# Fresh Ubuntu 24.04 Server install, then (source site-vars.sh first):
apt update && apt update && apt upgrade -y
apt install -y neovim git curl wget jq dnsutils chrony python3
```

**2. Allow rule in.** The firewall is still inactive, so this changes
nothing yet:

```bash
ufw default deny incoming
ufw default allow outgoing
for net in "${NET_ADMIN_SRC_A:?source site-vars.sh first}" ${NET_ADMIN_SRC_B:-}; do
  ufw allow from "$net" to any port 22 proto tcp
done
ufw show added        # expect one ALLOW 22/tcp rule per admin network
```

**3. Verify key login in a SECOND session.** From your admin
workstation, open a new terminal and leave this one open:

```bash
# on the workstation (your key must already be in ~/.ssh/authorized_keys here):
JUMPBOX_SSH="admin@jumpbox.example.internal"    # your login on the jumpbox
ssh -o PreferredAuthentications=publickey -o PasswordAuthentication=no "$JUMPBOX_SSH" 'echo KEY-LOGIN-OK'
```

Don't continue until that prints `KEY-LOGIN-OK`. If it doesn't, install
your key (`ssh-copy-id`) and retry. Password auth is still on, so
nothing is lost yet.

**4. Only then disable password auth.** Check the *effective* config,
not just the file. On Ubuntu cloud images, a drop-in under
`/etc/ssh/sshd_config.d/` can re-enable passwords and override the main
file:

```bash
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
grep -rl '^PasswordAuthentication yes' /etc/ssh/sshd_config.d/ 2>/dev/null   # any hit overrides — edit it to "no"
sshd -t && systemctl reload ssh
sshd -T | grep -E '^(passwordauthentication|permitrootlogin) '   # expect: no / prohibit-password
```

Repeat step 3's key login from the second session, and confirm that a
password login is now refused:
`ssh -o PubkeyAuthentication=no "$JUMPBOX_SSH"` should fail with
`Permission denied (publickey)`.

**5. Only then enable the firewall**, and only if the session you are
in comes from an allowed network. The guard fails closed:

- **Console** means the session's tty is a real tty device that is
  **not** a pts — `/dev/tty1`, `/dev/ttyS0`, `/dev/hvc0` — and there is
  no `SSH_CLIENT`. "Not a tty" (a pipe, a script, a job with no
  terminal) is **not** a console.
  - Console with no `SUDO_USER`: the source check is skipped.
  - Console **under sudo**: once sudo has cleared the environment the
    guard can't tell a console from an SSH session, so it proceeds only
    on an explicit acknowledgement, `CONSOLE_CONFIRMED=yes`, and
    otherwise refuses and names that variable. (On Ubuntu 24.04 root
    has no password, so a console operator is normally under sudo.)
- **Everything else** must show a source inside `$NET_ADMIN_SRC_A/B`:
  `SSH_CLIENT` when it is set, otherwise this tty's login record
  (`who -m`, which survives `sudo` and `su -`; **[unverified]** that
  sshd on the jumpbox records the client IP in utmp, so that `who -m`
  shows it in parentheses — observed only on stubs so far; if it shows
  none, the guard refuses, which is the safe failure). An empty `SSH_CLIENT`
  alone proves nothing: `sudo -i` / `sudo -s` strip it, and on Ubuntu
  24.04 (root SSH login disabled) the ordinary operator is an SSH
  session under sudo. A source that is missing, not an IP address (a
  hostname, a tmux/screen session), or outside the ranges is a refusal.

```bash
src="${SSH_CLIENT:-}"; src="${src%% *}"; via="SSH_CLIENT"
if [ -z "$src" ]; then
  t="$(tty)" || t=""                         # "not a tty" -> empty: never a console
  case "$t" in /dev/pts/*|"") con="" ;; /dev/*) con="$t" ;; *) con="" ;; esac
  if [ -n "$con" ] && [ -z "${SUDO_USER:-}" ]; then
    via="console"
  elif [ -n "$con" ]; then
    via="console+sudo"
  else
    # sudo -i/-s (or su -) cleared SSH_CLIENT: recover the source from this tty's login record
    src="$(who -m | sed -n 's/.*(\([^()]*\))[[:space:]]*$/\1/p')"; via="who -m"
  fi
fi
if [ "$via" = "console" ]; then
  echo "console session ($con; no SSH_CLIENT, no SUDO_USER): no lockout risk for this session" && ufw --force enable
elif [ "$via" = "console+sudo" ]; then
  if [ "${CONSOLE_CONFIRMED:-}" = "yes" ]; then
    echo "console session ($con) under sudo, acknowledged by CONSOLE_CONFIRMED=yes" && ufw --force enable
  else
    echo "REFUSED: tty $con is a console, but this shell is under sudo (SUDO_USER=${SUDO_USER:-}), which can hide an SSH session — if you are on the VM/BMC console, set CONSOLE_CONFIRMED=yes and re-run this block"
  fi
elif [ -n "$src" ] && python3 -c 'import ipaddress,sys
try: ip=ipaddress.ip_address(sys.argv[1])
except ValueError: sys.exit(1)
sys.exit(0 if any(ip in ipaddress.ip_network(n, strict=False) for n in sys.argv[2:] if n) else 1)' \
     "$src" "${NET_ADMIN_SRC_A:?source site-vars.sh first}" ${NET_ADMIN_SRC_B:-}; then
  echo "session source $src (from $via) is inside NET_ADMIN_SRC_A/B" && ufw --force enable
else
  echo "REFUSED: session source '${src:-unknown}' (from $via) is not an address inside NET_ADMIN_SRC_A/B — enabling ufw would lock you out"
fi
ufw status verbose
```

**On the console under sudo**, the acknowledgement is the documented
path. First confirm you really are on the VM or BMC console: `tty`
shows `/dev/tty1`, `/dev/ttyS*` or `/dev/hvc*`, and `who -m` shows no
remote host in parentheses. Then set `export CONSOLE_CONFIRMED=yes` in
that shell, re-run the block, and `unset CONSOLE_CONFIRMED` afterwards.
Never set it in an SSH session: it tells the guard to skip the only
check that prevents the lockout.

Finally, from the **second** session, log in once more. Only after that
succeeds, close the first. Locked out anyway? From the VM or BMC
console: `ufw disable`.

**6. Tooling.**

```bash
# kubectl + helm (match the cluster versions from site-vars.sh). Pinned
# release binaries, checksum-verified — never a script piped from a
# branch head (L10).
curl -fLO "https://dl.k8s.io/release/v${K8S_VERSION}.0/bin/linux/amd64/kubectl"
curl -fLO "https://dl.k8s.io/release/v${K8S_VERSION}.0/bin/linux/amd64/kubectl.sha256"
echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check
install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
: "${HELM_VERSION:?set HELM_VERSION in site-vars.sh (the helm release from the plan in 10), then source it}"
curl -fLO "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz"
curl -fLO "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz.sha256sum"
sha256sum --check "helm-v${HELM_VERSION}-linux-amd64.tar.gz.sha256sum"
tar -xzf "helm-v${HELM_VERSION}-linux-amd64.tar.gz" linux-amd64/helm
install -o root -g root -m 0755 linux-amd64/helm /usr/local/bin/helm
```

- [ ] Key login verified in a second session **before** password auth was disabled
- [ ] `sshd -T` shows `passwordauthentication no`; a password login is refused
- [ ] `ufw status` shows one SSH rule per admin network (`$NET_ADMIN_SRC_A`/`B`), firewall active, second-session login still works
- [ ] `kubectl version --client`, `helm version` match the plan

## 14.2 — DNS (Technitium or site DNS)

The cluster needs forward **and** reverse DNS for every node and
service FQDN from 01 (the Run:ai and `cm-*-setup` wizards check both).
For greenfield, run Technitium DNS Server on the jumpbox or a small
dedicated VM; brownfield can use the site's existing DNS servers.

```bash
# Technitium DNS Server (self-hosted authoritative + recursive).
# Download, read, then run — never piped straight into a root shell
# (L10). The installer fetches the current release; record the installed
# version in 10.
curl -fsSL -o technitium-install.sh https://download.technitium.com/dns/install.sh
less technitium-install.sh
sudo bash technitium-install.sh
# Admin console: http://<dns-ip>:5380  (default admin / admin — change it)
```

In the console (or via its HTTP API):

1. Add a primary zone for `$DOMAIN`.
2. A records for every node in 01 (`bcm-headnode`, `k8s-ctrl-1..3`,
   `gpu-worker-*`) and every service (`kgateway`, `runai`,
   `runai-inference`, plus `*.runai` and `*.runai-inference` wildcards).
3. Reverse zones for each network in 01; PTR records for every A record.
4. Set the cluster nodes and headnode to use this DNS (BCM: set on the
   networks in 01, or via DHCP).

Verify from the jumpbox:

```bash
for h in bcm-headnode.$DOMAIN k8s-ctrl-1.$DOMAIN $FQDN_RUNAI $FQDN_INFERENCE; do
  dig +short $h @$DNS_IP
done
dig +short -x $IP_RUNAI @$DNS_IP
```

- [ ] Every FQDN resolves forward; every IP resolves back (PTR)
- [ ] Wildcards `*.runai.$DOMAIN` / `*.runai-inference.$DOMAIN` resolve

## 14.3 — Time sync (Chrony)

Kerberos, TLS, and etcd all hate clock skew. Chrony runs on the
jumpbox/DNS host syncing upstream; the BCM headnode syncs from it and
serves the cluster.

On the time source (jumpbox or dedicated host):

```bash
apt install -y chrony
# networks come from site-vars.sh (source it first); written to a temp
# file and moved into place only if every network expanded
tmp=$(mktemp) && cat > "$tmp" <<EOF && install -m 0644 "$tmp" /etc/chrony/conf.d/site.conf
# Upstream NTP — replace with the site's approved sources
pool pool.ntp.org iburst
# Serve time to the cluster networks
allow ${NET_PROVISION:?source site-vars.sh first}
allow ${NET_STORAGE:?source site-vars.sh first}
allow ${NET_USER:?source site-vars.sh first}
allow ${NET_OOB:?source site-vars.sh first}
EOF
rm -f "$tmp"
grep '^allow' /etc/chrony/conf.d/site.conf    # expect the four networks from 01
systemctl enable --now chrony
chronyc sources
```

On the BCM headnode, point Chrony at the time source instead of the
public pools, then let BCM distribute time to the nodes (BCM manages
node NTP from the headnode by default — verify with `cmsh` time settings
per the BCM 11 docs if the site needs custom sources).

```bash
chronyc tracking   # Leap status normal, Stratum <= 4
chronyc sources -v # the site source is reachable and selected
```

- [ ] `chronyc tracking` shows synchronized, offset < 50ms
- [ ] Headnode serves NTP to all four cluster networks
- [ ] Spot-check a GPU node: `chronyc tracking` agrees within a second
