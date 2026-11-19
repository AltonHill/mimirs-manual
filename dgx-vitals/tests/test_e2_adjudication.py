"""E2 — manual adjudications are signed, reasoned, and can never mint
a pass (issue #14): a row must name a reviewer and carry a substantive
note, may set only Failed/Skipped/Warned, and any invalid row is
reported NOT APPLIED in the footer, with the reason, and exits 1."""

import os
import unittest

import tracker_build

from tests import support as S

NOTE = "Ruled after review of the host's own logs and the run window"
REVIEWER = "ops.lead"


class AdjudicationRules(unittest.TestCase):

    def setUp(self):
        self.tmp = S.tempdir(self)

    def _failed(self, adj_rows):
        adj = S.write_adjudications(os.path.join(self.tmp, "adj.csv"),
                                    adj_rows)
        root = S.write_vitals_root(
            os.path.join(self.tmp, "r"),
            [S.full_dgx("a", "NOT HEALTHY", NVSM_Health="Failed")])
        return S.run_tracker(["--vitals", root, "--adjudications", adj],
                             self.tmp)

    def test_complete_override_is_not_applied(self):
        sh = self._failed([("a", "NVSM Health", "Complete", NOTE,
                            REVIEWER)])
        self.assertEqual(sh.rc, 1)
        self.assertEqual(sh.get("a", "NVSM Health"), "Failed")
        self.assertEqual(sh.get("a", "Overall"), "NOT HEALTHY")
        self.assertTrue(
            any("NOT APPLIED" in line and "Complete" in line
                for line in sh.footer), sh.footer)

    def test_missing_reviewer_is_not_applied(self):
        sh = self._failed([("a", "NVSM Health", "Warned", NOTE, "")])
        self.assertEqual(sh.rc, 1)
        self.assertEqual(sh.get("a", "NVSM Health"), "Failed")
        self.assertTrue(
            any("NOT APPLIED" in line and "reviewer" in line
                for line in sh.footer), sh.footer)

    def test_thin_note_is_not_applied(self):
        sh = self._failed([("a", "NVSM Health", "Warned", "ruled benign",
                            REVIEWER)])
        self.assertEqual(sh.rc, 1)
        self.assertEqual(sh.get("a", "NVSM Health"), "Failed")
        self.assertTrue(
            any("NOT APPLIED" in line and "substantive" in line
                for line in sh.footer), sh.footer)

    def test_unknown_state_is_not_applied(self):
        sh = self._failed([("a", "NVSM Health", "Passed", NOTE,
                            REVIEWER)])
        self.assertEqual(sh.rc, 1)
        self.assertEqual(sh.get("a", "NVSM Health"), "Failed")

    def test_signed_warned_ruling_applies(self):
        sh = self._failed([("a", "NVSM Health", "Warned", NOTE,
                            REVIEWER)])
        self.assertEqual(sh.rc, 0)
        self.assertEqual(sh.get("a", "NVSM Health"), "Warned")
        self.assertEqual(sh.get("a", "Overall"), "HEALTHY WITH WARNINGS")
        self.assertTrue(
            any("Manual adjudication" in line
                and "Reviewer: ops.lead" in line
                for line in sh.footer), sh.footer)


class ReadAdjudications(unittest.TestCase):

    def test_reviewer_column_and_blank_lines(self):
        tmp = S.tempdir(self)
        path = os.path.join(tmp, "adj.csv")
        with open(path, "w", newline="") as f:
            f.write("hostname,step,state,note,reviewer\n")
            f.write(f"a,NVSM Health,Warned,{NOTE},ops.lead\n")
            f.write("\n")
        rows = tracker_build.read_adjudications(path)
        self.assertEqual(rows, [("a", "NVSM Health", "Warned", NOTE,
                                 "ops.lead")])


if __name__ == "__main__":
    unittest.main()
