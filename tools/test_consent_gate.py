#!/usr/bin/env python3
"""Tests for consent_gate.py. All participants, codes and registers are SYNTHETIC test data created in temp
directories; no real consent or identity data exists in this repository."""
import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consent_gate as cg  # noqa: E402

BUILD = os.environ.get("S2W_BUILD", "build")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCHEMA = os.path.join(ROOT, "data", "schema")
HAVE_C = all(os.path.exists(os.path.join(BUILD, n)) for n in ("s2w_make_fixture", "s2w_dataset_check", "s2w_eval"))
TODAY = "2001-06-01"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cg.main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def part_row(code, **kw):
    r = dict(participant_code=code, identity_record_ref="vault-ref-" + code[-3:], recruitment_source="open_call", independent_recruitment_confirmed="yes",
             reads_vocabulary_comfortably="yes", status="eligible", status_date="2000-01-02")
    r.update(kw)
    return r


def cons_row(code, **kw):
    r = dict(participant_code=code, consent_version="TEST-v1", information_sheet_version="TEST-v1", consent_date="2000-01-02", recording_and_analysis="yes",
             retention_until="2002-01-02", future_experiments="no", public_release="no", group_reporting_ok="no", optional_attributes_ok="no", status="consented",
             withdrawal_date="", withdrawal_action="")
    r.update(kw)
    return r


def write(path, tag, cols, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(tag + "\n")
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "participants.csv")
        self.c = os.path.join(self.d, "consent.csv")
        self.codes = ["SPK-001", "SPK-002", "SPK-003"]
        self.set_regs([part_row(c) for c in self.codes], [cons_row(c) for c in self.codes])

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def set_regs(self, prows, crows):
        write(self.p, cg.PART_TAG, cg.PART_COLS, prows)
        write(self.c, cg.CONSENT_TAG, cg.CONSENT_COLS, crows)

    def check(self, *extra):
        return run("check", "--participants", self.p, "--consent", self.c, "--today", TODAY, *extra)


