#!/usr/bin/env python3
"""
anonymize.py -- anonymize an Obsidian-style markdown bundle before it leaves
your laptop. Stdlib only, no dependencies, no network, no LLM.

Detection runs over file CONTENTS and relative FILE PATHS alike (a name is
an identifier too). Replacement is ONE combined pass per text, so nothing
a pass inserts is ever re-matched by another pass. At any position the
first matching class wins, in this priority order:

  1. Hostnames. Explicit-list driven: only FQDNs under --domains
     (repeatable, comma-delimited, or --domain-file) are anonymized.
     FQDNs with a host part become node-001, node-002, ... under a
     generic domain (--domain, default example.internal); a listed domain
     itself (bhicorp.com) collapses to the generic domain, as does any
     kept FQDN that is the parent domain of other kept FQDNs
     (acl.eng.cdwbuilt.com above acl-bcm-hn01.acl.eng.cdwbuilt.com).
     A listed domain buried mid-token (hn01.acme.com.prod) is still
     caught, and a listed domain beats a file-extension-looking last
     label (hub.acme.sh under acme.sh). Host identity is case-insensitive
     (HN01.ACME.COM and hn01.acme.com are one node), labels may contain
     underscores (acl_bcm_hn01), and replacement is case-insensitive.
     Bare short hostnames are auto-derived from anonymized FQDNs (a bare
     acl-bcm-hn01 is the same host as its FQDN; disable with
     --no-derive-bare), and still caught via --host-pattern regexes plus
     ssh/scp/mosh/ping targets (options and their values are skipped; for
     scp only the host:path / user@host arguments are hosts) and user@host
     contexts. A bare name followed by .label... is left to the FQDN pass
     only when what follows ends like a domain (www.github.com,
     dbx.log.globex.com stay intact); followed by anything else it is a
     filename and the bare host is scrubbed whatever the extension
     (hn01.md -> node-001.md, hn01.docx -> node-001.docx).
     Everything else -- Kubernetes field paths (spec.containers), Helm
     values (nfd.enabled), cluster.local, public vendor domains -- is left
     alone by design. --scan lists the non-listed domains it saw so a
     forgotten client domain is visible before anything is written.
  2. InfiniBand / hardware IDs. GUIDs (0x0011223344556677), GIDs (fe80::
     forms and full 8-group colon forms such as de:ad:be:ef:00:11:22:33)
     -> <ib-guid-001> etc.; 6-group MAC addresses, colon or dash form
     (aa:bb:cc:dd:ee:ff, AA-BB-CC-DD-EE-FF -- one MAC whichever separator)
     -> <mac-addr-001> etc. (own map section); ibnet* fabric names ->
     fabric-net-001 etc. (standard tools such as ibnetdiscover are left
     alone). IDs are case-insensitive. Shorter colon strings (time stamps
     like 12:34:56:78) are not IDs and are left alone. Bare ib0/ib1
     interface names are left as-is (generic Linux names) and reported.
  3. Networks. Every distinct IPv4 /24 found is remapped to a sequential
     documentation range: 10.10.11.0/24, 10.10.12.0/24, ... (--net-base,
     --net-start), skipping any range the bundle itself uses. Host octets
     are preserved, so 10.200.14.5 -> 10.10.11.5. Leading zeros are
     normalized (010.200.014.005 is the same address as 10.200.14.5).
     CIDR suffixes are kept. Reverse-DNS names map through the same net
     map, so they stay paired with their forward address:
     5.14.200.10.in-addr.arpa -> 5.11.10.10.in-addr.arpa when 10.200.14.5
     -> 10.10.11.5 (a /24 zone 14.200.10.in-addr.arpa likewise; a net seen
     only in reverse form is still collected). Loopback, link-local,
     multicast and netmasks are left alone.
  4. Client / company names. --client "Acme Widgets" (repeatable;
     comma-delimited lists also accepted: --client "Acme, Globex") or
     --client-file names.txt (one per line -- keeps real names out of shell
     history; also the way to pass a name containing a literal comma).
     Case-insensitive, with a static stand-in (--replacement, default
     "Wayland Megacorp"). The words of a name may be joined by any
     whitespace run (line breaks and tabs included), hyphens, underscores,
     punctuation or nothing at all: "Acme Widgets", "Acme\\nWidgets",
     "acme-widgets", "acme_widgets" and "AcmeWidgets" all match. The first
     and last word of a multi-word name also match ALONE ("At Acme we",
     "Acme's"), except words under 3 characters, generic corporate
     suffixes (Corp, Inc, LLC, Group, ...) and common English words (The,
     One, New, ...); a word matched alone never matches inside a larger
     word. An unlisted domain carrying the client's name (dbx.log.globex.com
     for --client Globex) is listed automatically.
     A client name -- or a word of it matched alone -- that would match
     inside the tool's own vocabulary (the stand-in, node,
     example.internal, fabric-net, ib-guid, mac-addr, the net base) is
     refused up front.

  After anonymizing, every output text, every output filename and every
  file copied without scrubbing is re-scanned for every collected real
  value (client names, hostnames/FQDNs, IPs, GUIDs, MACs, ibnet names).
  Survivors print as LEAK lines on stderr and the run exits 3.

Files that cannot be scrubbed (binary suffixes, not valid UTF-8, or
containing NUL bytes -- e.g. UTF-16 without a BOM) are copied VERBATIM
under their anonymized filename and listed in a "copied WITHOUT
scrubbing" manifest -- scrub those by hand. Their contents are still
detection surface (read as latin-1, NULs stripped too), so an identifier
found in them is collected and the re-scan flags the file. .git
directories in an input are never read or copied.

Nothing is ever edited in place: --out must not be, or be inside, an
input; no path below --out may be a symlink, and every destination must
resolve inside --out; a pre-existing output file whose content differs
aborts the run. Dry-run first, read the diff, then write:

    ./anonymize.py --client-file clients.txt --dry-run ~/obsidian/bcm-bundle | nvim -
    ./anonymize.py --client-file clients.txt --out ~/bcm-bundle-anon ~/obsidian/bcm-bundle

If two anonymized filenames would collide the run aborts loudly instead of
overwriting.

A mapping of old -> new values is written to anonymize-map.json NEXT TO
(never inside) the output directory: <parent-of-out>/anonymize-map.json,
or --map PATH. THAT FILE CONTAINS THE REAL VALUES. Do not commit it. Ever.

Exit codes: 0 clean; 1 no input files, or filename collision; 2 usage
error or refused by a guard (--out inside input, no client names,
client/vocabulary collision, pre-existing output differs, --map inside
--out); 3 LEAK survivors found.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

# ---------------------------------------------------------------- constants

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
                 ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz",
                 ".rpm", ".deb", ".iso", ".img", ".qcow2", ".vmdk", ".pyc",
                 ".mp4", ".mp3", ".wav", ".ogg"}

FILE_EXTS = {"md", "markdown", "yml", "yaml", "json", "toml", "ini", "cfg",
             "conf", "log", "txt", "sh", "bash", "zsh", "py", "pl", "rb",
             "go", "rs", "js", "ts", "java", "c", "h", "cpp", "cc", "html",
             "htm", "css", "xml", "csv", "tsv", "png", "jpg", "jpeg", "gif",
             "svg", "webp", "pdf", "zip", "tar", "gz", "rpm", "deb", "iso",
             "service", "timer", "socket", "mount", "target", "key", "crt",
             "pem", "pub", "sql", "db", "sqlite", "env"}

# Every extension-looking last label (used by the derived-bare rule).
ALL_EXTS = FILE_EXTS | {s[1:] for s in SKIP_SUFFIXES}

# Public / vendor domains that are never treated as customer hostnames.
PUBLIC_DOMAINS = {
    "github.com", "raw.githubusercontent.com", "gist.github.com",
    "gitlab.com", "bitbucket.org", "docker.io", "hub.docker.com",
    "quay.io", "ghcr.io", "kubernetes.io", "k8s.io", "helm.sh",
    "redhat.com", "access.redhat.com", "ubuntu.com", "debian.org",
    "archlinux.org", "nvidia.com", "mellanox.com", "kernel.org",
    "python.org", "pypi.org", "golang.org", "rust-lang.org", "nodejs.org",
    "microsoft.com", "google.com", "cloud.google.com", "aws.amazon.com",
    "azure.microsoft.com", "oracle.com", "intel.com", "amd.com",
    "supermicro.com", "dell.com", "hpe.com", "lenovo.com", "slack.com",
    "zoom.us", "wikipedia.org", "stackoverflow.com", "serverfault.com",
}

# Standard InfiniBand diagnostic tools: names, not fabrics.
IBNET_TOOLS = {"ibnetdiscover", "ibnetinfo", "ibnetfind", "ibnetstat",
               "ibnetwatch", "ibnetping", "ibnetcount"}

# Directories never read from an input (VCS metadata is not bundle content
# and its compressed objects could never be scrubbed or re-scanned).
VCS_DIRS = {".git"}

# Words that follow "ssh"/"ping" in prose ("ssh into the box") and are not
# hosts; and names that are never a customer host.
SSH_STOPWORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in",
                 "into", "is", "it", "of", "on", "onto", "or", "over",
                 "the", "then", "to", "via", "with", "using", "again",
                 "back", "access", "agent", "config", "key", "keys",
                 "session", "sessions", "tunnel", "tunnels", "command",
                 "commands", "connection", "connections", "client"}
NEVER_HOSTS = {"localhost", "localdomain"}

# One DNS-ish label; underscores allowed inside (SRV records, Windows
# names, Ansible inventories), never at either end.
LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?"
HOST_TOKEN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.\-]*[A-Za-z0-9])?")

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
INADDR_TAIL = r"\.in-addr\.arpa(?![A-Za-z0-9-])"
INADDR_RE = re.compile(INADDR_TAIL, re.IGNORECASE)
# Reverse-DNS names: d.c.b.a.in-addr.arpa (a host) or c.b.a.in-addr.arpa (a
# /24 zone). The octets are a real address written backwards.
REV_SRC = r"(?<![\d.])(?:\d{1,3}\.){2,3}\d{1,3}(?=" + INADDR_TAIL + ")"
REV_RE = re.compile(REV_SRC, re.IGNORECASE)
FQDN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + LABEL + r"\.)+[A-Za-z]{2,63}(?![A-Za-z0-9])")
GUID_HEX_RE = re.compile(r"(?<![0-9A-Za-z_])0[xX][0-9a-fA-F]{12,16}(?![0-9A-Za-z_])")
# Real GID shapes only: fe80:: link-local forms, or full 8-group colon
# forms (which include the de:ad:be:ef:00:11:22:33 GUID form).
GID_RE = re.compile(
    r"(?<![0-9A-Za-z_:])"
    r"(?:fe80::(?:[0-9a-f]{1,4}:){0,5}[0-9a-f]{1,4}"
    r"|(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4})"
    r"(?![0-9A-Za-z_:])", re.IGNORECASE)
# 6-group MACs, colon (aa:bb:..) or dash (AA-BB-..) form, one separator
# throughout. Identity is separator-insensitive: see mac_key().
# A dash may sit next to a MAC (nic-00-1A-...), just not as part of a longer
# run of hex pairs (no 6-of-7-group matches).
MAC_PRE = r"(?<![0-9A-Za-z_:])(?<![0-9A-Fa-f]{2}-)"
MAC_POST = r"(?![0-9A-Za-z_:])(?!-[0-9A-Fa-f]{2}(?![0-9A-Za-z_]))"
MAC_RE = re.compile(
    MAC_PRE + r"[0-9a-f]{2}(?P<sep>[:-])(?:[0-9a-f]{2}(?P=sep)){4}[0-9a-f]{2}"
    + MAC_POST, re.IGNORECASE)
IBNET_RE = re.compile(r"\bibnet[a-z0-9_-]*\b", re.IGNORECASE)
IB_IFACE_RE = re.compile(r"\bib\d+\b")
SSH_VERB_RE = re.compile(
    r"(?<![^\s;>|&(`])(ssh|scp|mosh|ping)[ \t]+([^\n;|&`]*)")
USER_AT_RE = re.compile(
    r"([A-Za-z0-9_.\-]+)@([A-Za-z0-9](?:[A-Za-z0-9_.\-]*[A-Za-z0-9])?)")

# Options that take a value, per verb (the value is never a host).
VALUE_OPTS = {
    "ssh": set("BbcDEeFIiJLlmOoPpQRSWw"),
    "scp": set("cDFiJloPSX"),
    "mosh": set("p"),
    "ping": set("cFIilMmpQSsTtWw"),
}
LONG_VALUE_OPTS = {"--ssh", "--port", "--server", "--client", "--predict",
                   "--family", "--bind-server"}

# A bare host label followed by .label... is left to the FQDN pass ONLY when
# what follows actually ends like a domain: its last label is a TLD-shaped
# label (any two letters, or one of DOMAIN_TLDS) and the tail is not just
# file extensions. Everything else -- hn01.md, hn01.tar.gz, hn01.docx,
# hn01.backup.xlsx -- is a filename, and the bare host is scrubbed whatever
# the extension. www.github.com stays intact.
DOMAIN_TLDS = {"com", "net", "org", "edu", "gov", "mil", "int", "info", "biz",
               "name", "pro", "arpa", "dev", "cloud", "tech", "internal",
               "local", "localdomain", "lan", "home", "corp", "intranet",
               "private", "test", "example", "invalid", "onion"}
# Two-letter extensions that are also ccTLD-shaped: treated as extensions
# (scrub), since a bare host must never hide behind a filename.
SHORT_EXTS = {"so", "ko", "ps", "in", "am", "po", "mo", "rc", "mk", "cs",
              "hs", "ml", "el", "vb", "ex", "lz", "7z"}
_EXT_ALT = "|".join(sorted((re.escape(e) for e in ALL_EXTS | SHORT_EXTS),
                           key=len, reverse=True))
_TLD_ALT = "|".join(sorted(DOMAIN_TLDS, key=len, reverse=True)) + "|[A-Za-z]{2}"
_TOKEN_END = r"(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9])"
BARE_TAIL = (r"(?!(?!(?:\.(?:" + _EXT_ALT + r"))+" + _TOKEN_END + r")"
             r"\.(?:" + LABEL + r"\.)*(?:" + _TLD_ALT + r")" + _TOKEN_END + r")")

# Joiner between the words of a client name: any run of whitespace,
# punctuation, hyphens or underscores -- or nothing. Never a path
# separator, so a name can't swallow directory structure.
CLIENT_JOIN = r"(?:[^\w/\\]|_)*"


# ---------------------------------------------------------------- helpers

def ip_ints(tok):
    """Canonical octets of an IPv4 token, or None if it isn't one."""
    try:
        parts = [int(p) for p in tok.split(".")]
    except ValueError:
        return None
    if len(parts) != 4 or not all(0 <= p <= 255 for p in parts):
        return None
    return parts


