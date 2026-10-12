#!/usr/bin/env python3
"""Consent, eligibility and gate checks for the Sound2Word pilot (see docs/PILOT_RECRUITMENT_AND_CONSENT.md).

The registers are PRIVATE files kept outside the repository. This tool only reads them; it never creates real
consent records, and nothing here proves legal compliance - data-protection review stays a human approval.

  consent_gate.py check            --participants P --consent C [--today YYYY-MM-DD] [--allow-any-code]
  consent_gate.py eligible         --participants P --consent C [--today YYYY-MM-DD] [--allow-any-code]
  consent_gate.py stamp-consent    --participants-csv IN --consent C --out OUT [--allow-any-code]
  consent_gate.py withdrawals      --consent C --dataset DIR --out report.csv
  consent_gate.py build-eligible   --dataset DIR --consent C --out DIR2 [--allow-any-code]
  consent_gate.py gate A|B|C       --approvals F

Exit codes: 0 ok, 1 problems found / not eligible / gate not passed, 2 usage or I/O error.
"""
import argparse
import csv
import datetime
import os
import re
import shutil
import sys

PART_TAG = "#s2w-participant-register v1"
CONSENT_TAG = "#s2w-consent-register v1"
APPROVALS_TAG = "#s2w-pilot-approvals v1"
DATASET_PART_TAG = "#s2w-participants v1"
CODE_RE = re.compile(r"^SPK-[0-9]{3,6}$")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")

PART_COLS = ["participant_code", "identity_record_ref", "recruitment_source", "independent_recruitment_confirmed", "reads_vocabulary_comfortably", "status", "status_date"]
PART_STATUS = {"screened", "eligible", "ineligible", "withdrawn"}
CONSENT_COLS = ["participant_code", "consent_version", "information_sheet_version", "consent_date", "recording_and_analysis", "retention_until", "future_experiments",
                "public_release", "group_reporting_ok", "optional_attributes_ok", "status", "withdrawal_date", "withdrawal_action"]
CONSENT_STATUS = {"pending", "consented", "withdrawn"}
WITHDRAWAL_ACTIONS = {"exclude_future_runs", "delete_audio", "delete_all"}
YESNO = {"yes", "no"}
RECRUIT_SOURCES = {"open_call", "community_group", "personal_network_non_authority", "other_documented"}

APPROVAL_ITEMS = ["vocab_subset_verified", "vocabulary_verified", "consent_materials_approved", "data_protection_review", "storage_arrangement", "retention_defined",
                  "withdrawal_procedure", "split_rules_reviewed", "rehearsal_passed"]
CONSENT_READY = ["consent_materials_approved", "data_protection_review", "storage_arrangement", "retention_defined", "withdrawal_procedure"]
GATES = {
    "A": ["vocabulary_verified"],
    "B": ["vocab_subset_verified"] + CONSENT_READY,
    "C": ["vocabulary_verified"] + CONSENT_READY + ["split_rules_reviewed", "rehearsal_passed"],
}
GATE_TEXT = {"A": "Vocabulary approval (full approved list)", "B": "Small end-to-end rehearsal may start", "C": "Main pilot may start"}


class Problems(Exception):
    pass


def read_tagged(path, tag, cols):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            first = f.readline().rstrip("\r\n")
            if first != tag:
                raise Problems("%s: first line must be %r" % (path, tag))
            rd = csv.DictReader(f)
            if rd.fieldnames != cols:
                raise Problems("%s: header must be exactly %s" % (path, ",".join(cols)))
            rows = list(rd)
    except OSError as e:
        raise Problems("cannot read %s: %s" % (path, e))
    for i, r in enumerate(rows, start=3):
        if None in r or any(v is None for v in r.values()):
            raise Problems("%s line %d: wrong number of columns" % (path, i))
    return rows


def parse_date(s):
    try:
        return datetime.date.fromisoformat(s) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None
    except ValueError:
        return None


def pii_suspect(value):
    """Heuristic guard only: reject values that look like an email, phone number, URL or a free-text name. Not a privacy proof."""
    v = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", value)          # ISO dates are fine
    if "@" in v or "://" in v or re.search(r"\d[\d\s().-]{6,}\d", v):
        return True
    return False