class Registers(Base):
    def test_templates_are_header_only_and_valid(self):
        for name in ("participant_register_template.csv", "consent_register_template.csv"):
            with open(os.path.join(SCHEMA, name), encoding="utf-8") as f:
                self.assertEqual(f.read().count("\n"), 2)                              # tag + header only: no dummy people
        code, out, err = run("check", "--participants", os.path.join(SCHEMA, "participant_register_template.csv"),
                             "--consent", os.path.join(SCHEMA, "consent_register_template.csv"), "--today", TODAY)
        self.assertEqual(code, 0, err)
        self.assertIn("participants=0", out)
        self.assertEqual(cg.read_tagged(os.path.join(SCHEMA, "pilot_approvals_template.csv"), cg.APPROVALS_TAG, ["item", "status", "approver_id", "date", "reference"])[0]["status"], "pending")

    def test_valid_registers_and_eligibility(self):
        code, out, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertIn("eligible_now=3", out)
        code, out, _ = run("eligible", "--participants", self.p, "--consent", self.c, "--today", TODAY)
        self.assertEqual((code, out.split()), (0, self.codes))

    def test_not_eligible_without_completed_consent(self):
        rows = [part_row("SPK-001"), part_row("SPK-002", status="screened"), part_row("SPK-003", status="screened")]
        crows = [cons_row("SPK-001"), cons_row("SPK-002", status="pending", consent_date="", retention_until=""),
                 cons_row("SPK-003", recording_and_analysis="no", status="pending", consent_version="", information_sheet_version="", consent_date="", retention_until="")]
        self.set_regs(rows, crows)
        code, out, err = run("eligible", "--participants", self.p, "--consent", self.c, "--today", TODAY)
        self.assertEqual((code, out.split()), (0, ["SPK-001"]))
        # marking someone eligible before consent is complete is an error, not a silent pass
        self.set_regs([part_row("SPK-001"), part_row("SPK-002")], [cons_row("SPK-001"), cons_row("SPK-002", status="pending", consent_date="", retention_until="")])
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("eligible without a completed consent record", err)
        code, out, err = run("eligible", "--participants", self.p, "--consent", self.c, "--today", TODAY)
        self.assertEqual((code, out.strip()), (1, ""))                                  # invalid registers: nobody is reported eligible

    def test_withdrawal_and_expiry(self):
        self.set_regs([part_row("SPK-001"), part_row("SPK-002", status="withdrawn", status_date="2000-06-01"), part_row("SPK-003")],
                      [cons_row("SPK-001"), cons_row("SPK-002", status="withdrawn", withdrawal_date="2000-06-01", withdrawal_action="delete_audio"),
                       cons_row("SPK-003", retention_until="2001-01-01")])
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("retention period ended", err)                                    # SPK-003 expired
        self.set_regs([part_row("SPK-001"), part_row("SPK-002", status="withdrawn")],
                      [cons_row("SPK-001"), cons_row("SPK-002", status="withdrawn", withdrawal_date="2000-06-01", withdrawal_action="delete_audio")])
        self.assertEqual(self.check()[0], 0)
        code, out, _ = run("eligible", "--participants", self.p, "--consent", self.c, "--today", TODAY)
        self.assertEqual(out.split(), ["SPK-001"])                                      # withdrawn is never eligible
        # a withdrawal that the participant register ignores is caught
        self.set_regs([part_row("SPK-001")], [cons_row("SPK-001", status="withdrawn", withdrawal_date="2000-06-01", withdrawal_action="exclude_future_runs")])
        self.assertIn("withdrew consent but the participant register still says", self.check()[2])

    def test_validation_errors(self):
        cases = [
            ("bad code", [part_row("Alice")], [cons_row("Alice")], "should look like SPK-001"),
            ("duplicate code", [part_row("SPK-001"), part_row("SPK-001")], [cons_row("SPK-001")], "duplicate participant code"),
            ("phone number", [part_row("SPK-001", identity_record_ref="0123456789")], [cons_row("SPK-001")], "personal contact data"),
            ("email", [part_row("SPK-001", identity_record_ref="a@b.example")], [cons_row("SPK-001")], "personal contact data"),
            ("bad status", [part_row("SPK-001", status="maybe")], [cons_row("SPK-001")], "status must be"),
            ("bad source", [part_row("SPK-001", recruitment_source="boss")], [cons_row("SPK-001")], "recruitment_source"),
            ("no identity ref", [part_row("SPK-001", identity_record_ref="")], [cons_row("SPK-001")], "identity_record_ref"),
            ("eligible without screening", [part_row("SPK-001", independent_recruitment_confirmed="no")], [cons_row("SPK-001")], "independent recruitment"),
            ("consent without recording", [part_row("SPK-001")], [cons_row("SPK-001", recording_and_analysis="no")], "recording_and_analysis=yes"),
            ("no retention", [part_row("SPK-001")], [cons_row("SPK-001", retention_until="")], "retention_until"),
            ("retention before consent", [part_row("SPK-001")], [cons_row("SPK-001", retention_until="1999-01-01")], "must be after consent_date"),
            ("bad date", [part_row("SPK-001")], [cons_row("SPK-001", consent_date="02/01/2000")], "consent_date must be"),
            ("no info sheet", [part_row("SPK-001")], [cons_row("SPK-001", information_sheet_version="")], "information_sheet_version"),
            ("withdrawn no action", [part_row("SPK-001", status="withdrawn")], [cons_row("SPK-001", status="withdrawn", withdrawal_date="2000-03-01")], "withdrawal_action"),
            ("withdrawn no date", [part_row("SPK-001", status="withdrawn")], [cons_row("SPK-001", status="withdrawn", withdrawal_action="delete_all")], "withdrawal_date"),
            ("withdrawal fields without withdrawal", [part_row("SPK-001")], [cons_row("SPK-001", withdrawal_date="2000-03-01")], "withdrawal fields set"),
            ("consent without participant", [part_row("SPK-001")], [cons_row("SPK-001"), cons_row("SPK-009")], "no participant-register row"),
            ("bad yesno", [part_row("SPK-001")], [cons_row("SPK-001", future_experiments="maybe")], "future_experiments must be yes or no"),
        ]
        for name, prows, crows, expect in cases:
            self.set_regs(prows, crows)
            code, _, err = self.check()
            self.assertEqual(code, 1, name)
            self.assertIn(expect, err, name)
        self.set_regs([part_row("SPK-001")], [cons_row("SPK-001", public_release="yes")])
        code, out, _ = self.check()
        self.assertEqual(code, 0)
        self.assertIn("public_release=yes needs its own explicit decision", out)        # allowed but flagged, never silent

    def test_file_problems(self):
        with open(self.p, "w") as f:
            f.write("nope\n")
        self.assertEqual(self.check()[0], 2)
        self.assertEqual(run("check", "--participants", "/nonexistent", "--consent", self.c)[0], 2)
        self.set_regs([part_row("SPK-001")], [cons_row("SPK-001")])
        self.assertEqual(self.check("--allow-any-code")[0], 0)


