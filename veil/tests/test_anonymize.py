"""End-to-end suite for anonymize.py (stdlib unittest, no dependencies).

Run from the repo root:

    python3 -m unittest discover -s tests -v

Every test builds its fixture bundle in a fresh temp directory and runs the
CLI as a subprocess, exactly as an operator would. VEIL_SCRIPT points the
suite at another copy of the script (used to record before/after results).

Test names carry the fix (v1..v12) and the review finding they encode:
glm-* = REVIEW-VEIL-glm.md, qwen-* = REVIEW-VEIL-qwen.md, pin-* = a
verified-sound behavior that must not regress.
"""

import hashlib
import json
import os
import random
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = Path(os.environ.get("VEIL_SCRIPT", REPO / "anonymize.py"))

ACME = ["--client", "Acme Widgets", "--domains", "acme.com"]


class VeilCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="veil-test-")
        self.tmp = Path(self._td.name)
        self.inp = self.tmp / "in"
        self.out = self.tmp / "out"
        self.inp.mkdir()

    def tearDown(self):
        self._td.cleanup()

    # -- fixtures
    def bundle(self, files, root=None):
        root = root or self.inp
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                p.write_bytes(content)
            else:
                p.write_text(content, encoding="utf-8")

    def veil(self, *args, inputs=None):
        cmd = [sys.executable, str(SCRIPT), *args,
               *(inputs if inputs is not None else [str(self.inp)])]
        return subprocess.run(cmd, capture_output=True, text=True,
                              cwd=str(self.tmp), timeout=120)

    def write(self, *args, inputs=None):
        return self.veil(*args, "--out", str(self.out), inputs=inputs)

    # -- inspection
    def outfiles(self, root=None):
        root = root or self.out
        if not root.exists():
            return {}
        return {f.relative_to(root).as_posix(): f.read_bytes()
                for f in sorted(root.rglob("*")) if f.is_file()}

    def published(self, root=None):
        """Everything that would cross the boundary: names + contents.

        A top-level anonymize-map.json is excluded so a fixture's verdict is
        about the fixture's identifier; the map's own placement is pinned
        separately (V8 tests use outfiles(), which never excludes it).
        """
        parts = []
        for rel, data in self.outfiles(root).items():
            if rel == "anonymize-map.json":
                continue
            parts.append(rel)
            parts.append(data.decode("utf-8", errors="replace"))
        return "\n".join(parts)

    def mapdoc(self, path=None):
        return json.loads((path or self.tmp / "anonymize-map.json").read_text())

    def tree_hash(self, root):
        h = hashlib.sha256()
        for f in sorted(root.rglob("*")):
            h.update(f.relative_to(root).as_posix().encode())
            if f.is_file():
                h.update(f.read_bytes())
        return h.hexdigest()

    def assertGone(self, needle, haystack):
        self.assertNotIn(needle.lower(), haystack.lower(),
                         f"{needle!r} survived into the output")

    def assertRc(self, r, rc):
        self.assertEqual(r.returncode, rc,
                         f"exit {r.returncode}, wanted {rc}\nstdout:\n{r.stdout}\n"
                         f"stderr:\n{r.stderr}")


# ===================================================================== V1