def check_codes(rows, key, strict, errs, label):
    seen = set()
    for r in rows:
        c = r[key]
        if not ID_RE.match(c):
            errs.append("%s: invalid participant code %r" % (label, c))
        elif strict and not CODE_RE.match(c):
            errs.append("%s: participant code %r should look like SPK-001 (random pseudonym; use --allow-any-code only for synthetic tests)" % (label, c))
        if c in seen:
            errs.append("%s: duplicate participant code %r" % (label, c))
        seen.add(c)


def validate(part_rows, cons_rows, today, strict):
    errs, warns = [], []
    check_codes(part_rows, "participant_code", strict, errs, "participant register")
    check_codes(cons_rows, "participant_code", strict, errs, "consent register")
    for label, rows in (("participant register", part_rows), ("consent register", cons_rows)):
        for r in rows:
            for k, v in r.items():
                if pii_suspect(v):
                    errs.append("%s %s: field %s looks like personal contact data; store identities outside the register (use a reference id)" % (label, r["participant_code"], k))
    pc = {r["participant_code"]: r for r in part_rows}
    cc = {r["participant_code"]: r for r in cons_rows}
    for r in part_rows:
        c = r["participant_code"]
        if r["status"] not in PART_STATUS:
            errs.append("participant %s: status must be one of %s" % (c, "/".join(sorted(PART_STATUS))))
        if r["recruitment_source"] not in RECRUIT_SOURCES:
            errs.append("participant %s: recruitment_source must be one of %s" % (c, "/".join(sorted(RECRUIT_SOURCES))))
        for k in ("independent_recruitment_confirmed", "reads_vocabulary_comfortably"):
            if r[k] not in YESNO:
                errs.append("participant %s: %s must be yes or no" % (c, k))
        if not r["identity_record_ref"]:
            errs.append("participant %s: identity_record_ref (a pointer into the restricted identity store) is required" % c)
        if r["status"] == "eligible" and (r["independent_recruitment_confirmed"] != "yes" or r["reads_vocabulary_comfortably"] != "yes"):
            errs.append("participant %s: cannot be eligible without confirmed independent recruitment and screening" % c)
        if r["status_date"] and not parse_date(r["status_date"]):
            errs.append("participant %s: status_date must be YYYY-MM-DD" % c)
    for r in cons_rows:
        c = r["participant_code"]
        st = r["status"]
        if st not in CONSENT_STATUS:
            errs.append("consent %s: status must be one of %s" % (c, "/".join(sorted(CONSENT_STATUS))))
            continue
        for k in ("recording_and_analysis", "future_experiments", "public_release", "group_reporting_ok", "optional_attributes_ok"):
            if r[k] not in YESNO:
                errs.append("consent %s: %s must be yes or no" % (c, k))
        if st in ("consented", "withdrawn"):
            for k in ("consent_version", "information_sheet_version"):
                if not ID_RE.match(r[k]):
                    errs.append("consent %s: %s required (matches [A-Za-z0-9._-]{1,40})" % (c, k))
            cd, rd = parse_date(r["consent_date"]), parse_date(r["retention_until"])
            if not cd:
                errs.append("consent %s: consent_date must be YYYY-MM-DD" % c)
            if not rd:
                errs.append("consent %s: retention_until must be YYYY-MM-DD (a retention period must be defined)" % c)
            elif cd and rd <= cd:
                errs.append("consent %s: retention_until must be after consent_date" % c)
            if r["recording_and_analysis"] != "yes" and st == "consented":
                errs.append("consent %s: consented requires recording_and_analysis=yes" % c)
            if r["public_release"] == "yes":
                warns.append("consent %s: public_release=yes needs its own explicit decision and approval before any release" % c)
            if st == "consented" and rd and rd < today:
                errs.append("consent %s: retention period ended on %s: delete the data or re-consent" % (c, r["retention_until"]))
        if st == "withdrawn":
            wd = parse_date(r["withdrawal_date"])
            if not wd:
                errs.append("consent %s: withdrawn requires withdrawal_date" % c)
            elif parse_date(r["consent_date"]) and wd < parse_date(r["consent_date"]):
                errs.append("consent %s: withdrawal_date is before consent_date" % c)
            if r["withdrawal_action"] not in WITHDRAWAL_ACTIONS:
                errs.append("consent %s: withdrawal_action must be one of %s" % (c, "/".join(sorted(WITHDRAWAL_ACTIONS))))
        elif r["withdrawal_date"] or r["withdrawal_action"]:
            errs.append("consent %s: withdrawal fields set but status is %s" % (c, st))
        if st == "pending" and r["consent_date"]:
            warns.append("consent %s: pending but has a consent_date" % c)
    for c in cc:
        if c not in pc:
            errs.append("consent register has %s with no participant-register row" % c)
    for c, r in pc.items():
        crow = cc.get(c)
        if r["status"] == "eligible":
            if not crow or crow["status"] != "consented":
                errs.append("participant %s is marked eligible without a completed consent record" % c)
        if crow and crow["status"] == "withdrawn" and r["status"] != "withdrawn":
            errs.append("participant %s withdrew consent but the participant register still says %s" % (c, r["status"]))
        if r["status"] == "withdrawn" and (not crow or crow["status"] != "withdrawn"):
            errs.append("participant %s is withdrawn in the participant register but not in the consent register" % c)
    return errs, warns