class Heuristics(unittest.TestCase):
    def test_pii_guard(self):
        for bad in ("a@b.example", "https://x.example/p", "0123456789", "+44 20 7946 0958", "(555) 123-4567"):
            self.assertTrue(cg.pii_suspect(bad), bad)
        for good in ("vault-ref-001", "2002-01-02", "TEST-v1", "open_call", "delete_audio", ""):
            self.assertFalse(cg.pii_suspect(good), good)


class Gates(Base):
    def approvals(self, approved):
        path = os.path.join(self.d, "approvals.csv")
        with open(os.path.join(SCHEMA, "pilot_approvals_template.csv"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        out = lines[:2]
        for ln in lines[2:]:
            item = ln.split(",")[0]
            out.append("%s,approved,rev-a,2001-01-01,doc-%s" % (item, item) if item in approved else ln)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        return path

    def test_template_passes_nothing(self):
        t = os.path.join(SCHEMA, "pilot_approvals_template.csv")
        for g in "ABC":
            code, out, _ = run("gate", g, "--approvals", t)
            self.assertEqual(code, 1, g)
            self.assertIn("NOT PASSED", out)

    def test_gate_requirements(self):
        a = self.approvals({"vocabulary_verified"})
        self.assertEqual(run("gate", "A", "--approvals", a)[0], 0)
        self.assertEqual(run("gate", "C", "--approvals", a)[0], 1)                    # vocabulary alone does not open the pilot
        b = self.approvals({"vocab_subset_verified"} | set(cg.CONSENT_READY))
        self.assertEqual(run("gate", "B", "--approvals", b)[0], 0)
        self.assertEqual(run("gate", "C", "--approvals", b)[0], 1)
        no_dp = self.approvals({"vocab_subset_verified"} | set(cg.CONSENT_READY) - {"data_protection_review"})
        code, out, _ = run("gate", "B", "--approvals", no_dp)
        self.assertEqual(code, 1)
        self.assertIn("data_protection_review", out)                                   # pending data-protection review blocks recording
        c = self.approvals({"vocabulary_verified", "split_rules_reviewed", "rehearsal_passed"} | set(cg.CONSENT_READY))
        self.assertEqual(run("gate", "C", "--approvals", c)[0], 0)

    def test_incomplete_and_invalid_approvals(self):
        path = self.approvals({"vocabulary_verified"})
        with open(path, encoding="utf-8") as f:
            t = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(t.replace("vocabulary_verified,approved,rev-a,2001-01-01,doc-vocabulary_verified", "vocabulary_verified,approved,,2001-01-01,"))
        code, out, _ = run("gate", "A", "--approvals", path)
        self.assertEqual(code, 1)
        self.assertIn("approved rows need approver_id", out)
        with open(path, "w", encoding="utf-8") as f:
            f.write(t + "bogus_item,approved,rev-a,2001-01-01,x\n")
        self.assertEqual(run("gate", "A", "--approvals", path)[0], 2)
        with open(path, "w", encoding="utf-8") as f:
            f.write(t + "vocabulary_verified,approved,rev-a,2001-01-01,x\n")
        self.assertEqual(run("gate", "A", "--approvals", path)[0], 2)                  # duplicate row


@unittest.skipUnless(HAVE_C, "C tools not built")
class DatasetIntegration(Base):
    """consent register -> dataset participants.csv -> C validator / evaluator, on the synthetic fixture."""

    def setUp(self):
        super().setUp()
        self.fx = os.path.join(self.d, "fx")
        subprocess.run([os.path.join(BUILD, "s2w_make_fixture"), self.fx], check=True, stdout=subprocess.DEVNULL)
        spk = ["spkE1", "spkE2", "spkE3", "spkE4", "spkC1", "spkC2", "spkC3", "spkT1", "spkT2", "spkT3"]
        self.set_regs([part_row(s) for s in spk], [cons_row(s) for s in spk])

    def dscheck(self, ds):
        r = subprocess.run([os.path.join(BUILD, "s2w_dataset_check"), ds, "--no-audio"], capture_output=True, text=True)
        return r.returncode, r.stdout

    def test_stamp_withdraw_report_and_rebuild(self):
        pcsv = os.path.join(self.fx, "participants.csv")
        # fixture participants carry consent_version synthetic-v1: align the register so drift is not flagged
        self.set_regs([part_row(s) for s in [r["speaker_id"] for r in cg.read_dataset_csv(pcsv, cg.DATASET_PART_TAG)[1]]],
                      [cons_row(r["speaker_id"], consent_version="synthetic-v1", consent_date="2000-01-01", retention_until="2002-01-01", group_reporting_ok="yes")
                       if r["speaker_id"] != "spkE1" else
                       cons_row("spkE1", consent_version="synthetic-v1", consent_date="2000-01-01", retention_until="2002-01-01", group_reporting_ok="yes",
                                status="withdrawn", withdrawal_date="2000-06-01", withdrawal_action="delete_all")
                       for r in cg.read_dataset_csv(pcsv, cg.DATASET_PART_TAG)[1]])
        write(self.p, cg.PART_TAG, cg.PART_COLS, [part_row(r["speaker_id"], status="withdrawn" if r["speaker_id"] == "spkE1" else "eligible")
                                                   for r in cg.read_dataset_csv(pcsv, cg.DATASET_PART_TAG)[1]])
        stamped = os.path.join(self.d, "participants_stamped.csv")
        code, out, err = run("stamp-consent", "--participants-csv", pcsv, "--consent", self.c, "--out", stamped, "--allow-any-code")
        self.assertEqual(code, 0, err)
        self.assertIn("withdrawn: 1", out)
        self.assertEqual(run("stamp-consent", "--participants-csv", pcsv, "--consent", self.c, "--out", stamped, "--allow-any-code")[0], 2)     # no overwrite
        shutil.copy(stamped, os.path.join(self.fx, "participants.csv"))

        # the C validator (and therefore the evaluator) refuses a dataset that still contains a withdrawn participant
        code, out = self.dscheck(self.fx)
        self.assertEqual(code, 1)
        self.assertIn("WITHDRAWN consent", out)
        cfg = os.path.join(self.d, "s.cfg")
        with open(cfg, "w") as f:
            f.write("#s2w-settings v1\nnorm=mean\n")
        r = subprocess.run([os.path.join(BUILD, "s2w_eval"), "run", "--dataset", self.fx, "--settings", cfg, "--split", "calib", "--out-dir", os.path.join(self.d, "runs"), "--run-id", "w"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "runs", "w")))

        # the report names every affected recording and the action required
        rep = os.path.join(self.d, "withdrawals.csv")
        code, out, _ = run("withdrawals", "--consent", self.c, "--dataset", self.fx, "--out", rep)
        self.assertEqual(code, 1)
        self.assertIn("ACTION REQUIRED", out)
        with open(rep, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 24)                                                  # spkE1 is an enrolment speaker: 8 vocab words x 3 repetitions
        self.assertTrue(all(r["participant_code"] == "spkE1" and r["withdrawal_action"] == "delete_all" and r["metadata_path"].startswith("meta/") for r in rows))

        # a rebuilt eligible dataset excludes them and validates and evaluates again
        out_ds = os.path.join(self.d, "eligible")
        code, out, err = run("build-eligible", "--dataset", self.fx, "--consent", self.c, "--out", out_ds, "--allow-any-code")
        self.assertEqual(code, 0, err)
        code, o = self.dscheck(out_ds)
        self.assertEqual(code, 0, o)
        self.assertNotIn("spkE1", open(os.path.join(out_ds, "recordings.csv"), encoding="utf-8").read())
        self.assertNotIn("spkE1", open(os.path.join(out_ds, "participants.csv"), encoding="utf-8").read())
        self.assertFalse(any("spkE1" in f for f in os.listdir(os.path.join(out_ds, "audio"))))      # their audio is not reachable from the new dataset
        with open(os.path.join(out_ds, "exclusions_report.csv"), encoding="utf-8") as f:
            self.assertIn("spkE1", f.read())
        self.assertNotEqual(run("build-eligible", "--dataset", self.fx, "--consent", self.c, "--out", out_ds, "--allow-any-code")[0], 0)
        r = subprocess.run([os.path.join(BUILD, "s2w_eval"), "run", "--dataset", out_ds, "--settings", cfg, "--split", "calib", "--out-dir", os.path.join(self.d, "runs"), "--run-id", "ok",
                            "--min-group-queries", "1000"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_stamp_refuses_unconsented_and_drift(self):
        pcsv = os.path.join(self.fx, "participants.csv")
        speakers = [r["speaker_id"] for r in cg.read_dataset_csv(pcsv, cg.DATASET_PART_TAG)[1]]
        def regs(**override):
            rows = []
            for s in speakers:
                kw = dict(consent_version="synthetic-v1", consent_date="2000-01-01", retention_until="2002-01-01", group_reporting_ok="yes")
                kw.update(override.get(s, {}))
                rows.append(cons_row(s, **kw))
            write(self.c, cg.CONSENT_TAG, cg.CONSENT_COLS, rows)
        out = os.path.join(self.d, "o.csv")
        regs(spkE2=dict(status="pending", consent_date="", retention_until=""))
        code, _, err = run("stamp-consent", "--participants-csv", pcsv, "--consent", self.c, "--out", out, "--allow-any-code")
        self.assertEqual(code, 1)
        self.assertIn("pending", err)
        self.assertFalse(os.path.exists(out))
        regs(spkE3=dict(consent_version="other-v2"))
        self.assertIn("differs from the register", run("stamp-consent", "--participants-csv", pcsv, "--consent", self.c, "--out", out, "--allow-any-code")[2])
        regs(spkE4=dict(group_reporting_ok="no"))
        self.assertIn("allows group reporting but the register does not", run("stamp-consent", "--participants-csv", pcsv, "--consent", self.c, "--out", out, "--allow-any-code")[2])
        write(self.c, cg.CONSENT_TAG, cg.CONSENT_COLS, [cons_row("spkE1", consent_version="synthetic-v1", consent_date="2000-01-01", retention_until="2002-01-01", group_reporting_ok="yes")])
        self.assertIn("not in the consent register", run("stamp-consent", "--participants-csv", pcsv, "--consent", self.c, "--out", out, "--allow-any-code")[2])
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main(verbosity=1)