def valid_ip(tok):
    return ip_ints(tok) is not None


def skip_ip(ints):
    """Addresses that are not identifying: leave them alone."""
    if ints in ([0, 0, 0, 0], [255, 255, 255, 255]):
        return True
    if ints[0] == 127 or ints[0] >= 224:   # loopback, multicast/reserved, netmasks
        return True
    if ints[0] == 169 and ints[1] == 254:  # link-local
        return True
    return False


def fqdn_ok(tok):
    """Could this token be a customer hostname (when not under a listed domain)?"""
    labels = tok.split(".")
    if labels[-1].lower() in FILE_EXTS:
        return False
    dom2 = ".".join(labels[-2:]).lower()
    if dom2 in PUBLIC_DOMAINS:
        return False
    low = tok.lower()
    if low.endswith(".cluster.local") or low.endswith(".svc"):
        return False
    if low.endswith(".in-addr.arpa"):
        return False
    if all(re.fullmatch(r"\d+", l) for l in labels):
        return False
    return True


def under_listed(fq, listed):
    """True if fq is exactly, or a subdomain of, a listed customer domain."""
    fl = fq.lower()
    return any(fl == d or fl.endswith("." + d) for d in listed)


def read_list_file(path):
    """One entry per line; blank lines and # comments (even indented) skipped."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def iter_files(inputs):
    """(path, rel) for every file under the inputs; .git dirs excluded."""
    for inp in inputs:
        p = Path(inp)
        if p.is_file():
            yield p, p.name
        elif p.is_dir():
            noted = set()
            for f in sorted(p.rglob("*")):
                relp = f.relative_to(p)
                vcs = [i for i, part in enumerate(relp.parts) if part in VCS_DIRS]
                if vcs:
                    top = Path(*relp.parts[:vcs[0] + 1]).as_posix()
                    if top not in noted:
                        noted.add(top)
                        print(f"SKIP {top}/: VCS metadata is never read or copied",
                              file=sys.stderr)
                    continue
                if f.is_file():
                    yield f, relp.as_posix()
        else:
            print(f"SKIP {inp}: not found", file=sys.stderr)


def line_of(text, start, end):
    s = text.rfind("\n", 0, start) + 1
    e = text.find("\n", end)
    return text[s:e if e != -1 else len(text)].strip()


def opaque_views(data):
    """Text views of bytes that are never rewritten: latin-1 (every byte
    maps to one char), plus a NUL-stripped view when NULs are present so
    ASCII-range UTF-16 becomes readable to the collectors and the re-scan."""
    view = data.decode("latin-1")
    return [view, view.replace("\x00", "")] if "\x00" in view else [view]


def is_inside(child, parent):
    return child == parent or parent in child.parents


# ---------------------------------------------------------------- clients

def client_tokens(name):
    """Words of a client name: split on non-alphanumerics and camel humps."""
    toks = []
    for part in re.findall(r"[^\W_]+", name):
        toks.extend(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", part).split())
    return toks


# Generic legal-form / corporate suffix words: never matched ALONE (every
# "Inc" or "Group" in a bundle is not the client), only as part of the name.
CORP_SUFFIXES = {"corp", "corporation", "inc", "incorporated", "llc", "llp",
                 "ltd", "limited", "co", "company", "group", "gmbh", "ag",
                 "sa", "sas", "bv", "nv", "plc", "pty", "oy", "ab", "as",
                 "holdings", "holding", "international", "intl"}

# Common English words: never matched ALONE either. A client named
# "The Group" / "One Logistics" / "New Venture" must not turn every "the",
# "one" or "new" in the prose into the stand-in; the full name still is.
LONE_STOPWORDS = {
    "the", "and", "for", "but", "nor", "not", "yet", "all", "any", "are",
    "was", "were", "has", "had", "have", "his", "her", "its", "our", "out",
    "you", "your", "they", "them", "their", "this", "that", "these",
    "those", "with", "from", "into", "onto", "over", "under", "about",
    "after", "before", "then", "than", "when", "where", "what", "which",
    "who", "why", "how", "can", "will", "may", "must", "shall", "should",
    "would", "could", "via", "per", "off", "own", "one", "two", "three",
    "four", "five", "six", "seven", "eight", "nine", "ten", "first",
    "second", "third", "last", "next", "new", "old", "big", "best", "top",
    "good", "great", "high", "low", "more", "most", "other", "some",
    "such", "each", "every", "only", "just", "also", "very", "here",
    "there", "now", "see", "use", "get", "set", "run", "make", "work",
    "home", "team", "data", "net", "web", "online", "general",
    "global", "national", "united", "american", "north", "south", "east",
    "west", "central", "city", "state", "service", "services",
    "system", "systems", "solutions", "technology", "technologies",
    "tech", "software", "network", "networks", "digital", "partners",
    "associates", "enterprises", "industries", "logistics", "consulting",
    "management", "capital", "health", "energy", "power", "media",
    "labs", "lab", "cloud", "open", "smart", "blue", "red", "green",
    "black", "white", "gold", "star", "sun", "day", "way"}


def lone_tokens(name):
    """First and last word of a multi-word name, each to be matched alone.

    "At Acme we build" and "Acme's datacenter" mention the client without
    its full name. Words shorter than 3 characters, generic corporate
    suffixes and common English words are not used alone.
    """
    toks = client_tokens(name)
    if len(toks) < 2:
        return []
    out = []
    for t in (toks[0], toks[-1]):
        low = t.lower()
        if len(t) >= 3 and low not in CORP_SUFFIXES and \
                low not in LONE_STOPWORDS and \
                low not in (o.lower() for o in out):
            out.append(t)
    return out


def client_source(names, lone=True):
    """One regex source matching every name in every separator variant,
    plus (lone=True) the first and last word of multi-word names alone.

    A word matched alone needs a boundary on both sides: never inside a
    larger word ("renew" is not "New"). The full name keeps matching
    anywhere -- a single-word client inside "globexdb01" is still the
    client (the leak-safe direction)."""
    srcs, seen = [], set()
    for name in names:
        toks = client_tokens(name)
        if not toks:
            continue
        pieces = [(len(toks), len(name), CLIENT_JOIN.join(re.escape(t) for t in toks))]
        if lone:
            pieces += [(1, len(t), r"(?<![A-Za-z0-9])" + re.escape(t)
                        + r"(?![A-Za-z0-9])") for t in lone_tokens(name)]
        for p in pieces:
            if p[2].lower() not in seen:
                seen.add(p[2].lower())
                srcs.append(p)
    srcs.sort(key=lambda s: (-s[0], -s[1], s[2]))
    return "|".join(s[2] for s in srcs)


def client_domains(skipped, client_src):
    """Unlisted domains that carry the client's own name.

    An FQDN-like token not under --domains whose label contains a client
    match (dbx.log.globex.com, client Globex) is the client's own domain:
    left to the client class it would be split-mangled into
    "dbx.log.<stand-in>.com" with its host label surviving. Its domain --
    from the label carrying the name, down to the TLD (globex.com) -- is
    treated as listed, so the hostname class claims the whole token.
    """
    if not client_src:
        return set()
    rx = re.compile(client_src, re.IGNORECASE)
    out = set()
    for low in skipped:
        labels = low.split(".")
        for i, lab in enumerate(labels[:-1]):
            if lab in DOMAIN_TLDS:
                continue
            if rx.search(lab):
                out.add(".".join(labels[i:]))
                break
    return out


# ---------------------------------------------------------------- networks

def mac_key(mac):
    """One device, one identity: lowercase, colon-separated."""
    return mac.lower().replace("-", ":")


def reverse_forward(tok):
    """Octets of a reverse name's address part, put back in forward order.

    '5.14.200.10' -> [10, 200, 14, 5] (a host); '14.200.10' -> [10, 200, 14]
    (a /24 zone). None if any octet is out of range.
    """
    try:
        parts = [int(p) for p in tok.split(".")]
    except ValueError:
        return None
    if len(parts) not in (3, 4) or not all(0 <= p <= 255 for p in parts):
        return None
    return parts[::-1]


def rev_skipped(fwd):
    """Reverse names for non-identifying space are left alone."""
    return skip_ip(fwd + [1] if len(fwd) == 3 else fwd)


def collect_networks(surfaces):
    """{(a,b,c): {canonical ips}}, plus the written forms of every real IP
    (forward) and every real reverse name's address part (for re-scan).

    A net that appears only in reverse-DNS form is still collected, so its
    reverse names map through the same net map as forward addresses.
    """
    nets, forms, rev_forms = {}, set(), set()
    for text in surfaces:
        for m in IP_RE.finditer(text):
            if INADDR_RE.match(text, m.end()):
                continue          # reverse names: handled below
            ints = ip_ints(m.group(0))
            if ints is None or skip_ip(ints):
                continue
            canon = ".".join(map(str, ints))
            nets.setdefault(tuple(ints[:3]), set()).add(canon)
            forms.add(m.group(0))
            forms.add(canon)
            # the same address written backwards is the same identifier
            rev_forms.add(".".join(map(str, ints[::-1])))
            rev_forms.add(".".join(map(str, ints[2::-1])))
        for m in REV_RE.finditer(text):
            fwd = reverse_forward(m.group(0))
            if fwd is None or rev_skipped(fwd):
                continue
            addrs = nets.setdefault(tuple(fwd[:3]), set())
            if len(fwd) == 4:
                addrs.add(".".join(map(str, fwd)))
            rev_forms.add(m.group(0))
            rev_forms.add(".".join(map(str, fwd[::-1])))
    return nets, forms, rev_forms


def assign_networks(nets, base, start):
    observed = set(nets)
    try:
        b = tuple(int(x) for x in base.split("."))
    except ValueError:
        b = ()
    if len(b) != 2 or not all(0 <= x <= 255 for x in b):
        raise SystemExit(f"ERROR: --net-base must be two octets, got {base!r}")
    mapping, i = {}, start
    for net in sorted(nets):
        while b + (i,) in observed:
            i += 1
        if i > 255:
            raise SystemExit("ERROR: ran out of /24s under --net-base")
        mapping[net] = f"{b[0]}.{b[1]}.{i}"
        i += 1
    return mapping


# ---------------------------------------------------------------- hostnames

def ssh_hosts(text):
    """Host tokens that are the target of an ssh/scp/mosh/ping command.

    Options and their values are skipped (a key file is never a host).
    ssh/mosh/ping: the destination is the first non-option argument (what
    follows it is a remote command). scp: only arguments shaped host:path
    or user@host[:path] are remote; a local source argument is never a host.
    """
    for m in SSH_VERB_RE.finditer(text):
        verb, rest = m.group(1), m.group(2)
        vopts = VALUE_OPTS[verb]
        toks = [t.strip("'\"()[]{},") for t in rest.split()]
        pos, i, opts_done = [], 0, False
        while i < len(toks):
            t = toks[i]
            if not opts_done and t == "--":
                opts_done = True
            elif not opts_done and t.startswith("--") and len(t) > 2:
                if "=" not in t and t in LONG_VALUE_OPTS:
                    i += 1
            elif not opts_done and t.startswith("-") and len(t) > 1:
                body = t[1:]
                for j, ch in enumerate(body):
                    if ch in vopts:
                        if j == len(body) - 1:
                            i += 1        # value is the next token
                        break             # value attached (-p2222) or consumed
            elif t:
                pos.append(t)
            i += 1
        if verb == "scp":
            cands = [t for t in pos if ":" in t or "@" in t]
        else:
            cands = pos[:1]
        for tok in cands:
            tok = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", tok)
            if "@" in tok:
                tok = tok.rsplit("@", 1)[1]
            tok = tok.split(":", 1)[0].strip("[]").rstrip(".")
            if HOST_TOKEN_RE.fullmatch(tok or "-"):
                yield tok


def collect_hostnames(texts, paths, extra_patterns, listed, opaque=()):
    """Kept FQDNs, bare hosts and skipped FQDN-like tokens, keyed lowercase.

    `opaque` (copy-through contents) feeds only the FQDN collectors: a listed
    domain's hosts are collected from it, but binary noise never becomes a
    bare host that would then rewrite ordinary words in the scrubbed text.
    """
    fqdns, bare, skipped = {}, {}, {}

    def keep(tok, record=True):
        tok = tok.strip(".")
        low = tok.lower()
        if "." not in low:
            return
        if under_listed(low, listed):
            # a listed domain beats a file-extension-looking last label
            fqdns.setdefault(low, tok)
            return
        # a listed domain buried mid-token (hn01.acme.com.prod, or a
        # filename hn01.acme.com.md): keep the longest listed prefix
        labels = low.split(".")
        for k in range(len(labels) - 1, 1, -1):
            pre = ".".join(labels[:k])
            if under_listed(pre, listed):
                fqdns.setdefault(pre, tok[:len(pre)])
                return
        if not record:
            return
        if fqdn_ok(tok):
            skipped.setdefault(low, tok)
            return
        # an unlisted FQDN with a file-extension tail (a filename such as
        # hub.acmewidgets.internal.md): still surface its domain in --scan
        stem = labels[:]
        while len(stem) > 2 and stem[-1] in ALL_EXTS:
            stem.pop()
        if len(stem) < len(labels):
            st = ".".join(stem)
            if re.fullmatch(r"[a-z]{2,63}", stem[-1]) and fqdn_ok(st):
                skipped.setdefault(st, tok[:len(st)])

    def add_bare(tok, ctx_ssh=False):
        low = tok.lower()
        if low in NEVER_HOSTS or (ctx_ssh and low in SSH_STOPWORDS):
            return
        if valid_ip(tok) or len(tok) < 2 or not re.search(r"[A-Za-z]", tok):
            return
        bare.setdefault(low, tok)

    def host_ctx(host, ctx_ssh=False):
        if valid_ip(host):
            return
        if "." in host:
            keep(host)
        else:
            add_bare(host, ctx_ssh)

    for text in list(texts) + list(paths):
        for m in FQDN_RE.finditer(text):
            keep(m.group(0))
        for m in USER_AT_RE.finditer(text):
            host_ctx(m.group(2))
        for pat in extra_patterns:
            for m in pat.finditer(text):
                tok = m.group(0)
                if "." in tok:
                    keep(tok)
                else:
                    add_bare(tok)
    for text in texts:
        for host in ssh_hosts(text):
            host_ctx(host, ctx_ssh=True)
    for text in opaque:
        for m in FQDN_RE.finditer(text):
            keep(m.group(0), record=False)
        for m in USER_AT_RE.finditer(text):
            if "." in m.group(2) and not valid_ip(m.group(2)):
                keep(m.group(2), record=False)
    for d in listed:            # a listed domain is always a domain
        fqdns.setdefault(d, d)
    return fqdns, bare, skipped


def assign_hostnames(fqdns, bare, prefix, domain, listed, derive_bare=True):
    """Map lowercase host keys -> anonymized names."""
    mapping = {}
    parents = set()
    for fq in fqdns:
        labels = fq.split(".")
        for i in range(1, len(labels)):
            p = ".".join(labels[i:])
            if p in fqdns:
                parents.add(p)
    # Listed domains, bare two-label domains and kept FQDNs that are the
    # parent of another kept FQDN are domains, not nodes: they collapse to
    # the generic domain. Distinct domains that collapse together are
    # visible in --scan output.
    hosts = []
    for fq in sorted(fqdns):
        if fq in listed or len(fq.split(".")) == 2 or fq in parents:
            mapping[fq] = domain
        else:
            hosts.append(fq)
    n = 0
    for fq in hosts:
        n += 1
        mapping[fq] = f"{prefix}-{n:03d}.{domain}"
    # Auto-derive bare hostnames from anonymized host FQDNs: a bare
    # `acl-bcm-hn01` (cmsh commands, prose) is the same host as
    # `acl-bcm-hn01.acl.eng.cdwbuilt.com` and must not survive. Tiny
    # labels are skipped to avoid turning ordinary words into nodes.
    derived = set()
    if derive_bare:
        for fq in hosts:
            label = fq.split(".")[0]
            if len(label) >= 3 and re.search(r"[a-z]", label):
                derived.add(label)
    # A bare occurrence of a host FQDN's first label maps to the same node
    # short name. Ambiguous labels (same first label on several domains)
    # get their own node-NNN.
    label_to_nodes = {}
    for fq in hosts:
        label_to_nodes.setdefault(fq.split(".")[0], set()).add(mapping[fq])
    for b in sorted(set(bare) | derived):
        nodes = label_to_nodes.get(b, set())
        if len(nodes) == 1:
            mapping[b] = next(iter(nodes)).split(".")[0]
        else:
            n += 1
            mapping[b] = f"{prefix}-{n:03d}"
    return mapping, derived


# ---------------------------------------------------------------- the passes

def _alt(vals):
    return "|".join(re.escape(v) for v in sorted(vals, key=lambda s: (-len(s), s)))


class Veil:
    """The combined replacement pass and the independent-value re-scan."""

    def __init__(self, hostmap, guidmap, macmap, ibnetmap, netmap, ip_forms,
                 rev_forms, client_src, replacement, mask_src):
        self.fq = {k: v for k, v in hostmap.items() if "." in k}
        self.bare = {k: v for k, v in hostmap.items() if "." not in k}
        self.guid, self.mac, self.ibnet = guidmap, macmap, ibnetmap
        self.netmap, self.replacement = netmap, replacement
        # replace: one alternation; at any position the first class wins
        groups = self._groups(
            client_src, r"\b(?:\d{1,3}\.){3}\d{1,3}\b(?!" + INADDR_TAIL + ")",
            REV_SRC)
        self.rx = re.compile("|".join(f"(?P<{n}>{s})" for n, s in groups),
                             re.IGNORECASE)
        # re-scan: the same classes, but IPs (forward and reversed) as the
        # literal real values seen
        scan = self._groups(
            None,
            (r"(?<![\d.])(?:" + _alt(ip_forms) + r")(?!\d|\.\d)") if ip_forms else None,
            (r"(?<![\d.])(?:" + _alt(rev_forms) + r")(?=" + INADDR_TAIL + ")")
            if rev_forms else None)
        self.scan_rx = re.compile("|".join(f"(?P<{n}>{s})" for n, s in scan),
                                  re.IGNORECASE) if scan else None
        # the client class is scanned on its own (see leaks())
        self.client_scan_rx = re.compile(client_src, re.IGNORECASE) if client_src else None
        self.mask_rx = re.compile(mask_src, re.IGNORECASE)

    def _groups(self, client_src, ip_src, rev_src=None):
        g = []
        if self.fq:
            g.append(("hostname", r"(?<![A-Za-z0-9])(?:" + _alt(self.fq) + r")(?![A-Za-z0-9])"))
        if self.bare:
            g.append(("bare_host", r"(?<![A-Za-z0-9_])(?:" + _alt(self.bare)
                      + r")(?![A-Za-z0-9_])" + BARE_TAIL))
        if self.guid:
            g.append(("ib_guid", r"(?<![0-9A-Za-z_:])(?:" + _alt(self.guid)
                      + r")(?![0-9A-Za-z_:])"))
        if self.mac:
            g.append(("mac_addr", MAC_PRE + r"(?:" + _alt(self.mac) + r")" + MAC_POST))
        if self.ibnet:
            g.append(("ib_net", r"\b(?:" + _alt(self.ibnet) + r")\b"))
        if rev_src:
            g.append(("reverse_ip", rev_src))
        if ip_src:
            g.append(("ip", ip_src))
        if client_src:
            g.append(("client", "(?:" + client_src + ")"))
        return g

    def _repl(self, m):
        k, v = m.lastgroup, m.group(0)
        if k == "hostname":
            return self.fq[v.lower()]
        if k == "bare_host":
            return self.bare[v.lower()]
        if k == "ib_guid":
            return self.guid[v.lower()]
        if k == "mac_addr":
            return self.mac[mac_key(v)]
        if k == "reverse_ip":
            # d.c.b.a.in-addr.arpa -> the anonymized a.b.c.d, reversed again,
            # so a reverse name stays paired with its forward address
            fwd = reverse_forward(v)
            if fwd is None or rev_skipped(fwd):
                return v
            new = self.netmap.get(tuple(fwd[:3]))
            if not new:
                return v
            anon = [int(x) for x in new.split(".")] + fwd[3:]
            return ".".join(map(str, anon[::-1]))
        if k == "ib_net":
            return self.ibnet[v.lower()]
        if k == "ip":
            ints = ip_ints(v)
            if ints is None or skip_ip(ints):
                return v
            new = self.netmap.get(tuple(ints[:3]))
            return f"{new}.{ints[3]}" if new else v
        if k == "client":
            return self.replacement
        return v

    def anonymize(self, text):
        return self.rx.sub(self._repl, text)

    def leaks(self, text):
        """(class, value, line) for every real value surviving in text.

        Generated vocabulary (the stand-in, node-NNN, the generic domain,
        fabric-net-NNN, <ib-guid-NNN>, <mac-addr-NNN>) is masked first so
        the tool's own output never false-positives.

        The client scan masks with WORD characters: the client joiner
        crosses any non-word run, so a NUL mask between two client words
        ("Acme <masked host> Corp") would manufacture a client match. The
        other classes keep the NUL mask (a word-char mask would glue a real
        value onto masked text and hide it behind a boundary).
        """
        out = []
        if self.scan_rx is not None:
            masked = self.mask_rx.sub(lambda m: "\x00" * len(m.group(0)), text)
            for m in self.scan_rx.finditer(masked):
                out.append((m.lastgroup.replace("_", " "), m.group(0),
                            line_of(text, m.start(), m.end())))
        if self.client_scan_rx is not None:
            masked = self.mask_rx.sub(lambda m: "x" * len(m.group(0)), text)
            for m in self.client_scan_rx.finditer(masked):
                out.append(("client", m.group(0), line_of(text, m.start(), m.end())))
        return out


# ---------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(
        description="Anonymize an Obsidian markdown bundle (hostnames, "
                    "IB/MAC IDs, networks, client names).")
    ap.add_argument("inputs", nargs="+", help="files or directories to process")
    ap.add_argument("--client", action="append", default=[],
                    help="client/company name to replace (repeatable; "
                         "comma-delimited lists also accepted, e.g. "
                         "'Acme, Globex'. Names containing a literal comma "
                         "should go in --client-file instead). Required for --out.")
    ap.add_argument("--client-file",
                    help="file with one client name per line (avoids shell history)")
    ap.add_argument("--replacement", default="Wayland Megacorp",
                    help="static stand-in for client names")
    ap.add_argument("--net-base", default="10.10",
                    help="first two octets of anonymized ranges")
    ap.add_argument("--net-start", type=int, default=11,
                    help="third octet to start anonymized ranges at")
    ap.add_argument("--host-pattern", action="append", default=[],
                    help="extra regex for bare hostnames, e.g. 'bcm-[a-z0-9-]+' (repeatable)")
    ap.add_argument("--no-derive-bare", action="store_true",
                    help="don't auto-derive bare hostnames from anonymized "
                         "FQDNs (on by default: a bare acl-bcm-hn01 is the "
                         "same host as acl-bcm-hn01.acl.eng.cdwbuilt.com)")
    ap.add_argument("--host-prefix", default="node",
                    help="generic hostname stem")
    ap.add_argument("--domain", default="example.internal",
                    help="generic domain for anonymized FQDNs")
    ap.add_argument("--domains", action="append", default=[],
                    help="customer domains to anonymize (repeatable; "
                         "comma-delimited also accepted, e.g. "
                         "'bhicorp.com, promega.com'). ONLY FQDNs under "
                         "these domains are touched -- Kubernetes field "
                         "paths, Helm values, cluster.local and everything "
                         "else is left alone by design.")
    ap.add_argument("--domain-file",
                    help="file with one domain per line (# comments allowed)")
    ap.add_argument("--out", "-o", help="output directory (must not be, or be "
                                        "inside, an input)")
    ap.add_argument("--map", help="mapping file path (default: "
                                  "<parent-of-out>/anonymize-map.json -- next "
                                  "to the output, never inside it)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print unified diffs, write nothing")
    ap.add_argument("--scan", action="store_true",
                    help="report what would be anonymized, change nothing")
    return ap


def err(msg):
    print(f"ERROR: {msg}", file=sys.stderr)


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    writing = bool(args.out) and not args.dry_run and not args.scan
    if not args.scan and not args.dry_run and not args.out:
        ap.error("need --out or --dry-run/--scan")

    # ---- client names (repeatable --client, comma-delimited accepted)
    clients = []
    for c in args.client:
        clients.extend(s.strip() for s in c.split(",") if s.strip())
    if args.client_file:
        clients += read_list_file(args.client_file)
    seen_c, uniq = set(), []
    for c in clients:
        if c.casefold() not in seen_c and client_tokens(c):
            seen_c.add(c.casefold())
            uniq.append(c)
        elif not client_tokens(c):
            print(f"WARNING: client name {c!r} has no letters or digits; ignored",
                  file=sys.stderr)
    clients = uniq
    client_src = client_source(clients)

    # ---- customer domains (explicit list; only these are touched)
    doms = []
    for d in args.domains:
        doms.extend(s.strip().lower().strip(".") for s in d.split(",") if s.strip())
    if args.domain_file:
        doms += [l.lower().strip(".") for l in read_list_file(args.domain_file)]
    doms = sorted(set(d for d in doms if d))

    # ---- guards (before anything is read or written)
    vocab = [("the replacement", args.replacement),
             ("the host prefix", args.host_prefix),
             ("the generic domain", args.domain),
             ("the fabric name stem", "fabric-net"),
             ("the IB GUID stem", "ib-guid"),
             ("the MAC stem", "mac-addr"),
             ("the net base", args.net_base)]
    bad = []
    for name in clients:
        rx = re.compile(client_source([name], lone=False), re.IGNORECASE)
        for what, word in vocab:
            if rx.search(word):
                bad.append(f"client name {name!r} matches inside {what} {word!r}")
        # the first/last word is also matched alone: it must not collide either
        for tok in lone_tokens(name):
            trx = re.compile(re.escape(tok), re.IGNORECASE)
            for what, word in vocab:
                if trx.search(word):
                    bad.append(f"client name {name!r}: its word {tok!r} (matched "
                               f"alone) matches inside {what} {word!r}")
    if bad:
        for b in bad:
            err(b)
        err("a client name that collides with the generated vocabulary would "
            "scrub the tool's own output. Use a fuller client name, or change "
            "--replacement/--host-prefix/--domain.")
        return 2
    if not clients:
        if writing:
            err("no client names given (--client / --client-file). An --out "
                "run without client names would publish them unscrubbed.")
            return 2
        print("WARNING: no client names given -- the client pass is disabled "
              "(an --out run will refuse).", file=sys.stderr)

    outdir = Path(args.out) if args.out else None
    in_paths = [Path(i).resolve() for i in args.inputs]
    if writing:
        out_res = outdir.resolve()
        for inp, ip in zip(args.inputs, in_paths):
            if is_inside(out_res, ip):
                err(f"--out {args.out!r} is, or is inside, the input {inp!r}. "
                    f"Nothing is ever edited in place: pick an output "
                    f"directory outside every input.")
                return 2
    mappath = None
    if writing:
        mappath = Path(args.map) if args.map else out_res.parent / "anonymize-map.json"
        if is_inside(mappath.resolve(), out_res):
            err(f"--map {str(mappath)!r} is inside --out: the map contains the "
                f"real values and must never ship with the bundle.")
            return 2

    # ---- input guard: never read through a symlink (issue #9). A linked
    # input -- named directly, or anywhere inside a named directory --
    # would pull content from outside the bundle into the anonymized
    # output. The output side has refused symlinks since W6; the input
    # side refuses them too, before anything is read. VCS internals are
    # exempt: iter_files never reads them.
    for inp in args.inputs:
        p = Path(inp)
        if p.is_symlink():
            err(f"input {inp!r} is a symlink (-> {os.path.realpath(p)!r}); "
                f"refusing to read through it.")
            return 2
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                relp = f.relative_to(p)
                vcs = [i for i, part in enumerate(relp.parts)
                       if part in VCS_DIRS]
                upto = vcs[0] if vcs else len(relp.parts)
                for i in range(1, upto + 1):
                    q = p.joinpath(*relp.parts[:i])
                    if q.is_symlink():
                        err(f"input {str(q)!r} is a symlink "
                            f"(-> {os.path.realpath(q)!r}); refusing to "
                            f"read through it.")
                        return 2

    # ---- gather inputs: text files are scrubbed, everything else copied
    texts, copies = {}, {}
    for p, rel in iter_files(args.inputs):
        if rel in texts or rel in copies:
            err(f"two inputs both provide {rel!r}; process them separately.")
            return 1
        if p.suffix.lower() in SKIP_SUFFIXES:
            copies[rel] = (p, "binary suffix")
            continue
        try:
            data = p.read_bytes()
        except OSError as e:
            err(f"cannot read {rel}: {e}")
            return 1
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            copies[rel] = (p, "not UTF-8")
            continue
        if "\x00" in text:
            # valid UTF-8, but NUL bytes mean UTF-16 without a BOM (or
            # binary): every token is NUL-split, so it can't be scrubbed
            copies[rel] = (p, "contains NUL bytes (UTF-16 or binary)")
            continue
        texts[rel] = text
    if not texts and not copies:
        print("No files found.", file=sys.stderr)
        return 1
    paths = sorted(list(texts) + list(copies))
    # Copy-through contents are opaque (never rewritten) but are still
    # detection surface: an identifier that appears only there must be
    # collected, so the re-scan flags the file that carries it.
    opaque = [v for rel in sorted(copies)
              for v in opaque_views(copies[rel][0].read_bytes())]
    surfaces = list(texts.values()) + paths + opaque

    # ---- detect: every collector runs over contents AND relative paths
    nets, ip_forms, rev_forms = collect_networks(surfaces)
    netmap = assign_networks(nets, args.net_base, args.net_start)

    guids, macs, ibnets = {}, {}, set()
    for t in surfaces:
        for m in GUID_HEX_RE.finditer(t):
            guids.setdefault(m.group(0).lower(), m.group(0))
        for m in GID_RE.finditer(t):
            guids.setdefault(m.group(0).lower(), m.group(0))
        for m in MAC_RE.finditer(t):
            macs.setdefault(mac_key(m.group(0)), m.group(0))
        ibnets.update(m.lower() for m in IBNET_RE.findall(t)
                      if m.lower() not in IBNET_TOOLS)
    guidmap = {g: f"<ib-guid-{i:03d}>" for i, g in enumerate(sorted(guids), 1)}
    macmap = {g: f"<mac-addr-{i:03d}>" for i, g in enumerate(sorted(macs), 1)}
    ibnetmap = {n: f"fabric-net-{i:03d}" for i, n in enumerate(sorted(ibnets), 1)}
    ib_ifaces = sum(len(IB_IFACE_RE.findall(t)) for t in texts.values())

    extra = [re.compile(p) for p in args.host_pattern]
    fqdns, bare, skipped = collect_hostnames(texts.values(), paths, extra, doms,
                                             opaque=opaque)
    auto_doms = client_domains(skipped, client_src) - set(doms)
    if auto_doms:
        doms = sorted(set(doms) | auto_doms)
        fqdns, bare, skipped = collect_hostnames(texts.values(), paths, extra,
                                                 doms, opaque=opaque)
    hostmap, derived_bare = assign_hostnames(
        fqdns, bare, args.host_prefix, args.domain, set(doms),
        derive_bare=not args.no_derive_bare)

    mask_src = "|".join([re.escape(args.replacement), re.escape(args.domain),
                         re.escape(args.host_prefix) + r"-\d{3,}",
                         r"fabric-net-\d{3,}", r"<ib-guid-\d{3,}>",
                         r"<mac-addr-\d{3,}>"])
    # every MAC is matched (and re-scanned) in both separator forms
    mac_forms = {}
    for k, ph in macmap.items():
        mac_forms[k] = ph
        mac_forms[k.replace(":", "-")] = ph
    veil = Veil(hostmap, guidmap, mac_forms, ibnetmap, netmap, ip_forms,
                rev_forms, client_src, args.replacement, mask_src)

    # ---- plan: anonymize everything in memory first
    renames = {}
    for rel in paths:
        new_rel = veil.anonymize(rel)
        if new_rel != rel:
            renames[rel] = new_rel
    plan = []        # (kind, rel, new_rel, new_bytes or source path)
    for rel in sorted(texts):
        plan.append(("text", rel, renames.get(rel, rel),
                     veil.anonymize(texts[rel]).encode("utf-8")))
    for rel in sorted(copies):
        plan.append(("copy", rel, renames.get(rel, rel), copies[rel][0]))

    collisions, seen = [], {}
    for kind, rel, new_rel, _ in plan:
        key = new_rel.casefold()   # case-insensitive filesystems collide too
        if key in seen:
            collisions.append(f"filename collision after anonymizing: "
                              f"{seen[key]!r} and {rel!r} both become {new_rel!r}. "
                              f"Rename one source file and re-run.")
        else:
            seen[key] = rel

    def leak_lines(where, text):
        return [(where, cls, val, line) for cls, val, line in veil.leaks(text)]

    def dedupe(found):
        seen_l, out = set(), []
        for f in found:
            key = (f[0], f[1], f[2].replace("\x00", "").lower())
            if key not in seen_l:
                seen_l.add(key)
                out.append(f)
        return out

    def plan_leaks():
        found = []
        for kind, rel, new_rel, payload in plan:
            found += leak_lines(f"{new_rel} (filename)", new_rel)
            if kind == "text":
                found += leak_lines(new_rel, payload.decode("utf-8"))
            else:
                for view in opaque_views(Path(payload).read_bytes()):
                    found += leak_lines(f"{new_rel} (copied without scrubbing)", view)
        return dedupe(found)

    def report_leaks(found):
        for where, cls, val, line in found:
            print(f"LEAK {where}: {cls} {val!r} survives: {line[:200]!r}",
                  file=sys.stderr)

    if args.scan:
        rc = 0
        print("== client names ==")
        for c in clients:
            rx = re.compile(client_source([c]), re.IGNORECASE)
            n = sum(len(rx.findall(t)) for t in surfaces)
            print(f"  {n:5d}  {c}  (any separator/line-break variant; contents + filenames)")
        print("== networks ==")
        for old, new in sorted(netmap.items()):
            print(f"  {'.'.join(map(str, old))}.0/24 -> {new}.0/24 ({len(nets[old])} addrs)")
        print("== infiniband ==")
        for g, r in sorted(guidmap.items(), key=lambda kv: kv[1]):
            print(f"  {r}  <- {guids[g]}")
        for n_, r in sorted(ibnetmap.items(), key=lambda kv: kv[1]):
            print(f"  {r}  <- {n_}")
        print(f"  ibN interface refs left as-is: {ib_ifaces}")
        print("== mac addresses ==")
        for g, r in sorted(macmap.items(), key=lambda kv: kv[1]):
            print(f"  {r}  <- {macs[g]}")
        print("== hostnames ==")
        for old, new in sorted(hostmap.items(), key=lambda kv: (kv[1], kv[0])):
            tag = " [derived bare]" if old in derived_bare else ""
            print(f"  {new}  <- {old}{tag}")
        if doms:
            print("== domains ==")
            for d in doms:
                tag = "  (auto: carries a client name)" if d in auto_doms else ""
                print(f"  {args.domain}  <- {d}{tag}")
        print(f"== FQDN-like tokens NOT under --domains ({len(skipped)}, left alone) ==")
        bydom = {}
        for low, tok in skipped.items():
            bydom.setdefault(".".join(low.split(".")[-2:]), []).append(tok)
        if bydom:
            print("  Check that none of these is a client domain you forgot to list:")
            for d in sorted(bydom):
                ex = sorted(bydom[d])
                print(f"  {len(ex):5d}  {d}  (e.g. {ex[0]})")
        else:
            print("  (none)")
        print("== filenames ==")
        if renames:
            for old, new in sorted(renames.items()):
                print(f"  {new}  <- {old}")
        else:
            print("  (none)")
        print("== copied WITHOUT scrubbing (scrub these by hand) ==")
        if copies:
            for rel in sorted(copies):
                print(f"  {renames.get(rel, rel)}  ({copies[rel][1]})")
        else:
            print("  (none)")
        for c in collisions:
            err(c)
            rc = 1
        found = plan_leaks()
        report_leaks(found)
        if found and rc == 0:
            rc = 3
        return rc

    if collisions:
        for c in collisions:
            err(c)
        return 1

    # ---- write guards: never edit an input, never clobber differing output
    if writing:
        in_files = {p for p in in_paths if p.is_file()}
        for p, rel in iter_files(args.inputs):
            in_files.add(p.resolve())
        clobber = []
        for kind, rel, new_rel, payload in plan:
            dest = outdir / new_rel
            # Symlinks: no component below --out may be a link (a linked
            # file or directory would bless/write a target the operator
            # never chose), and the fully resolved destination must sit
            # inside the resolved --out. --out itself may be a link.
            p = dest
            while p != outdir:
                if p.is_symlink():
                    err(f"{str(p)!r} is a symlink (-> {os.path.realpath(p)!r}); "
                        f"refusing to write {new_rel!r} through it.")
                    return 2
                p = p.parent
            real = Path(os.path.realpath(dest))
            if not is_inside(real, out_res):
                err(f"writing {new_rel!r} would land outside --out "
                    f"({str(real)!r}); refusing.")
                return 2
            if real in in_files:
                err(f"writing {new_rel!r} would overwrite an input file "
                    f"({rel!r}). Nothing is ever edited in place.")
                return 2
            if real.exists():
                want = payload if kind == "text" else Path(payload).read_bytes()
                if not real.is_file() or real.read_bytes() != want:
                    clobber.append(new_rel)
        if clobber:
            for c in clobber:
                err(f"{str(outdir / c)!r} already exists with different content.")
            err("refusing to overwrite pre-existing output; remove it or pick "
                "a fresh --out.")
            return 2

    # ---- the mapping (PRIVATE -- contains real values)
    mapdoc = json.dumps({
        "client_names": {c: args.replacement for c in clients},
        "client_words_matched_alone": {c: lone_tokens(c) for c in clients
                                       if lone_tokens(c)},
        "networks": {f"{'.'.join(map(str, k))}.0/24": f"{v}.0/24"
                     for k, v in sorted(netmap.items())},
        "hostnames": dict(sorted(hostmap.items(), key=lambda kv: (kv[1], kv[0]))),
        "ib_guids": {guids[k]: v for k, v in sorted(guidmap.items(), key=lambda kv: kv[1])},
        "mac_addrs": dict(sorted(macmap.items(), key=lambda kv: kv[1])),
        "ib_nets": dict(sorted(ibnetmap.items(), key=lambda kv: kv[1])),
        "renamed_files": dict(sorted(renames.items())),
        "copied_without_scrubbing": {renames.get(r, r): copies[r][1] for r in sorted(copies)},
    }, indent=2) + "\n"
    if writing and not args.map and mappath.exists() \
            and mappath.read_text(encoding="utf-8", errors="replace") != mapdoc:
        err(f"{str(mappath)!r} already exists and holds a different mapping "
            f"(another run's key?). Move it aside or pass --map.")
        return 2

    # ---- dry-run: show it; out: write it
    if args.dry_run:
        for kind, rel, new_rel, payload in plan:
            if kind == "text":
                new = payload.decode("utf-8")
                if new != texts[rel] or new_rel != rel:
                    print("\n".join(difflib.unified_diff(
                        texts[rel].splitlines(), new.splitlines(),
                        fromfile=f"a/{rel}", tofile=f"b/{new_rel}", lineterm="")))
            else:
                print(f"COPY WITHOUT SCRUBBING ({copies[rel][1]}): a/{rel} -> b/{new_rel}")
        found = plan_leaks()
    else:
        written = set()
        for kind, rel, new_rel, payload in plan:
            dest = outdir / new_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if kind == "text":
                dest.write_bytes(payload)
            else:
                shutil.copyfile(payload, dest)
            written.add(new_rel)
        # The map holds the real values: never follow a symlink
        # into truncating its target, and never leave the file
        # group/other-readable. A symlinked or loose map path is
        # refused outright; the write goes through a descriptor
        # created 0600 with the mode re-asserted on the fd, so the
        # process umask cannot widen it.
        if os.path.islink(mappath):
            err(f"map path {str(mappath)!r} is a symlink — refusing to "
                f"follow it; the map contains the real values")
            return 2
        if mappath.exists():
            mmode = mappath.stat().st_mode
            if not stat.S_ISREG(mmode):
                err(f"map path {str(mappath)!r} is not a regular file "
                    f"— refusing")
                return 2
            if mmode & 0o077:
                err(f"map {str(mappath)!r} is group/other-accessible "
                    f"({stat.S_IMODE(mmode):04o}) — chmod 0600 or remove "
                    f"it; the map contains the real values")
                return 2
        mappath.parent.mkdir(parents=True, exist_ok=True)
        mfd = os.open(mappath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                      | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(mfd, 0o600)
            with os.fdopen(mfd, "w", encoding="utf-8") as mfh:
                mfh.write(mapdoc)
        except BaseException:
            try:
                os.close(mfd)
            except OSError:
                pass
            raise
        print(f"\nWROTE {mappath.resolve()} -- PRIVATE, contains real values. "
              f"Do not commit.", file=sys.stderr)
        # re-scan what is actually on disk in --out (all of it)
        found = []
        copied_out = {renames.get(r, r) for r in copies}
        for f in sorted(outdir.rglob("*")):
            relp = f.relative_to(outdir)
            if any(part in VCS_DIRS for part in relp.parts) or not f.is_file():
                continue
            rel = relp.as_posix()
            tag = "" if rel in written else " (pre-existing, not written by this run)"
            data = f.read_bytes()
            try:
                content, ctag = data.decode("utf-8"), ""
                views = [content] + ([content.replace("\x00", "")]
                                     if "\x00" in content else [])
            except UnicodeDecodeError:
                views, ctag = opaque_views(data), " (binary)"
            found += leak_lines(f"{rel} (filename){tag}", rel)
            if rel in copied_out:
                ctag = " (copied without scrubbing)"
            for view in views:
                found += leak_lines(f"{rel}{ctag}{tag}", view)
        found = dedupe(found)
    report_leaks(found)

    if copies:
        print("\nCopied WITHOUT scrubbing (content untouched -- scrub these "
              "by hand before publishing):", file=sys.stderr)
        for rel in sorted(copies):
            print(f"  {renames.get(rel, rel)}  ({copies[rel][1]})", file=sys.stderr)

    print(f"\nSummary: {len(clients)} client names, "
          f"{len(netmap)} networks, {len(hostmap)} hostnames "
          f"({sum(1 for k in hostmap if '.' in k)} FQDN, "
          f"{sum(1 for k in hostmap if '.' not in k)} bare), "
          f"{len(guidmap)} IB GUIDs, {len(macmap)} MAC addresses, "
          f"{len(ibnetmap)} ibnet names, "
          f"{ib_ifaces} ibN refs left as-is, "
          f"{len(copies)} files copied WITHOUT scrubbing, "
          f"{len(found)} leaks surviving, "
          f"{len(skipped)} FQDN-like tokens left alone (not under --domains).",
          file=sys.stderr)
    if found:
        print(f"FAIL: {len(found)} real value(s) survive in the output -- "
              f"see the LEAK lines above. Do not publish.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