def eligible_codes(part_rows, cons_rows, today):
    cc = {r["participant_code"]: r for r in cons_rows}
    out = []
    for r in part_rows:
        c = cc.get(r["participant_code"])
        if r["status"] == "eligible" and c and c["status"] == "consented" and c["recording_and_analysis"] == "yes":
            rd = parse_date(c["retention_until"])
            if rd and rd >= today:
                out.append(r["participant_code"])
    return sorted(out)


def load_registers(a):
    part = read_tagged(a.participants, PART_TAG, PART_COLS)
    cons = read_tagged(a.consent, CONSENT_TAG, CONSENT_COLS)
    return part, cons


def today_of(a):
    if getattr(a, "today", None):
        d = parse_date(a.today)
        if not d:
            sys.exit("error: --today must be YYYY-MM-DD")
        return d
    return datetime.date.today()


# ---------------------------------------------------------------- dataset helpers --------------------
def read_dataset_csv(path, tag):
    with open(path, encoding="utf-8", newline="") as f:
        if f.readline().rstrip("\r\n") != tag:
            raise Problems("%s: unexpected first line" % path)
        rd = csv.DictReader(f)
        return rd.fieldnames, list(rd)


def write_dataset_csv(path, tag, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(tag + "\n")
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def cmd_check(a):
    today = today_of(a)
    part, cons = load_registers(a)
    errs, warns = validate(part, cons, today, not a.allow_any_code)
    for w in warns:
        print("WARN:", w)
    for e in errs:
        print("ERROR:", e, file=sys.stderr)
    print("participants=%d consent_rows=%d eligible_now=%d result=%s errors=%d warnings=%d" % (len(part), len(cons), len(eligible_codes(part, cons, today)), "INVALID" if errs else "VALID", len(errs), len(warns)))
    return 1 if errs else 0


def cmd_eligible(a):
    today = today_of(a)
    part, cons = load_registers(a)
    errs, _ = validate(part, cons, today, not a.allow_any_code)
    if errs:
        for e in errs:
            print("ERROR:", e, file=sys.stderr)
        print("registers are invalid: no one is reported eligible", file=sys.stderr)
        return 1
    for c in eligible_codes(part, cons, today):
        print(c)
    return 0


def cmd_stamp(a):
    fields, rows = read_dataset_csv(a.participants_csv, DATASET_PART_TAG)
    cons = {r["participant_code"]: r for r in read_tagged(a.consent, CONSENT_TAG, CONSENT_COLS)}
    errs = []
    for r in rows:
        c = cons.get(r["speaker_id"])
        if not a.allow_any_code and not CODE_RE.match(r["speaker_id"]):
            errs.append("speaker %s does not look like SPK-001" % r["speaker_id"])
        if c is None:
            errs.append("speaker %s is not in the consent register" % r["speaker_id"])
            continue
        if c["status"] not in ("consented", "withdrawn"):
            errs.append("speaker %s has consent status %r (must be consented or withdrawn to stamp)" % (r["speaker_id"], c["status"]))
            continue
        if r.get("consent_version") and r["consent_version"] != c["consent_version"]:
            errs.append("speaker %s: dataset consent_version %r differs from the register's %r" % (r["speaker_id"], r["consent_version"], c["consent_version"]))
        if r.get("consent_date") and r["consent_date"] != c["consent_date"]:
            errs.append("speaker %s: dataset consent_date differs from the register" % r["speaker_id"])
        if r.get("group_reporting_ok") == "yes" and c["group_reporting_ok"] != "yes":
            errs.append("speaker %s: dataset allows group reporting but the register does not" % r["speaker_id"])
        r["consent_status"] = c["status"]
    if errs:
        for e in errs:
            print("ERROR:", e, file=sys.stderr)
        return 1
    if os.path.exists(a.out):
        print("error: %s exists; refusing to overwrite" % a.out, file=sys.stderr)
        return 2
    out_fields = fields if "consent_status" in fields else fields + ["consent_status"]
    write_dataset_csv(a.out, DATASET_PART_TAG, out_fields, rows)
    print("stamped %d speakers into %s (withdrawn: %d)" % (len(rows), a.out, sum(1 for r in rows if r["consent_status"] == "withdrawn")))
    return 0


def affected(dataset, cons_rows):
    withdrawn = {r["participant_code"]: r for r in cons_rows if r["status"] == "withdrawn"}
    _, recs = read_dataset_csv(os.path.join(dataset, "recordings.csv"), "#s2w-recordings v1")
    return withdrawn, [r for r in recs if r["speaker_id"] in withdrawn]


def cmd_withdrawals(a):
    cons = read_tagged(a.consent, CONSENT_TAG, CONSENT_COLS)
    withdrawn, recs = affected(a.dataset, cons)
    if os.path.exists(a.out):
        print("error: %s exists; refusing to overwrite" % a.out, file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["participant_code", "recording_id", "original_path", "derived_path", "metadata_path", "withdrawal_action", "withdrawal_date", "status_for_evaluation"])
        for r in recs:
            c = withdrawn[r["speaker_id"]]
            w.writerow([r["speaker_id"], r["recording_id"], r["path"], "derived16k/%s.wav" % r["recording_id"], "meta/%s.json" % r["recording_id"],
                        c["withdrawal_action"], c["withdrawal_date"], "BLOCKED: dataset validation fails until these rows are removed (build-eligible)"])
    print("withdrawn participants: %d; affected recordings in this dataset: %d; report: %s" % (len(withdrawn), len(recs), a.out))
    if recs:
        print("ACTION REQUIRED: remove these recordings from any dataset used for evaluation and apply each participant's withdrawal_action to the stored files.")
    return 1 if recs else 0


def cmd_build_eligible(a):
    cons = {r["participant_code"]: r for r in read_tagged(a.consent, CONSENT_TAG, CONSENT_COLS)}
    if os.path.exists(a.out):
        print("error: %s exists; refusing to overwrite" % a.out, file=sys.stderr)
        return 2
    pf, prow = read_dataset_csv(os.path.join(a.dataset, "participants.csv"), DATASET_PART_TAG)
    rf, rrow = read_dataset_csv(os.path.join(a.dataset, "recordings.csv"), "#s2w-recordings v1")

    def ok(code):
        c = cons.get(code)
        return bool(c) and c["status"] == "consented" and c["recording_and_analysis"] == "yes"
    keep_p = [r for r in prow if ok(r["speaker_id"])]
    drop_codes = sorted({r["speaker_id"] for r in prow if not ok(r["speaker_id"])} | {r["speaker_id"] for r in rrow if not ok(r["speaker_id"])})
    keep_r = [r for r in rrow if ok(r["speaker_id"])]
    drop_r = [r for r in rrow if not ok(r["speaker_id"])]
    os.makedirs(a.out)
    for r in keep_p:
        r["consent_status"] = "consented"
    out_pf = pf if "consent_status" in pf else pf + ["consent_status"]
    write_dataset_csv(os.path.join(a.out, "participants.csv"), DATASET_PART_TAG, out_pf, keep_p)
    write_dataset_csv(os.path.join(a.out, "recordings.csv"), "#s2w-recordings v1", rf, keep_r)
    shutil.copy(os.path.join(a.dataset, "wordlist.csv"), os.path.join(a.out, "wordlist.csv"))
    with open(os.path.join(a.dataset, "dataset_meta.txt"), encoding="utf-8") as f:
        meta = f.read()
    meta = re.sub(r"^dataset_version=(.*)$", lambda m: "dataset_version=%s.elig" % m.group(1), meta, flags=re.M)
    with open(os.path.join(a.out, "dataset_meta.txt"), "w", encoding="utf-8") as f:
        f.write(meta)
    for r in keep_r:                                   # per-file links so excluded audio is not reachable from the new dataset
        dst = os.path.join(a.out, r["path"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.symlink(os.path.abspath(os.path.join(a.dataset, r["path"])), dst)
    with open(os.path.join(a.out, "exclusions_report.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["participant_code", "reason", "recordings_excluded"])
        for c in drop_codes:
            st = cons.get(c, {}).get("status", "not_in_register")
            w.writerow([c, "consent status: %s" % st, sum(1 for r in drop_r if r["speaker_id"] == c)])
    print("eligible dataset written to %s: %d recordings kept, %d excluded (%d participants); validate it with s2w_dataset_check" % (a.out, len(keep_r), len(drop_r), len(drop_codes)))
    return 0


def cmd_gate(a):
    try:
        rows = read_tagged(a.approvals, APPROVALS_TAG, ["item", "status", "approver_id", "date", "reference"])
    except Problems as e:
        print("error:", e, file=sys.stderr)
        return 2
    by = {}
    for r in rows:
        if r["item"] not in APPROVAL_ITEMS:
            print("error: unknown approval item %r" % r["item"], file=sys.stderr)
            return 2
        if r["item"] in by:
            print("error: duplicate approval item %r" % r["item"], file=sys.stderr)
            return 2
        by[r["item"]] = r
    need = GATES[a.gate]
    fails = []
    print("Gate %s - %s" % (a.gate, GATE_TEXT[a.gate]))
    for item in need:
        r = by.get(item)
        if r is None:
            state, why = "MISSING", "no row in the approvals file"
        elif r["status"] != "approved":
            state, why = "PENDING", "status is %r" % r["status"]
        elif not ID_RE.match(r["approver_id"]) or not parse_date(r["date"]) or not r["reference"]:
            state, why = "INCOMPLETE", "approved rows need approver_id, date (YYYY-MM-DD) and a reference to the evidence"
        else:
            state, why = "approved", "by %s on %s (%s)" % (r["approver_id"], r["date"], r["reference"])
        print("  [%s] %s - %s" % ("x" if state == "approved" else " ", item, why))
        if state != "approved":
            fails.append(item)
    print("result=%s" % ("PASS" if not fails else "NOT PASSED (%d item(s) outstanding)" % len(fails)))
    return 1 if fails else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "eligible"):
        s = sub.add_parser(name); s.add_argument("--participants", required=True); s.add_argument("--consent", required=True)
        s.add_argument("--today"); s.add_argument("--allow-any-code", action="store_true")
    s = sub.add_parser("stamp-consent"); s.add_argument("--participants-csv", required=True); s.add_argument("--consent", required=True); s.add_argument("--out", required=True)
    s.add_argument("--allow-any-code", action="store_true")
    s = sub.add_parser("withdrawals"); s.add_argument("--consent", required=True); s.add_argument("--dataset", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("build-eligible"); s.add_argument("--dataset", required=True); s.add_argument("--consent", required=True); s.add_argument("--out", required=True)
    s.add_argument("--allow-any-code", action="store_true")
    s = sub.add_parser("gate"); s.add_argument("gate", choices=["A", "B", "C"]); s.add_argument("--approvals", required=True)
    a = p.parse_args(argv)
    try:
        fn = {"check": cmd_check, "eligible": cmd_eligible, "stamp-consent": cmd_stamp, "withdrawals": cmd_withdrawals, "build-eligible": cmd_build_eligible, "gate": cmd_gate}[a.cmd]
        return fn(a)
    except Problems as e:
        print("error:", e, file=sys.stderr)
        return 2
    except OSError as e:
        print("error:", e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