class V1FilenamesAreDetectionSurface(VeilCase):
    def test_v1_glm_h1_fqdn_only_in_filename(self):
        self.bundle({"overview.md": "bundle overview, no FQDNs inside\n",
                     "topology-acl-bcm-hn01.acmewidgets.internal.md": "topology\n"})
        r = self.write("--client", "Globex Corp", "--domains", "acmewidgets.internal")
        self.assertRc(r, 0)
        pub = self.published()
        self.assertGone("acl-bcm-hn01", pub)
        self.assertGone("acmewidgets", pub)

    def test_v1_glm_f3_case_variant_filename_host_label(self):
        self.bundle({"changelog-ACL-BCM-HN01.ACMEWIDGETS.INTERNAL.md": "log\n"})
        r = self.write("--client", "Acme Widgets", "--domains", "acmewidgets.internal")
        self.assertRc(r, 0)
        self.assertGone("hn01", self.published())
        self.assertGone("acmewidgets", self.published())

    def test_v1_glm_h1_bare_filename_case_variant(self):
        self.bundle({"notes.md": "ssh acl-bcm-hn01\n",
                     "ACL-BCM-HN01.md": "host page\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertGone("acl-bcm-hn01", self.published())

    def test_v1_qwen_f08_f16_case_variant_domain_filenames_abort(self):
        self.bundle({"acme.com.md": "first\n", "ACME.COM.md": "second\n"})
        r = self.write(*ACME)
        self.assertRc(r, 1)
        self.assertIn("collision", r.stderr)
        self.assertEqual(self.outfiles(), {})
        self.assertFalse((self.tmp / "anonymize-map.json").exists())

    def test_v1_qwen_f08_listed_domain_only_in_filename(self):
        self.bundle({"ACME.COM.md": "second\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(list(self.outfiles()), ["example.internal.md"])

    def test_v1_qwen_f09_ip_only_in_filename(self):
        self.bundle({"10.200.14.5.md": "a\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(list(self.outfiles()), ["10.10.11.5.md"])

    def test_v1_qwen_f09_leading_zero_ip_only_in_filename(self):
        self.bundle({"10.200.14.05.md": "a\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(list(self.outfiles()), ["10.10.11.5.md"])

    def test_v1_qwen_f12_fqdn_in_one_filename_only(self):
        self.bundle({"hn02.acme.com.md": "x\n", "other.md": "see hn01.acme.com\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        pub = self.published()
        self.assertGone("hn02", pub)
        self.assertGone("hn01", pub)

    def test_v1_qwen_f3d_guid_only_in_filename(self):
        self.bundle({"0x0011223344556677.md": "x\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertGone("0011223344556677", self.published())

    def test_v1_glm_l1_qwen_l8_case_variant_fqdn_is_one_node(self):
        self.bundle({"a.md": "hn01.acme.com HN01.ACME.COM Hn01.Acme.Com\nssh hn01\nssh HN01\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertEqual(out.count("node-001.example.internal"), 3)
        self.assertEqual(out.count("ssh node-001\n"), 2)
        hosts = self.mapdoc()["hostnames"]
        self.assertEqual(sorted(hosts), ["acme.com", "hn01", "hn01.acme.com"])


# ===================================================================== V2

class V2ClientMatching(VeilCase):
    def test_v2_glm_h2_qwen_f07_newline_split_name(self):
        self.bundle({"a.md": "Acme\nWidgets is our client.\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertEqual(out, "Wayland Megacorp is our client.\n")

    def test_v2_qwen_f19_tab_and_double_space(self):
        self.bundle({"a.md": "Acme\tWidgets\nAcme  Widgets double space\nAcme\n   Widgets\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertGone("acme", out)
        self.assertGone("widgets", out)

    def test_v2_glm_h3_hyphen_underscore_compact_variants(self):
        self.bundle({"a.md": "repo: https://github.com/acme-corp/infra\n"
                             "mirror: git@github.com:acme-corp/infra.git\n"
                             "site: https://www.acme-corp.com/docs\n"
                             "bare: acme-corp acme_corp AcmeCorp ACME CORP\n"})
        r = self.write("--client", "Acme Corp")
        self.assertRc(r, 0)
        self.assertGone("acme", self.published())

    def test_v2_qwen_f25_loose_punctuation_variant(self):
        self.bundle({"a.md": "Globex, Inc. visited us. Also globex inc prose.\n"})
        cf = self.tmp / "clients.txt"
        cf.write_text("Globex, Inc.\n")
        r = self.write("--client-file", str(cf))
        self.assertRc(r, 0)
        self.assertGone("globex", self.published())

    def test_v2_camel_case_client_name_splits(self):
        self.bundle({"a.md": "GlobexCorp, Globex Corp, globex-corp\n"})
        r = self.write("--client", "GlobexCorp")
        self.assertRc(r, 0)
        self.assertGone("globex", self.published())

    def test_v2_glm_m2_one_combined_pass_never_nests(self):
        # The original applied names one after another, longest first, so
        # "Acme Widgets Group" -> "Wayland Megacorp", and then the shorter
        # "Corp Inc" matched across that stand-in and the next word:
        # "Wayland MegaWayland Megacorp". (GLM's own fixture used "Corp"/"Mega",
        # which V6c now refuses outright -- see the v6c test; round 2's
        # lone-word guard also refuses "Megacorp Inc", so the fixture uses
        # "Corp Inc": both words are generic suffixes, never matched alone.)
        self.bundle({"a.md": "Acme Widgets Group Inc. and Corp Inc. and Globex.\n"})
        r = self.write("--client", "Acme Widgets Group", "--client", "Corp Inc",
                       "--client", "Globex")
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertEqual(out, "Wayland Megacorp Inc. and Wayland Megacorp. and "
                              "Wayland Megacorp.\n")
        self.assertNotIn("LEAK", r.stderr)

    def test_v2_client_name_in_filename(self):
        self.bundle({"Acme-Widgets-notes.md": "x\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(list(self.outfiles()), ["Wayland Megacorp-notes.md"])

    def test_v2_rescan_uses_same_matcher_split_name_in_copy(self):
        self.bundle({"ok.md": "fine\n", "dump.dat": b"\xff Acme\n\tWidgets\n"})
        r = self.write(*ACME)
        self.assertRc(r, 3)
        self.assertRegex(r.stderr, r"LEAK dump\.dat.*client")

    def test_v2_qwen_f29_fuzz_split_names_never_survive(self):
        seps = [" ", "\n", "\t", "  ", "-", "_", "", "\n  ", " \t", "\r\n", ". "]
        words = "the cluster was rebooted by ops after the storage upgrade".split()
        rng = random.Random(20261117)
        oracle = re.compile(r"acme[\W_]*widgets", re.IGNORECASE)
        for i in range(40):
            with self.subTest(seed=i):
                inp = self.tmp / f"fz{i}" / "in"     # own parent: own default map
                out = self.tmp / f"fz{i}" / "out"
                files = {}
                for j in range(3):
                    toks = [rng.choice(words) for _ in range(30)]
                    for _ in range(4):
                        a = "".join(c.upper() if rng.random() < .3 else c for c in "acme")
                        b = "".join(c.upper() if rng.random() < .3 else c for c in "widgets")
                        toks.insert(rng.randrange(len(toks)), a + rng.choice(seps) + b)
                    files[f"note{j}.md"] = " ".join(toks) + "\n"
                files[f"Acme{rng.choice(['-', '_', ''])}Widgets-{i}.md"] = "x\n"
                self.bundle(files, root=inp)
                r = self.veil("--client", "Acme Widgets", "--out", str(out),
                              inputs=[str(inp)])
                self.assertRc(r, 0)
                self.assertIsNone(oracle.search(self.published(out)))
                self.assertIn(" 0 leaks surviving", r.stderr)


# ===================================================================== V3

class V3UnderscoreLabels(VeilCase):
    def test_v3_glm_h4_qwen_f02_underscore_hosts(self):
        self.bundle({"a.md": "_ldap._tcp.acl_bcm_hn01.acme.com\n"
                             "ssh acl_bcm_hn01.acme.com\n"
                             "alice@a_b_hn01.acme.com\n"
                             "nodes:\n  - acl_bcm_hn01.acme.com\n"
                             "  - ssh admin@acl_bcm_hn01\n"
                             "  - acl_bcm_hn01 runs cmsh\n"})
        r = self.write("--client", "Globex", "--domains", "acme.com")
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertGone("hn01", out)
        self.assertGone("acme", out)
        self.assertIn("_ldap._tcp.node-", out)   # SRV prefix kept


# ===================================================================== V4

class V4SshScpTargets(VeilCase):
    def test_v4_glm_f16_scp_bare_source_not_a_host(self):
        self.bundle({"a.md": "scp backup acl-bcm-hn04:~/tmp/\nkeep the backup safe\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertGone("acl-bcm-hn04", out)
        self.assertIn("scp backup ", out)
        self.assertIn("keep the backup safe", out)
        self.assertNotIn("backup", self.mapdoc()["hostnames"])

    def test_v4_glm_f15_scp_source_with_extension(self):
        self.bundle({"a.md": "scp backup.tgz acl-bcm-hn04:~/tmp/\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertGone("acl-bcm-hn04", out)
        self.assertIn("scp backup.tgz node-", out)

    def test_v4_qwen_f28_option_values_never_hosts(self):
        self.bundle({"a.md": "ssh -i id_rsa hn01\nssh root@hn02\n"
                             "scp x.tar.gz hn03:/tmp/\nping hn04\n"
                             "ssh hn05.acme.com\nssh -o StrictHostKeyChecking=no -p 2222 hn06 uptime\n"
                             "scp -P 2222 -i key.pem notes.txt admin@hn07:/srv/\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        for h in ("hn01", "hn02", "hn03", "hn04", "hn05", "hn06", "hn07"):
            self.assertGone(h, out)
        self.assertIn("ssh -i id_rsa node-", out)
        self.assertIn("uptime", out)
        hosts = self.mapdoc()["hostnames"]
        for junk in ("id", "id_rsa", "x.tar.gz", "key.pem", "notes.txt",
                     "stricthostkeychecking=no", "2222", "uptime"):
            self.assertNotIn(junk, hosts)

    def test_v4_ssh_prose_stopwords_not_hosts(self):
        self.bundle({"a.md": "ssh into the box, then ssh to it\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "ssh into the box, then ssh to it\n")

    def test_v4_pin_glm_ssh_shapes_that_already_worked(self):
        self.bundle({"a.md": "ssh bob@hn11.acme.com\nmosh hn12.acme.com\n"
                             "ping -c1 hn13\nssh -p 2222 hn14.acme.com\nalice@hn15\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        for h in ("hn11", "hn12", "hn13", "hn14", "hn15"):
            self.assertGone(h, out)
        self.assertIn("ssh -p 2222 node-", out)


# ===================================================================== V5

class V5RescanCanFire(VeilCase):
    def test_v5_glm_h6_qwen_h4_copy_through_leak_exits_nonzero(self):
        self.bundle({"blob.log": "Globex secret \xe9\n".encode("latin-1"), "ok.md": "fine\n"})
        r = self.write("--client", "Globex")
        self.assertRc(r, 3)
        self.assertRegex(r.stderr, r"LEAK blob\.log \(copied without scrubbing\): client 'Globex'")
        self.assertNotIn(" 0 leaks surviving", r.stderr)
        self.assertIn("1 leaks surviving", r.stderr)

    def test_v5_dry_run_reports_leak_and_fails(self):
        self.bundle({"blob.log": "Globex secret \xe9\n".encode("latin-1")})
        r = self.veil("--client", "Globex", "--dry-run")
        self.assertRc(r, 3)
        self.assertIn("LEAK blob.log", r.stderr)
        self.assertFalse(self.out.exists())

    def test_v5_scan_predicts_leak(self):
        self.bundle({"blob.log": "Globex secret \xe9\n".encode("latin-1")})
        r = self.veil("--client", "Globex", "--scan")
        self.assertRc(r, 3)
        self.assertIn("LEAK blob.log", r.stderr)

    def test_v5_every_value_class_is_rescanned(self):
        vals = "hn01.acme.com 10.200.14.5 0x0011223344556677 aa:bb:cc:dd:ee:ff ibnet0"
        self.bundle({"a.md": vals + "\n", "dump.dat": b"\xff " + vals.encode() + b"\n"})
        r = self.write(*ACME)
        self.assertRc(r, 3)
        for cls in ("hostname", "ip", "ib guid", "mac addr", "ib net"):
            self.assertRegex(r.stderr, rf"LEAK dump\.dat \(copied without scrubbing\): {cls} ")
        self.assertNotRegex(r.stderr, r"LEAK a\.md")

    def test_v5_filename_survivor_in_output_dir_is_caught(self):
        self.out.mkdir()
        (self.out / "acme-widgets-old.md").write_text("stale\n")
        self.bundle({"a.md": "Acme Widgets\n"})
        r = self.write(*ACME)
        self.assertRc(r, 3)
        self.assertRegex(r.stderr, r"LEAK acme-widgets-old\.md \(filename\) "
                                   r"\(pre-existing, not written by this run\)")

    def test_v5_clean_run_is_zero(self):
        self.bundle({"a.md": "Acme Widgets on hn01.acme.com 10.200.14.5\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertIn(" 0 leaks surviving", r.stderr)
        self.assertNotIn("LEAK", r.stderr)


# ===================================================================== V6

class V6ContractGuards(VeilCase):
    def test_v6a_glm_m3_out_equals_input_refused(self):
        self.bundle({"a.md": "Acme Widgets\n", "sub/10.200.14.6-notes.md": "x\n"})
        before = self.tree_hash(self.inp)
        r = self.veil(*ACME, "--out", str(self.inp))
        self.assertRc(r, 2)
        self.assertIn("inside", r.stderr)
        self.assertEqual(self.tree_hash(self.inp), before)

    def test_v6a_out_inside_input_refused(self):
        self.bundle({"a.md": "Acme Widgets\n"})
        before = self.tree_hash(self.inp)
        r = self.veil(*ACME, "--out", str(self.inp / "anon"))
        self.assertRc(r, 2)
        self.assertEqual(self.tree_hash(self.inp), before)

    def test_v6a_file_input_overwritten_in_place_refused(self):
        self.bundle({"a.md": "Acme Widgets\n"})
        before = self.tree_hash(self.inp)
        r = self.veil(*ACME, "--out", str(self.inp), inputs=[str(self.inp / "a.md")])
        self.assertRc(r, 2)
        self.assertEqual(self.tree_hash(self.inp), before)

    def test_v6b_glm_m5_no_client_for_out_is_hard_error(self):
        self.bundle({"a.md": "Acme runs here\n"})
        r = self.write("--domains", "acme.com")
        self.assertRc(r, 2)
        self.assertIn("no client names", r.stderr)
        self.assertFalse(self.out.exists())

    def test_v6b_no_client_dry_run_warns_only(self):
        self.bundle({"a.md": "Acme runs here\n"})
        r = self.veil("--dry-run")
        self.assertRc(r, 0)
        self.assertIn("WARNING: no client names", r.stderr)

    def test_v6c_qwen_m5_glm_m1_m2_vocabulary_collisions_refused(self):
        self.bundle({"a.md": "x\n"})
        cases = [("Node", "host prefix"), ("Mega", "replacement"),
                 ("Corp", "replacement"), ("Way land", "replacement"),
                 ("Example", "generic domain"), ("internal", "generic domain"),
                 ("fabric", "fabric name stem"), ("guid", "IB GUID stem"),
                 ("addr", "MAC stem"), ("10", "net base")]
        for name, what in cases:
            with self.subTest(client=name):
                r = self.write("--client", name)
                self.assertRc(r, 2)
                self.assertIn(f"client name {name!r} matches inside the {what}", r.stderr)
        self.assertFalse(self.out.exists())

    def test_v6c_qwen_f17_no_self_mangling_of_generated_names(self):
        self.bundle({"a.md": "node.acme.com and bare node\nNode.js app\n"})
        r = self.write("--client", "Globex", "--domains", "acme.com")
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertIn("node-001.example.internal and bare node-001\n", out)
        self.assertNotIn("node-001-001", out)
        self.assertNotIn("Wayland Megacorp-001", out)

    def test_v6d_qwen_l4_preexisting_differing_output_aborts(self):
        self.out.mkdir()
        (self.out / "a.md").write_text("PRE-EXISTING SECRET\n")
        self.bundle({"a.md": "Acme Widgets\n", "b.md": "other\n"})
        r = self.write(*ACME)
        self.assertRc(r, 2)
        self.assertIn("already exists with different content", r.stderr)
        self.assertEqual((self.out / "a.md").read_text(), "PRE-EXISTING SECRET\n")
        self.assertFalse((self.out / "b.md").exists())

    def test_v6d_identical_rerun_is_fine(self):
        self.bundle({"a.md": "Acme Widgets\n"})
        self.assertRc(self.write(*ACME), 0)
        self.assertRc(self.write(*ACME), 0)


# ===================================================================== V7

class V7CopyThrough(VeilCase):
    def test_v7_glm_m4_skipped_suffix_copied_verbatim_name_scrubbed(self):
        blob = b"\x1f\x8b\x08\x00binary-ish payload\x00\xff"
        self.bundle({"notes-globex-secret.md.gz": blob, "ok.md": "fine\n"})
        r = self.write("--client", "Globex")
        self.assertRc(r, 0)
        files = self.outfiles()
        self.assertEqual(files["notes-Wayland Megacorp-secret.md.gz"], blob)
        self.assertIn("Copied WITHOUT scrubbing", r.stderr)
        self.assertIn("notes-Wayland Megacorp-secret.md.gz  (binary suffix)", r.stderr)
        self.assertEqual(self.mapdoc()["copied_without_scrubbing"],
                         {"notes-Wayland Megacorp-secret.md.gz": "binary suffix"})

    def test_v7_qwen_f05_undecodable_copied_and_flagged(self):
        raw = b"Acme Widgets \xff\xfe broken\n"
        self.bundle({"bad.md": raw, "ok.md": "fine\n"})
        r = self.write(*ACME)
        self.assertRc(r, 3)          # copied verbatim, and the name inside is a LEAK
        self.assertEqual(self.outfiles()["bad.md"], raw)
        self.assertIn("bad.md  (not UTF-8)", r.stderr)

    def test_v7_qwen_f15_only_binaries_still_copied(self):
        png = b"\x89PNG\r\n\x1a\n\x00\x00fake"
        self.bundle({"img.png": png})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles(), {"img.png": png})
        self.assertIn("img.png  (binary suffix)", r.stderr)

    def test_v7_git_metadata_never_copied(self):
        self.bundle({"a.md": "x\n", ".git/config": "url = git@github.com:acme-widgets/x\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(list(self.outfiles()), ["a.md"])
        self.assertIn("SKIP .git/", r.stderr)

    def test_v7_readme_points_at_manifest(self):
        readme = (REPO / "README.md").read_text()
        self.assertIn("copied WITHOUT", readme)
        self.assertNotIn("are skipped — scrub those by hand", readme)


# ===================================================================== V8

class V8MapLeavesTheBundle(VeilCase):
    def test_v8_glm_h7_qwen_m2_default_map_is_sibling(self):
        self.bundle({"a.md": "Acme Widgets\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertNotIn("anonymize-map.json", self.outfiles())
        mp = (self.tmp / "anonymize-map.json").resolve()
        self.assertTrue(mp.exists())
        self.assertIn(f"WROTE {mp} ", r.stderr)

    def test_v8_map_override(self):
        self.bundle({"a.md": "Acme Widgets\n"})
        mp = self.tmp / "keys" / "m.json"
        r = self.write(*ACME, "--map", str(mp))
        self.assertRc(r, 0)
        self.assertTrue(mp.exists())
        self.assertFalse((self.tmp / "anonymize-map.json").exists())

    def test_v8_map_inside_out_refused(self):
        self.bundle({"a.md": "Acme Widgets\n"})
        r = self.write(*ACME, "--map", str(self.out / "anonymize-map.json"))
        self.assertRc(r, 2)
        self.assertFalse(self.out.exists())

    def test_v8_default_map_of_another_run_not_clobbered(self):
        (self.tmp / "anonymize-map.json").write_text('{"another": "run"}\n')
        self.bundle({"a.md": "Acme Widgets\n"})
        r = self.write(*ACME)
        self.assertRc(r, 2)
        self.assertEqual((self.tmp / "anonymize-map.json").read_text(), '{"another": "run"}\n')


# ===================================================================== V9-V11

class V9ListedDomainBeatsExtension(VeilCase):
    def test_v9_glm_m6_sh_and_cc_tlds(self):
        self.bundle({"a.md": "hub.acme.sh and node7.acme.cc and run deploy.sh\n"})
        r = self.write("--client", "Globex", "--domains", "acme.sh, acme.cc")
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertGone("acme", out)
        self.assertIn("run deploy.sh", out)


class V10DerivedBareSafety(VeilCase):
    def test_v10_qwen_m7_f14_public_fqdn_first_label_untouched(self):
        self.bundle({"a.md": "web www.acme.com site\nalso www.github.com stays\n"
                             "bare www reference here\nsee www.md\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertIn("also www.github.com stays", out)
        self.assertIn("web node-001.example.internal site", out)
        self.assertIn("bare node-001 reference", out)
        self.assertIn("see node-001.md", out)

    def test_v10_qwen_f22_no_derive_bare(self):
        self.bundle({"a.md": "web www.acme.com site\nalso www.github.com stays\nbare www\n"})
        r = self.write(*ACME, "--no-derive-bare")
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertIn("www.github.com", out)
        self.assertIn("bare www", out)       # the documented trade of the flag


class V11InfinibandHonesty(VeilCase):
    def test_v11_glm_l9_qwen_l6_f06_gid_shapes_and_mac(self):
        self.bundle({"a.md": "mac aa:bb:cc:dd:ee:ff and 00:1A:2B:3C:4D:5E\n"
                             "guid 0x0011223344556677 gid fe80::1122:33ff:fe44:5566\n"
                             "colon de:ad:be:ef:00:11:22:33\n"
                             "date 12:34:56:78 elapsed; partial 00:11:22:33; hh 12:34:56\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertIn("mac <mac-addr-", out)
        self.assertIn("date 12:34:56:78 elapsed; partial 00:11:22:33; hh 12:34:56", out)
        for gone in ("aa:bb", "1a:2b", "0011223344556677", "fe80::", "de:ad"):
            self.assertGone(gone, out)
        m = self.mapdoc()
        self.assertEqual(len(m["mac_addrs"]), 2)
        self.assertEqual(len(m["ib_guids"]), 3)

    def test_v11_qwen_l2_f18_ibnet_tools_exempt(self):
        self.bundle({"a.md": "run ibnetdiscover then ibnet0 and IBNETFOO; ibnetstat too\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertIn("run ibnetdiscover then fabric-net-", out)
        self.assertIn("ibnetstat too", out)
        self.assertGone("ibnet0", out)
        self.assertGone("ibnetfoo", out)

    def test_v11_glm_l2_qwen_f07_guid_case_is_one_id(self):
        self.bundle({"a.md": "0xDEADBEEF12345678 / 0xdeadbeef12345678 / AA:BB:CC:DD:EE:FF aa:bb:cc:dd:ee:ff\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertEqual(out, "<ib-guid-001> / <ib-guid-001> / <mac-addr-001> <mac-addr-001>\n")

    def test_v11_pin_ib_interfaces_left(self):
        self.bundle({"a.md": "ib0 and ib1 up\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(), "ib0 and ib1 up\n")
        self.assertIn("2 ibN refs left as-is", r.stderr)


# ===================================================================== V12

class V12Housekeeping(VeilCase):
    def test_v12a_glm_l8_qwen_l1_readme_pass_order(self):
        readme = (REPO / "README.md").read_text()
        self.assertNotIn("networks, IB, hostnames, then", readme)
        self.assertIn("this is the real order in the code", readme)
        doc = SCRIPT.read_text()
        self.assertIn("first matching class wins", doc)

    def test_v12b_glm_l3_fenris_f3_leading_zeros_canonical(self):
        self.bundle({"a.md": "010.020.030.005 and 10.20.30.7 and 10.20.030.9/24\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertEqual(out, "10.10.11.5 and 10.10.11.7 and 10.10.11.9/24\n")
        self.assertEqual(self.mapdoc()["networks"], {"10.20.30.0/24": "10.10.11.0/24"})

    def test_v12b_leading_zero_output_range_still_avoided(self):
        self.bundle({"a.md": "real 10.10.011.3 and 192.168.0.1\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertEqual(out, "real 10.10.12.3 and 10.10.13.1\n")

    def test_v12c_glm_l5_indented_comments_in_list_files(self):
        self.bundle({"a.md": "Globex at hn01.acme.com\n"})
        cf = self.tmp / "clients.txt"
        cf.write_text("Globex\n  # indented comment\n#normal comment\n")
        df = self.tmp / "domains.txt"
        df.write_text("  # indented\nacme.com\n")
        r = self.write("--client-file", str(cf), "--domain-file", str(df))
        self.assertRc(r, 0)
        m = self.mapdoc()
        self.assertEqual(list(m["client_names"]), ["Globex"])
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "Wayland Megacorp at node-001.example.internal\n")

    def test_v12d_qwen_m1_scan_reports_collision_as_error(self):
        self.bundle({"Acme.md": "a\n", "acme.md": "b\n"})
        r = self.veil("--client", "Acme", "--scan")
        self.assertRc(r, 1)
        self.assertIn("ERROR: filename collision", r.stderr)

    def test_v12d_scan_lists_copy_through_and_unlisted_domains(self):
        self.bundle({"a.md": "monitor.internal.acme.net and hn01.acme.com\n",
                     "pic.png": b"\x89PNG"})
        r = self.veil(*ACME, "--scan")
        self.assertRc(r, 0)
        self.assertIn("== copied WITHOUT scrubbing", r.stdout)
        self.assertIn("pic.png  (binary suffix)", r.stdout)
        # fix round 3 (N2): acme.net carries the client's word "Acme", so it
        # is auto-listed (was: advisory only, and split-mangled on --out)
        self.assertIn("example.internal  <- acme.net  (auto: carries a client name)", r.stdout)
        self.bundle({"b.md": "see build.example.org\n"})
        r = self.veil(*ACME, "--scan")
        self.assertRegex(r.stdout, r"NOT under --domains[\s\S]*example\.org  \(e\.g\. "
                                   r"build\.example\.org\)")

    def test_v12e_glm_l4_in_addr_arpa_paired_with_forward(self):
        # mini-fix 1: reverse names map through the same net map (was: left
        # alone, which published the real address octet-reversed)
        self.bundle({"a.md": "fwd 10.200.14.5\nrev 5.14.200.10.in-addr.arpa\n"
                             "zone 14.200.10.in-addr.arpa\nPTR 9.14.200.10.IN-ADDR.ARPA\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertEqual(out, "fwd 10.10.11.5\nrev 5.11.10.10.in-addr.arpa\n"
                              "zone 11.10.10.in-addr.arpa\nPTR 9.11.10.10.IN-ADDR.ARPA\n")
        self.assertEqual(self.mapdoc()["networks"], {"10.200.14.0/24": "10.10.11.0/24"})


class MiniFixReverseDns(VeilCase):
    def test_mini1_net_only_in_reverse_form_is_collected(self):
        self.bundle({"a.md": "ptr 7.3.168.192.in-addr.arpa\nzone 3.168.192.in-addr.arpa\n",
                     "9.3.168.192.in-addr.arpa.md": "x\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()
        self.assertEqual(out["a.md"].decode(),
                         "ptr 7.11.10.10.in-addr.arpa\nzone 11.10.10.in-addr.arpa\n")
        self.assertIn("9.11.10.10.in-addr.arpa.md", out)
        self.assertEqual(self.mapdoc()["networks"], {"192.168.3.0/24": "10.10.11.0/24"})
        self.assertGone("168.192", self.published())

    def test_mini1_reverse_and_forward_leading_zeros_one_net(self):
        self.bundle({"a.md": "010.200.014.005 and 005.014.200.010.in-addr.arpa\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "10.10.11.5 and 5.11.10.10.in-addr.arpa\n")
        self.assertEqual(len(self.mapdoc()["networks"]), 1)

    def test_mini1_reverse_output_range_collision_avoided(self):
        # a real net that appears only reversed is in the observed set, so no
        # other net is mapped onto it
        self.bundle({"a.md": "rev 4.11.10.10.in-addr.arpa and host 10.200.14.5\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "rev 4.12.10.10.in-addr.arpa and host 10.10.13.5\n")

    def test_mini1_non_identifying_reverse_left_alone(self):
        text = "1.0.0.127.in-addr.arpa 4.3.254.169.in-addr.arpa 999.1.1.10.in-addr.arpa\n"
        self.bundle({"a.md": text})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(), text)

    def test_mini1_rescan_flags_surviving_reversed_real_ip(self):
        self.bundle({"a.md": "fwd 10.200.14.5\n",
                     "dump.dat": b"\xff PTR 5.14.200.10.in-addr.arpa\n"})
        r = self.write(*ACME)
        self.assertRc(r, 3)
        self.assertRegex(r.stderr, r"LEAK dump\.dat \(copied without scrubbing\): "
                                   r"reverse ip '5\.14\.200\.10'")
        self.assertNotRegex(r.stderr, r"LEAK a\.md")

    def test_mini1_rescan_flags_reverse_only_net_in_copy(self):
        self.bundle({"a.md": "zone 3.168.192.in-addr.arpa\n",
                     "dump.dat": b"\xff zone 3.168.192.in-addr.arpa\n"})
        r = self.write(*ACME)
        self.assertRc(r, 3)
        self.assertRegex(r.stderr, r"LEAK dump\.dat .*reverse ip '3\.168\.192'")


class MiniFixDashMacs(VeilCase):
    def test_mini2_dash_mac_same_identity_as_colon(self):
        self.bundle({"a.md": "win 00-1A-2B-3C-4D-5E linux 00:1a:2b:3c:4d:5e "
                             "lower 00-1a-2b-3c-4d-5e other AA-BB-CC-DD-EE-FF\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "win <mac-addr-001> linux <mac-addr-001> "
                         "lower <mac-addr-001> other <mac-addr-002>\n")
        self.assertEqual(self.mapdoc()["mac_addrs"],
                         {"00:1a:2b:3c:4d:5e": "<mac-addr-001>",
                          "aa:bb:cc:dd:ee:ff": "<mac-addr-002>"})

    def test_mini2_dash_mac_only_in_filename(self):
        self.bundle({"nic-00-1A-2B-3C-4D-5E.md": "x\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(list(self.outfiles()), ["nic-<mac-addr-001>.md"])

    def test_mini2_rescan_flags_other_separator_form(self):
        self.bundle({"a.md": "mac 00:1a:2b:3c:4d:5e\n",
                     "dump.dat": b"\xff mac 00-1A-2B-3C-4D-5E\n"})
        r = self.write(*ACME)
        self.assertRc(r, 3)
        self.assertRegex(r.stderr, r"LEAK dump\.dat .*mac addr '00-1A-2B-3C-4D-5E'")

    def test_mini2_dashed_non_macs_untouched(self):
        text = ("date 2026-11-17 uuid-ish 00-11-22-33-44 seven "
                "00-11-22-33-44-55-66 mixed 00-11:22-33:44-55 word a-b-c-d-e-f\n")
        self.bundle({"a.md": text})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(), text)


class ExtraListedDomainMidToken(VeilCase):
    def test_extra_qwen_l5_f11_listed_domain_mid_token(self):
        self.bundle({"a.md": "hn01.acme.com.prod and hn02.acme.com.svc.local\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()["a.md"].decode()
        self.assertGone("hn01", out)
        self.assertGone("hn02", out)
        self.assertGone("acme", out)


# ===================================================================== pins

class PinnedSoundBehavior(VeilCase):
    def test_pin_network_remap_and_collision_avoidance(self):
        self.bundle({"a.md": "real 10.10.11.7, host 10.200.14.5, net 192.168.0.0/24\n"
                             "url https://10.200.14.5:8080/api\n"
                             "left 127.0.0.1 169.254.3.4 255.255.255.0 0.0.0.0 224.0.0.1\n",
                     "sub/10.200.14.6-notes.md": "x\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        out = self.outfiles()
        self.assertEqual(out["a.md"].decode(),
                         "real 10.10.12.7, host 10.10.13.5, net 10.10.14.0/24\n"
                         "url https://10.10.13.5:8080/api\n"
                         "left 127.0.0.1 169.254.3.4 255.255.255.0 0.0.0.0 224.0.0.1\n")
        self.assertIn("sub/10.10.13.6-notes.md", out)

    def test_pin_content_fqdns_domain_and_parent_collapse(self):
        self.bundle({"a.md": "acl-bcm-hn01.acl.eng.cdwbuilt.com under acl.eng.cdwbuilt.com "
                             "in cdwbuilt.com\nbare acl-bcm-hn01 in cmsh\n"})
        r = self.write("--client", "Globex", "--domains", "cdwbuilt.com")
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "node-001.example.internal under example.internal in "
                         "example.internal\nbare node-001 in cmsh\n")

    def test_pin_glm_f24_ip_prefixed_fqdn_replaced_whole(self):
        self.bundle({"a.md": "10.200.14.5.acme.com\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(), "node-001.example.internal\n")

    def test_pin_qwen_f13_user_at_fqdns(self):
        self.bundle({"a.md": "admin@hn01.acme.com root@hn02.acme.com\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "admin@node-001.example.internal root@node-002.example.internal\n")

    def test_pin_qwen_f26_collision_abort_writes_nothing(self):
        self.bundle({"Acme.md": "a\n", "acme.md": "b\n"})
        r = self.write("--client", "Acme")
        self.assertRc(r, 1)
        self.assertIn("ERROR: filename collision", r.stderr)
        self.assertEqual(self.outfiles(), {})
        self.assertFalse((self.tmp / "anonymize-map.json").exists())

    def test_pin_dry_run_writes_nothing(self):
        self.bundle({"a.md": "Acme Widgets at hn01.acme.com\n"})
        before = sorted(p.name for p in self.tmp.iterdir())
        r = self.veil(*ACME, "--dry-run", "--out", str(self.out))
        self.assertRc(r, 0)
        self.assertIn("+Wayland Megacorp at node-001.example.internal", r.stdout)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)

    def test_pin_public_and_cluster_local_left_alone(self):
        text = ("quay.io/x kubernetes.io access.redhat.com github.com/org\n"
                "redis.data.svc.cluster.local:6379 spec.containers nfd.enabled\n")
        self.bundle({"a.md": text})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(), text)

    def test_pin_inputs_never_modified(self):
        self.bundle({"a.md": "Acme Widgets hn01.acme.com 10.200.14.5\n",
                     "10.200.14.5.md": "x\n", "p.png": b"\x89PNG"})
        before = self.tree_hash(self.inp)
        self.assertRc(self.write(*ACME), 0)
        self.assertEqual(self.tree_hash(self.inp), before)

    def test_extra_crlf_preserved(self):
        # new: the original read with universal newlines and wrote LF
        self.bundle({"a.md": b"Acme Widgets\r\nline two\r\n"})
        self.assertRc(self.write(*ACME), 0)
        self.assertEqual(self.outfiles()["a.md"], b"Wayland Megacorp\r\nline two\r\n")


# ===================================================================== round 2
# W1-W7 from REVIEW-VEIL2-glm.md (glm2-*) and REVIEW-VEIL2-qwen.md (qwen2-*).

class W1NulTextIsNotScrubbable(VeilCase):
    def _utf16(self, codec):
        payload = "host hn01.acme.com at 10.200.14.5 up\n".encode(codec)
        self.bundle({"b.md": "also hn01.acme.com here\n", "notes.log": payload})
        r = self.write("--client", "Globex", "--domains", "acme.com")
        self.assertRc(r, 3)
        self.assertEqual(self.outfiles()["notes.log"], payload)       # verbatim
        self.assertIn("notes.log  (contains NUL bytes", r.stderr)     # manifest
        self.assertRegex(r.stderr, r"LEAK notes\.log \(copied without scrubbing\): hostname ")
        self.assertRegex(r.stderr, r"LEAK notes\.log \(copied without scrubbing\): ip ")
        self.assertEqual(self.outfiles()["b.md"].decode(), "also node-001.example.internal here\n")

    def test_w1_glm2_n1_qwen2_n2_utf16le_no_bom(self):
        self._utf16("utf-16-le")

    def test_w1_glm2_n1_qwen2_n2_utf16be_no_bom(self):
        self._utf16("utf-16-be")

    def test_w1_qwen2_n2_utf16_client_name_only(self):
        payload = "Acme Widgets visited the site\n".encode("utf-16-le")
        self.bundle({"notes.md": payload})
        r = self.write(*ACME)
        self.assertRc(r, 3)
        self.assertEqual(self.outfiles()["notes.md"], payload)
        self.assertRegex(r.stderr, r"LEAK notes\.md \(copied without scrubbing\): client ")


class W2LoneClientWords(VeilCase):
    def test_w2_glm2_n2_possessive(self):
        self.bundle({"a.md": "Acme's datacenter is big.\n"})
        r = self.write("--client", "Acme Corp")
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(), "Wayland Megacorp's datacenter is big.\n")

    def test_w2_glm2_n2_bare_first_word(self):
        self.bundle({"a.md": "At Acme we build things.\n"})
        r = self.write("--client", "Acme Corp")
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(), "At Wayland Megacorp we build things.\n")

    def test_w2_last_word_alone(self):
        self.bundle({"a.md": "the Widgets team shipped\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertGone("widgets", self.published())

    def test_w2_glm2_n2_copy_through_partial(self):
        self.bundle({"ok.md": "fine\n", "blob.log": "Globex secret \xe9\n".encode("latin-1")})
        r = self.write("--client", "Globex Corp")
        self.assertRc(r, 3)
        self.assertRegex(r.stderr, r"LEAK blob\.log \(copied without scrubbing\): client 'Globex'")

    def test_w2_glm2_n2_filename_partial(self):
        blob = b"\x1f\x8b\x08\x00payload"
        self.bundle({"notes-globex-secret.md.gz": blob})
        r = self.write("--client", "Globex Corp")
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles(), {"notes-Wayland Megacorp-secret.md.gz": blob})

    def test_w2_suffixes_and_short_words_never_alone(self):
        self.bundle({"a.md": "Microsoft Corp and an AB test; AB Widgets; Globex Corp\n"})
        r = self.write("--client", "Globex Corp", "--client", "AB Widgets")
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "Microsoft Corp and an AB test; Wayland Megacorp; Wayland Megacorp\n")
        self.assertEqual(self.mapdoc()["client_words_matched_alone"],
                         {"Globex Corp": ["Globex"], "AB Widgets": ["Widgets"]})

    def test_w2_qwen2_n7_lone_word_vocab_collision_refused_loudly(self):
        self.bundle({"a.md": "x\n"})
        for name, word, what in (("Acme Node", "Node", "host prefix"),
                                 ("Mega Widgets", "Mega", "replacement")):
            with self.subTest(client=name):
                r = self.write("--client", name)
                self.assertRc(r, 2)
                self.assertIn(f"client name {name!r}: its word {word!r} (matched alone) "
                              f"matches inside the {what}", r.stderr)
        self.assertFalse(self.out.exists())


class W3MaskNeverManufacturesAClient(VeilCase):
    def test_w3_glm2_n3_exact_fixture_exits_zero(self):
        self.bundle({"a.md": "Acme acl-bcm-hn01 Corp merged\n",
                     "b.md": "ssh acl-bcm-hn01.acme.com\n"})
        r = self.write("--client", "Acme Corp", "--domains", "acme.com")
        self.assertRc(r, 0)
        self.assertNotIn("LEAK", r.stderr)

    def test_w3_qwen2_n3_masked_value_between_unmatched_words(self):
        # "AB" (too short) and "Corp" (suffix) are never matched alone, so
        # both words stay in the output around a masked value -- the shape
        # that a NUL mask turned into a false client LEAK.
        self.bundle({"a.md": "AB node-001 Corp merged\n| AB | hn01.acme.com | Corp |\n"})
        r = self.write("--client", "AB Corp", "--domains", "acme.com")
        self.assertRc(r, 0)
        self.assertNotIn("LEAK", r.stderr)
        self.assertIn("| AB | node-001.example.internal | Corp |", self.outfiles()["a.md"].decode())


class W4CopyThroughIsCollected(VeilCase):
    def test_w4_glm2_n4_identifiers_only_in_copy_through(self):
        self.bundle({"a.md": "clean prose only\n",
                     "dump.dat": b"gw 10.200.14.5 and 0x0011223344556677 and mac "
                                 b"aa:bb:cc:dd:ee:ff and hn07.acme.com \xe9\n"})
        r = self.write("--client", "Globex", "--domains", "acme.com")
        self.assertRc(r, 3)
        for cls in ("ip", "ib guid", "mac addr", "hostname"):
            self.assertRegex(r.stderr, rf"LEAK dump\.dat \(copied without scrubbing\): {cls} ")
        self.assertNotIn(" 0 leaks surviving", r.stderr)

    def test_w4_copy_noise_never_becomes_a_bare_host(self):
        self.bundle({"a.md": "the ab test and ssh into it\n", "dump.dat": b"\xff x@ab \n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(), "the ab test and ssh into it\n")


class W5BareHostAnyExtension(VeilCase):
    def test_w5_qwen2_n1_bare_host_with_unlisted_extension(self):
        self.bundle({"a.md": "dbx.acme.com is the db\nssh hn01\nsee hn01.docx attached\n"
                             "and hn01.backup.xlsx and HN01.SO and dbx.rtf\n"
                             "keep www.github.com and dbx.log.globex.com\n",
                     "hn01.docx": "doc\n", "dbx.docx": "doc\n"})
        r = self.write(*ACME)
        self.assertRc(r, 0)
        files = self.outfiles()
        out = files["a.md"].decode()
        for gone in ("hn01", "dbx.rtf", "dbx.acme"):
            self.assertGone(gone, out)
        self.assertIn("see node-", out)
        self.assertIn(".docx attached", out)
        self.assertIn("keep www.github.com and dbx.log.globex.com", out)   # domains intact
        self.assertGone("hn01", " ".join(files))
        self.assertGone("dbx", " ".join(files))


class W6SymlinkGuard(VeilCase):
    def test_w6_qwen2_n4a_symlinked_dest_with_identical_bytes(self):
        victim = self.tmp / "victim.txt"
        victim.write_text("Wayland Megacorp\n")
        before = victim.stat().st_mtime_ns
        self.out.mkdir()
        os.symlink(victim, self.out / "a.md")
        self.bundle({"a.md": "Acme Widgets\n"})
        r = self.write(*ACME)
        self.assertRc(r, 2)
        self.assertIn("is a symlink", r.stderr)
        self.assertEqual(victim.stat().st_mtime_ns, before)

    def test_w6_qwen2_n4b_symlinked_intermediate_directory(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.out.mkdir()
        os.symlink(elsewhere, self.out / "sub")
        self.bundle({"sub/x.md": "Acme Widgets\n", "top.md": "y\n"})
        r = self.write(*ACME)
        self.assertRc(r, 2)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertFalse((self.out / "top.md").exists())     # nothing written

    def test_w6_qwen2_n4c_symlinked_out_onto_input_refused(self):
        self.bundle({"a.md": "Acme Widgets\n"})
        link = self.tmp / "outlink"
        os.symlink(self.inp, link)
        before = self.tree_hash(self.inp)
        r = self.veil(*ACME, "--out", str(link))
        self.assertRc(r, 2)
        self.assertEqual(self.tree_hash(self.inp), before)

    def test_w6_symlinked_out_to_fresh_dir_is_the_operators_choice(self):
        real = self.tmp / "realout"
        real.mkdir()
        link = self.tmp / "outlink"
        os.symlink(real, link)
        self.bundle({"a.md": "Acme Widgets\n"})
        r = self.veil(*ACME, "--out", str(link))
        self.assertRc(r, 0)
        self.assertEqual((real / "a.md").read_text(), "Wayland Megacorp\n")


class W7CheapLows(VeilCase):
    def test_w7a_qwen2_n10_case_insensitive_collision_pinned(self):
        self.bundle({"Notes.md": "a\n", "notes.md": "b\n"})
        r = self.write(*ACME)
        self.assertRc(r, 1)
        self.assertIn("filename collision", r.stderr)
        self.assertEqual(self.outfiles(), {})

    def test_w7b_glm2_n6_scan_sees_filename_fqdn_with_extension(self):
        self.bundle({"hub.acmewidgets.internal.md": "x\n"})
        r = self.veil("--client", "Globex", "--scan")
        self.assertRc(r, 0)
        self.assertRegex(r.stdout, r"NOT under --domains \(1, left alone\)[\s\S]*"
                                   r"acmewidgets\.internal  \(e\.g\. hub\.acmewidgets\.internal\)")


# ===================================================================== fix round 3
# N1/N2 = REVIEW-VEIL3-glm.md section 2.

class N1ClientWordsHaveBoundaries(VeilCase):
    def check(self, client, text, want):
        self.bundle({"a.md": text + "\n"})
        r = self.write("--client", client)
        self.assertRc(r, 0)
        self.assertNotIn("LEAK", r.stderr)
        self.assertEqual(self.outfiles()["a.md"].decode(), want + "\n")

    def test_n1_glm3_the_group_prose_survives(self):
        self.check("The Group", "the quick brown fox and The Group visited",
                   "the quick brown fox and Wayland Megacorp visited")

    def test_n1_glm3_one_logistics_prose_survives(self):
        self.check("One Logistics", "one two three, One Logistics visited",
                   "one two three, Wayland Megacorp visited")

    def test_n1_glm3_new_venture_never_inside_a_word(self):
        self.check("New Venture", "renew the lease with New Venture",
                   "renew the lease with Wayland Megacorp")

    def test_n1_lone_word_never_spliced_inside_a_larger_word(self):
        self.check("Globex Corp", "subglobexing aside, Globex shipped and globex_db is up",
                   "subglobexing aside, Wayland Megacorp shipped and Wayland Megacorp_db is up")

    def test_n1_stopword_first_word_not_alone_but_full_name_still_is(self):
        self.bundle({"a.md": "The plan: The Group, THE GROUP and the-group.\n"})
        r = self.write("--client", "The Group")
        self.assertRc(r, 0)
        self.assertEqual(self.outfiles()["a.md"].decode(),
                         "The plan: Wayland Megacorp, Wayland Megacorp and Wayland Megacorp.\n")
        self.assertEqual(self.mapdoc()["client_words_matched_alone"], {})   # no word alone

    def test_n1_pin_w2_every_form_still_one_pseudonym(self):
        self.check("Acme Corp", "Acme's gateway failed. Acme declined. Acme Corp HQ.",
                   "Wayland Megacorp's gateway failed. Wayland Megacorp declined. "
                   "Wayland Megacorp HQ.")


class N2ClientTokenInsideUnlistedFqdn(VeilCase):
    def test_n2_glm3_exact_fixture(self):
        self.bundle({"a.md": "dbx.acme.com and dbx.log.globex.com\n"})
        r = self.write("--client", "Globex", "--domains", "acme.com")
        self.assertRc(r, 0)
        self.assertNotIn("LEAK", r.stderr)
        out = self.outfiles()["a.md"].decode()
        self.assertEqual(out, "node-001.example.internal and node-002.example.internal\n")
        for gone in ("globex", "dbx", "Wayland Megacorp."):
            self.assertGone(gone, self.published())

    def test_n2_lone_word_inside_unlisted_fqdn_and_filename(self):
        self.bundle({"a.md": "see www.acme.net and Acme alone\n",
                     "hub.acme.net.md": "x\n"})
        r = self.write("--client", "Acme Corp")
        self.assertRc(r, 0)
        self.assertNotIn("LEAK", r.stderr)
        pub = self.published()
        self.assertGone("acme", pub)
        self.assertNotIn("Wayland Megacorp.", pub)
        self.assertIn("see node-", self.outfiles()["a.md"].decode())

    def test_n2_scan_names_the_auto_listed_client_domain(self):
        self.bundle({"a.md": "dbx.log.globex.com\n"})
        r = self.veil("--client", "Globex", "--scan")
        self.assertRc(r, 0)
        self.assertRegex(r.stdout, r"example\.internal  <- globex\.com  \(auto")


if __name__ == "__main__":
    unittest.main()


class W8InputSymlinkGuard(VeilCase):
    """Issue #9 (2026-11-19): input symlinks are refused before any read,
    the mirror of the W6 output-side guard."""

    def test_w8a_explicit_input_symlink_refused(self):
        outside = self.tmp / "outside.md"
        outside.write_text("Acme Widgets secret\n")
        link = self.tmp / "link.md"
        os.symlink(outside, link)
        r = self.write(*ACME, inputs=[str(link)])
        self.assertRc(r, 2)
        self.assertIn("is a symlink", r.stderr)
        self.assertEqual(self.outfiles(), {})

    def test_w8b_symlinked_file_inside_bundle_refused(self):
        outside = self.tmp / "outside.md"
        outside.write_text("Acme Widgets secret\n")
        self.bundle({"real.md": "Acme Widgets\n"})
        os.symlink(outside, self.inp / "sneaky.md")
        r = self.write(*ACME)
        self.assertRc(r, 2)
        self.assertIn("is a symlink", r.stderr)
        self.assertEqual(self.outfiles(), {})

    def test_w8c_symlinked_dir_inside_bundle_refused(self):
        outside_dir = self.tmp / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "x.md").write_text("Acme Widgets secret\n")
        self.bundle({"real.md": "Acme Widgets\n"})
        os.symlink(outside_dir, self.inp / "linked-dir")
        r = self.write(*ACME)
        self.assertRc(r, 2)
        self.assertIn("is a symlink", r.stderr)
        self.assertEqual(self.outfiles(), {})

    def test_w8d_symlink_inside_dotgit_never_read_and_allowed(self):
        self.bundle({"real.md": "Acme Widgets\n"})
        gitdir = self.inp / ".git"
        gitdir.mkdir()
        outside = self.tmp / "outside.md"
        outside.write_text("Acme Widgets secret\n")
        os.symlink(outside, gitdir / "link")
        r = self.write(*ACME)
        self.assertRc(r, 0)
        for data in self.outfiles().values():
            self.assertNotIn(b"secret", data)


class W9MapPermissions(VeilCase):
    """Issue #17 (2026-11-19): the identity map holds the real values.
    It is written through a descriptor created 0600 (the umask cannot
    widen it), and a symlinked or group/other-accessible map path is
    refused before any truncation."""

    def test_w9a_fresh_map_is_0600_under_zero_umask(self):
        self.bundle({"note.md": "Acme Widgets\n"})
        old = os.umask(0)
        try:
            r = self.write(*ACME)
        finally:
            os.umask(old)
        self.assertRc(r, 0)
        mappath = self.tmp / "anonymize-map.json"
        self.assertTrue(mappath.exists())
        self.assertEqual(stat.S_IMODE(mappath.stat().st_mode), 0o600)

    def test_w9b_loose_existing_map_refused_and_preserved(self):
        self.bundle({"note.md": "Acme Widgets\n"})
        mappath = self.tmp / "my-map.json"
        mappath.write_text("sentinel\n", encoding="utf-8")
        os.chmod(mappath, 0o644)
        r = self.write(*ACME, "--map", str(mappath))
        self.assertRc(r, 2)
        self.assertIn("group/other-accessible", r.stderr)
        self.assertEqual(mappath.read_text(encoding="utf-8"), "sentinel\n")

    def test_w9c_map_symlink_refused_target_preserved(self):
        self.bundle({"note.md": "Acme Widgets\n"})
        target = self.tmp / "target.json"
        target.write_text("sentinel\n", encoding="utf-8")
        os.chmod(target, 0o600)
        link = self.tmp / "link-map.json"
        os.symlink(target, link)
        r = self.write(*ACME, "--map", str(link))
        self.assertRc(r, 2)
        self.assertIn("is a symlink", r.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "sentinel\n")

    def test_w9d_existing_0600_map_rewritten(self):
        self.bundle({"note.md": "Acme Widgets\n"})
        mappath = self.tmp / "my-map.json"
        mappath.write_text("{}\n", encoding="utf-8")
        os.chmod(mappath, 0o600)
        r = self.write(*ACME, "--map", str(mappath))
        self.assertRc(r, 0)
        self.assertGreater(len(mappath.read_text(encoding="utf-8")), 10)
        self.assertEqual(stat.S_IMODE(mappath.stat().st_mode), 0o600)
