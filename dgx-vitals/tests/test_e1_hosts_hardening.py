"""E1 (2026-11-19) -- hosts-file hardening: issues #3, #5, #6.

#6: duplicate hostnames shared one evidence directory. #5: a traversing
or absolute hostname redirected FULLLOG writes outside --out. #3: host
or user values beginning with '-' were parsed by ssh/scp as options.
The parser now refuses all three classes, SSHRunner places '--' before
the destination, and _contained_host_dir enforces containment at the
point of use."""

import os
import unittest

from tests import support as S


class HostsParserRefusals(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)
        self.vit = S.load_vitals()

    def parse(self, text):
        p = os.path.join(self.tmp, "hosts.csv")
        with open(p, "w") as f:
            f.write(text)
        return self.vit.read_host_entries(p)

    def test_valid_file_still_parses(self):
        es = self.parse("hostname,address,user\n"
                        "gpu01,10.0.0.51,root\n")
        self.assertEqual(
            [(e["name"], e["address"], e["user"]) for e in es],
            [("gpu01", "10.0.0.51", "root")])

    def test_duplicate_hostname_refused(self):
        with self.assertRaises(self.vit.HostsFileError):
            self.parse("gpu01,10.0.0.51\ngpu01,10.0.0.52\n")

    def test_unsafe_hostnames_refused(self):
        for bad in ("../evil", "/abs", "a/b", "..",
                    "-oProxyCommand=x"):
            with self.assertRaises(self.vit.HostsFileError, msg=bad):
                self.parse(f"{bad},10.0.0.51\n")

    def test_option_like_address_refused(self):
        with self.assertRaises(self.vit.HostsFileError):
            self.parse("gpu01,-oProxyCommand=touch /tmp/pwned\n")

    def test_option_like_user_refused(self):
        with self.assertRaises(self.vit.HostsFileError):
            self.parse("gpu01,10.0.0.51,-oProxyCommand=x\n")

    def test_whitespace_address_refused(self):
        with self.assertRaises(self.vit.HostsFileError):
            self.parse("gpu01,10.0.0.51 10.0.0.52\n")


class SSHRunnerOptionsMarker(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.cmds = []

        def fake_exec(cmd, timeout, stdin_data=None, stream=None, **kw):
            self.cmds.append(cmd)
            return 0, "", ""
        self.vit._exec = fake_exec

    def test_ssh_target_behind_end_of_options(self):
        r = self.vit.SSHRunner("10.0.0.51", "root")
        r.run(["echo", "hi"], 10)
        cmd = self.cmds[-1]
        self.assertEqual(cmd[cmd.index("root@10.0.0.51") - 1], "--")

    def test_scp_target_behind_end_of_options(self):
        r = self.vit.SSHRunner("10.0.0.51", "root")
        r.fetch("/var/log/x", "/tmp/x")
        cmd = self.cmds[-1]
        i = cmd.index("root@10.0.0.51:/var/log/x")
        self.assertEqual(cmd[i - 1], "--")


class ContainedHostDir(unittest.TestCase):

    def setUp(self):
        self.vit = S.load_vitals()
        self.tmp = S.tempdir(self)

    def test_inside_ok(self):
        hd = self.vit._contained_host_dir(self.tmp, "gpu01")
        self.assertEqual(
            hd, os.path.join(os.path.realpath(self.tmp), "gpu01"))

    def test_escape_refused(self):
        for bad in ("../evil", "/abs", ".", ""):
            with self.assertRaises(ValueError, msg=bad):
                self.vit._contained_host_dir(self.tmp, bad)
