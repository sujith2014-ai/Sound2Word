#!/usr/bin/env python3
"""Pilot planning: condition matrix spec, speaker allocation, prompt order, sample-size budget.
No Malayalam vocabulary is created here and no hardware is needed. See docs/PILOT_DATA_COLLECTION.md.

  pilot_plan.py write-spec OUTDIR                       default pilot_conditions.csv + pilot_matrix.csv
  pilot_plan.py check   --conditions F --matrix F       validate both against the dataset schema rules
  pilot_plan.py plan    --speakers F --conditions F --matrix F --splits 8,4,4 --seed N --out allocation.csv
                        --consent-register F --participants-register F [--with all|ID,ID]
  pilot_plan.py prompts --wordlist F --conditions F --allocation F --speaker ID --out prompts.csv [--with-labels]
                        --consent-register F --participants-register F
  pilot_plan.py budget  --conditions F --matrix F --splits 8,4,4 --wordlist F [--with all|ID,ID]
  pilot_plan.py views   --pool DIR --allocation F --out-dir OUT      # speaker-independent AND personalised datasets from one pool

Default plan = the single-device CLEAN pilot (C0, C0-R). Optional conditions (noise, distance, room) are switched on with --with
once their equipment and procedure exist; the second device (C0-DEVB) is disabled until gap G2 is closed. plan/prompts refuse any
participant who is not consent-eligible (synthetic software tests may use --synthetic-no-consent-check).

Every identifier it emits satisfies the dataset schema (ids [A-Za-z0-9._-]{1,40}); conditions map onto the
recordings.csv fields environment / noise_condition / device_id; repetition numbers never collide within a
(speaker, session, word) because the dataset key ignores the condition.
"""
import argparse
import csv
import hashlib
import itertools
import math
import os
import random
import re
import shutil
import sys

ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
COND_TAG = "#s2w-pilot-conditions v1"
MATRIX_TAG = "#s2w-pilot-matrix v1"
COND_COLS = ["condition_id", "environment", "noise_condition", "device_id", "qa_limits", "session", "repetitions", "include_oov", "role", "optional", "enabled", "requires", "simultaneous_with", "recipe"]
REQUIRES = {"none", "tape_measure", "documented_room", "loudspeaker", "loudspeaker+spl_meter", "second_device"}
MATRIX_COLS = ["cell_id", "enrol_set", "enrol_conditions", "query_condition", "category"]
ROLES = {"baseline", "repeat", "noise", "distance", "room", "device"}


# ---------------------------------------------------------------- default spec -----------------------
DEFAULT_CONDITIONS = [
    # id, env, noise_condition, device, qa, session, reps, oov, role, optional, enabled, requires, simultaneous_with, recipe
    ("C0", "clean", "", "DEV-A", "clean", "S1", 3, "yes", "baseline", "no", "yes", "none", "",
     "Quiet room (background below ~35 dBA if measurable). The single primary device at about 20 cm, off-axis. Gain fixed; automatic gain control, noise suppression and echo cancellation OFF."),
    ("C0-DEVB", "clean", "", "DEV-B", "clean", "S1", 3, "yes", "device", "yes", "no", "second_device", "C0",
     "DEFERRED (gap G2). A second recorder capturing the SAME utterances at the same time needs a shared utterance id and pairing tests first. Disabled for this pilot."),
    ("C0-R", "clean", "", "DEV-A", "clean", "S2", 1, "yes", "repeat", "no", "yes", "none", "",
     "Same setup as C0, recorded again in session 2 (ideally at least a day later; if not possible, after a real break with the setup redone). Supports session variability and the personalised queries. First block of session 2."),
    ("N-FAN", "noisy", "real:fan-60dBA", "DEV-A", "noisy", "S2", 1, "yes", "noise", "yes", "yes", "loudspeaker+spl_meter", "",
     "OPTIONAL until a loudspeaker and SPL meter are available and the procedure is repeatable. Steady fan/air-conditioner recording played from a loudspeaker, 60 dBA measured at the speaker's head position. Do not specify or claim a level without a measurement."),
    ("N-STR", "noisy", "real:street-65dBA", "DEV-A", "noisy", "S2", 1, "yes", "noise", "yes", "yes", "loudspeaker+spl_meter", "",
     "OPTIONAL (as N-FAN). Street/traffic recording played from a loudspeaker; 65 dBA at the speaker's head position."),
    ("N-BAB", "noisy", "real:babble-65dBA", "DEV-A", "noisy", "S2", 1, "yes", "noise", "yes", "yes", "loudspeaker+spl_meter", "",
     "OPTIONAL (as N-FAN). Multi-talker babble recording played from a loudspeaker; 65 dBA at the speaker's head position."),
    ("D-100", "noisy", "real:dist-100cm", "DEV-A", "noisy", "S2", 1, "yes", "distance", "yes", "yes", "tape_measure", "",
     "OPTIONAL until the procedure is documented. Quiet room, device 100 cm from the mouth (otherwise as C0). Record the real level and SNR from QA."),
    ("R-HARD", "noisy", "real:room-hard", "DEV-A", "noisy", "S2", 1, "yes", "room", "yes", "yes", "documented_room", "",
     "OPTIONAL until a suitable room is identified and described. Quiet but hard-surfaced reverberant room, device at 20 cm. Describe the room; do not assume an RT60."),
]

NOISY_ENROL = ["N-FAN", "N-STR", "N-BAB"]
ENROL_SETS = [("E0", ["C0"]), ("E-FAN", ["N-FAN"]), ("E-STR", ["N-STR"]), ("E-BAB", ["N-BAB"]),
              ("EM-FAN", ["C0", "N-FAN"]), ("EM-STR", ["C0", "N-STR"]), ("EM-BAB", ["C0", "N-BAB"]), ("EM-ALL", ["C0", "N-FAN", "N-STR", "N-BAB"])]
QUERIES = ["C0", "C0-R", "N-FAN", "N-STR", "N-BAB", "D-100", "R-HARD"]
DEVICE_QUERY_SETS = {"E0"}      # the second-device query is only paired with clean enrolment in the default matrix


def cell_category(enrol_conds, query, conds):
    """Deterministic label for a matrix cell; used both to write and to verify the spec."""
    e = list(enrol_conds)
    qrole = conds[query]["role"]
    clean_q = qrole in ("baseline", "repeat")
    noisy = [c for c in e if c != "C0"]
    if e == ["C0"]:
        if query == "C0":
            return "baseline"
        if qrole == "device":
            return "device_mismatch"
        if qrole == "repeat":
            return "session_repeat"
        return "clean_enrol_loss"
    if qrole == "device":
        return "other_device"
    if len(noisy) >= 2 and "C0" in e:
        return "mixed_all_clean_cost" if clean_q else "mixed_all"
    if "C0" in e:      # clean + one noisy condition
        if clean_q:
            return "mixed_clean_cost"
        return "mixed_matched" if query == noisy[0] else "mixed_mismatched"
    if clean_q:        # noise-only enrolment
        return "noise_only_clean_cost"
    return "noise_only_matched" if query == e[0] else "noise_only_mismatched"


def read_csv(path, tag):
    with open(path, encoding="utf-8", newline="") as f:
        first = f.readline().rstrip("\r\n")
        if first != tag:
            raise ValueError("%s: expected first line %r" % (path, tag))
        rows = list(csv.DictReader(f))
    return rows


def load_conditions(path):
    rows = read_csv(path, COND_TAG)
    return rows, {r["condition_id"]: r for r in rows}


def write_spec(outdir):
    os.makedirs(outdir, exist_ok=True)
    cpath, mpath = os.path.join(outdir, "pilot_conditions.csv"), os.path.join(outdir, "pilot_matrix.csv")
    for p in (cpath, mpath):
        if os.path.exists(p):
            sys.exit("error: %s exists; refusing to overwrite" % p)
    with open(cpath, "w", encoding="utf-8", newline="") as f:
        f.write(COND_TAG + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COND_COLS)
        for r in DEFAULT_CONDITIONS:
            w.writerow(r)
    _, conds = load_conditions(cpath)
    with open(mpath, "w", encoding="utf-8", newline="") as f:
        f.write(MATRIX_TAG + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(MATRIX_COLS)
        for (sid, ec), q in itertools.product(ENROL_SETS, QUERIES + ["C0-DEVB"]):
            if q == "C0-DEVB" and (sid not in DEVICE_QUERY_SETS or "C0-DEVB" not in conds):
                continue
            w.writerow(["%s__%s" % (sid, q), sid, "+".join(ec), q, cell_category(ec, q, conds)])
    print("wrote %s and %s" % (cpath, mpath))


# ---------------------------------------------------------------- check ------------------------------
def check(cpath, mpath):
    errs, warns = [], []
    try:
        rows, conds = load_conditions(cpath)
    except (ValueError, OSError) as e:
        return [str(e)], []
    if rows and list(rows[0].keys()) != COND_COLS:
        errs.append("conditions: header must be exactly %s" % ",".join(COND_COLS))
        return errs, warns
    if len(conds) != len(rows):
        errs.append("conditions: duplicate condition_id")
    for r in rows:
        cid = r["condition_id"]
        where = "condition %s" % cid
        if not ID_RE.match(cid):
            errs.append("%s: invalid id" % where)
        if r["environment"] not in ("clean", "noisy"):
            errs.append("%s: environment must be clean or noisy (the dataset enumeration)" % where)
        nc = r["noise_condition"]
        if r["environment"] == "clean" and nc:
            errs.append("%s: clean conditions must have an empty noise_condition" % where)
        if r["environment"] == "noisy":
            if not nc:
                errs.append("%s: noisy conditions need a noise_condition" % where)
            elif not nc.startswith("real:"):
                errs.append("%s: recorded conditions must start with 'real:' ('sim:' is reserved for simulated data)" % where)
        if len(nc) > 40 or any(ord(ch) < 32 or ord(ch) > 126 or ch in ',"' for ch in nc):
            errs.append("%s: noise_condition must be <= 40 printable ASCII chars without commas or quotes" % where)
        if not ID_RE.match(r["device_id"]):
            errs.append("%s: invalid device_id" % where)
        if r["qa_limits"] not in ("clean", "noisy") or r["qa_limits"] != r["environment"]:
            errs.append("%s: qa_limits must equal the environment (clean/noisy)" % where)
        if not re.match(r"^S[1-9]$", r["session"]):
            errs.append("%s: session must be S1..S9" % where)
        if not r["repetitions"].isdigit() or not 1 <= int(r["repetitions"]) <= 10:
            errs.append("%s: repetitions must be 1..10" % where)
        if r["include_oov"] not in ("yes", "no"):
            errs.append("%s: include_oov must be yes or no" % where)
        if r["role"] not in ROLES:
            errs.append("%s: role must be one of %s" % (where, "/".join(sorted(ROLES))))
        if r["optional"] not in ("yes", "no"):
            errs.append("%s: optional must be yes or no" % where)
        if r["enabled"] not in ("yes", "no"):
            errs.append("%s: enabled must be yes or no" % where)
        if r["requires"] not in REQUIRES:
            errs.append("%s: requires must be one of %s" % (where, "/".join(sorted(REQUIRES))))
        if r["optional"] == "no" and r["enabled"] == "no":
            errs.append("%s: a non-optional condition cannot be disabled" % where)
        sw = r["simultaneous_with"]
        if sw and sw not in conds:
            errs.append("%s: simultaneous_with refers to an unknown condition" % where)
        if sw and r["enabled"] == "yes":
            errs.append("%s records the same utterances as %s on another device: it cannot be enabled until gap G2 (shared utterance id + pairing tests) is closed" % (where, sw))
    if "C0" not in conds or conds["C0"]["role"] != "baseline" or conds["C0"]["environment"] != "clean":
        errs.append("conditions: C0 (clean baseline) is required")
    if not errs and sum(1 for r in rows if r["role"] == "baseline") != 1:
        errs.append("conditions: exactly one baseline condition")
    inc = [r for r in rows if r["include_oov"] == "yes"]
    if rows and not inc:
        errs.append("conditions: at least one condition must include unknown (oov) words")

    try:
        mrows = read_csv(mpath, MATRIX_TAG)
    except (ValueError, OSError) as e:
        return errs + [str(e)], warns
    if mrows and list(mrows[0].keys()) != MATRIX_COLS:
        errs.append("matrix: header must be exactly %s" % ",".join(MATRIX_COLS))
        return errs, warns
    seen = set()
    cats = {}
    for m in mrows:
        where = "matrix cell %s" % m["cell_id"]
        if m["cell_id"] in seen:
            errs.append("%s: duplicate cell_id" % where)
        seen.add(m["cell_id"])
        ec = m["enrol_conditions"].split("+")
        bad = [c for c in ec + [m["query_condition"]] if c not in conds]
        if bad:
            errs.append("%s: unknown condition(s) %s" % (where, ",".join(bad)))
            continue
        for c in ec:
            if conds[c]["role"] == "device":
                errs.append("%s: device-only conditions cannot be enrolment conditions in v1" % where)
        want = cell_category(ec, m["query_condition"], conds)
        if m["category"] != want:
            errs.append("%s: category %r should be %r" % (where, m["category"], want))
        cats.setdefault(m["category"], []).append(m)
    if "baseline" not in cats:
        errs.append("matrix: the baseline cell (clean enrolment, clean query) is required")
    for q in [r["condition_id"] for r in rows if r["role"] in ("noise", "distance", "room")]:
        if not any(m["query_condition"] == q and m["category"] == "clean_enrol_loss" for m in mrows):
            errs.append("matrix: no clean-enrolment cell for query condition %s (needed to measure the loss)" % q)
    for q in [r["condition_id"] for r in rows if r["role"] == "noise"]:
        for need in ("noise_only_matched", "mixed_matched"):
            if not any(m["query_condition"] == q and m["category"] == need for m in mrows):
                warns.append("matrix: condition %s has no %s cell" % (q, need))
    for need in ("noise_only_mismatched", "mixed_mismatched", "noise_only_clean_cost", "mixed_clean_cost"):
        if need not in cats:
            errs.append("matrix: no %s cell: mismatched pairs and clean-query cost must be measured" % need)
    return errs, warns


# ---------------------------------------------------------------- activation ---------------------------
def parse_with(spec, rows):
    """--with all | id,id : which OPTIONAL enabled conditions are switched on. Mandatory enabled conditions are always active."""
    if not spec:
        return set()
    by = {r["condition_id"]: r for r in rows}
    if spec == "all":
        return {r["condition_id"] for r in rows if r["optional"] == "yes" and r["enabled"] == "yes"}
    ids = set(spec.split(","))
    for i in ids:
        if i not in by:
            sys.exit("error: --with: unknown condition %r" % i)
        if by[i]["enabled"] != "yes":
            sys.exit("error: --with: condition %s is disabled in the spec (%s)" % (i, by[i]["requires"]))
        if by[i]["optional"] != "yes":
            sys.exit("error: --with: %s is not optional (it is always active)" % i)
    return ids


def active_rows(rows, with_ids):
    return [r for r in rows if r["enabled"] == "yes" and (r["optional"] == "no" or r["condition_id"] in with_ids)]


# ---------------------------------------------------------------- consent ---------------------------
def require_eligible(a, speakers):
    """plan/prompts refuse participants who are not consent-eligible, unless the explicitly named synthetic escape hatch is used."""
    if getattr(a, "synthetic_no_consent_check", False):
        print("WARNING: consent check skipped (--synthetic-no-consent-check): valid for synthetic software tests ONLY", file=sys.stderr)
        return
    if not (a.consent_register and a.participants_register):
        sys.exit("error: --consent-register and --participants-register are required: no participant may be allocated or prompted without a verified consent record "
                 "(synthetic software tests may use --synthetic-no-consent-check)")
    import datetime
    import consent_gate as cg
    try:
        part = cg.read_tagged(a.participants_register, cg.PART_TAG, cg.PART_COLS)
        cons = cg.read_tagged(a.consent_register, cg.CONSENT_TAG, cg.CONSENT_COLS)
    except cg.Problems as e:
        sys.exit("error: %s" % e)
    today = datetime.date.fromisoformat(a.today) if getattr(a, "today", None) else datetime.date.today()
    errs, _ = cg.validate(part, cons, today, True)
    if errs:
        sys.exit("error: consent registers are invalid: " + "; ".join(errs[:3]))
    ok = set(cg.eligible_codes(part, cons, today))
    bad = sorted(set(speakers) - ok)
    if bad:
        sys.exit("error: not eligible for recording (consent incomplete, withdrawn, expired or not screened): " + ", ".join(bad))


# ---------------------------------------------------------------- allocation -------------------------
def seed_hex(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:8]


def block_order(conds_rows, session):
    return [r["condition_id"] for r in conds_rows if r["session"] == session and r["simultaneous_with"] == ""]


def assign_splits(speakers, sizes, seed):
    """Deterministic, stratified dealing: shuffle within each stratum (seeded), interleave strata, then give each
    speaker to the split with the largest remaining share of its quota."""
    rnd = random.Random(seed)
    strata = {}
    for sid, st in speakers:
        strata.setdefault(st, []).append(sid)
    for k in sorted(strata):
        rnd.shuffle(strata[k])
    order = []
    pools = [strata[k] for k in sorted(strata)]
    while any(pools):
        for p in pools:
            if p:
                order.append(p.pop(0))
    stratum_of = dict(speakers)
    quota = list(sizes)
    names = ["enrol", "calib", "test"]
    per_stratum = {k: [0, 0, 0] for k in strata}
    out = {}
    for sid in order:
        st = stratum_of[sid]
        best, bs = None, None
        for i in range(3):
            if quota[i] == 0:
                continue
            score = (quota[i] / sizes[i], -per_stratum[st][i])    # remaining share first, then least same-stratum already in this split
            if bs is None or score > bs:
                best, bs = i, score
        out[sid] = names[best]
        quota[best] -= 1
        per_stratum[st][best] += 1
    return out


def cmd_plan(a):
    sizes = [int(x) for x in a.splits.split(",")]
    if len(sizes) != 3 or any(s < 1 for s in sizes):
        sys.exit("error: --splits needs three positive integers: enrol,calib,test")
    with open(a.speakers, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        if "speaker_id" not in (rd.fieldnames or []):
            sys.exit("error: speakers file needs a speaker_id column (optional: stratum)")
        speakers = [(r["speaker_id"].strip(), (r.get("stratum") or "").strip() or "-") for r in rd]
    ids = [s for s, _ in speakers]
    if len(set(ids)) != len(ids):
        sys.exit("error: duplicate speaker_id")
    for s in ids:
        if not ID_RE.match(s):
            sys.exit("error: invalid speaker_id %r (use pseudonymous ids, e.g. SP001)" % s)
    if len(ids) != sum(sizes):
        sys.exit("error: %d speakers but splits sum to %d" % (len(ids), sum(sizes)))
    if not 10 <= len(ids) <= 20:
        print("note: the pilot target is 10-20 speakers (got %d)" % len(ids), file=sys.stderr)
    if os.path.exists(a.out):
        sys.exit("error: %s exists; refusing to overwrite" % a.out)
    all_rows, all_conds = load_conditions(a.conditions)
    errs, _ = check(a.conditions, a.matrix) if a.matrix else ([], [])
    if errs:
        sys.exit("error: spec invalid: " + "; ".join(errs[:3]))
    require_eligible(a, ids)
    rows = active_rows(all_rows, parse_with(a.with_, all_rows))
    conds = {r["condition_id"]: r for r in rows}
    if "C0" not in conds:
        sys.exit("error: the clean baseline C0 must be active")
    split = assign_splits(speakers, sizes, a.seed)
    stratum_of = dict(speakers)
    s2 = block_order(rows, "S2")
    s2_first = [c for c in s2 if conds[c]["role"] in ("baseline", "repeat")]     # clean blocks always open the session
    s2_noisy = [c for c in s2 if conds[c]["role"] not in ("baseline", "repeat")]
    base = list(s2_noisy)
    random.Random(a.seed).shuffle(base)
    counters = {"enrol": 0, "calib": 0, "test": 0}
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["speaker_id", "split", "stratum", "s1_blocks", "s2_blocks", "prompt_seed_s1", "prompt_seed_s2", "personalised_enrol_session", "personalised_query_session", "personalised_group"])
        for sid in sorted(ids):
            k = counters[split[sid]]
            counters[split[sid]] += 1
            rot = base[k % len(base):] + base[:k % len(base)] if base else []
            w.writerow([sid, split[sid], stratum_of[sid], "+".join(block_order(rows, "S1")), "+".join(s2_first + rot),
                        seed_hex(a.seed, sid, "S1"), seed_hex(a.seed, sid, "S2"), "S1", "S2",
                        "test_participant" if split[sid] == "test" else "calibration_participant"])
    print("wrote %s (%d speakers: %s)" % (a.out, len(ids), ", ".join("%s=%d" % (n, c) for n, c in counters.items())))


# ---------------------------------------------------------------- prompts ----------------------------
def read_wordlist(path):
    with open(path, encoding="utf-8", newline="") as f:
        if f.readline().rstrip("\r\n") != "#s2w-wordlist v1":
            sys.exit("error: %s is not a '#s2w-wordlist v1' file" % path)
        return list(csv.DictReader(f))


def rounds(words, n, rnd):
    out, prev_last = [], None
    for _ in range(n):
        perm = words[:]
        rnd.shuffle(perm)
        if prev_last is not None and perm[0] == prev_last and len(perm) > 1:
            perm[0], perm[1] = perm[1], perm[0]
        out.append(perm)
        prev_last = perm[-1]
    return out


def cmd_prompts(a):
    rows, conds = load_conditions(a.conditions)
    require_eligible(a, [a.speaker])
    words = read_wordlist(a.wordlist)
    vocab = [w["word_id"] for w in words if w["role"] == "vocab"]
    oov = [w["word_id"] for w in words if w["role"] == "oov"]
    label = {w["word_id"]: w["label"] for w in words}
    alloc = None
    with open(a.allocation, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["speaker_id"] == a.speaker:
                alloc = r
    if alloc is None:
        sys.exit("error: speaker %s is not in the allocation" % a.speaker)
    if os.path.exists(a.out):
        sys.exit("error: %s exists; refusing to overwrite" % a.out)
    out = []
    for sess in ("S1", "S2"):
        blocks = [b for b in alloc["s1_blocks" if sess == "S1" else "s2_blocks"].split("+") if b]
        offset = {}                                   # repetition numbers continue across blocks of one session (dataset key ignores the condition)
        pos = 0
        for b in blocks:
            c = conds[b]
            seed = int(alloc["prompt_seed_s1" if sess == "S1" else "prompt_seed_s2"], 16) ^ int(seed_hex("block", b), 16)
            rnd = random.Random(seed)
            pool = vocab + (oov if c["include_oov"] == "yes" else [])
            start = pos
            for r_i, perm in enumerate(rounds(pool, int(c["repetitions"]), rnd), start=1):
                for p_i, wid in enumerate(perm, start=1):
                    rep = start + r_i
                    rid = "%s-%s-%s-%s-r%d" % (a.speaker, sess, b, wid, rep)
                    if not ID_RE.match(rid):
                        sys.exit("error: planned recording id %r is too long or invalid; shorten ids" % rid)
                    out.append([a.speaker, sess, b, r_i, p_i, wid, label[wid] if a.with_labels else "", rep, c["environment"], c["noise_condition"], c["device_id"], rid])
            pos += int(c["repetitions"])
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["speaker_id", "session_id_suffix", "condition_id", "round", "position", "word_id", "label", "repetition", "environment", "noise_condition", "device_id", "planned_recording_id"])
        w.writerows(out)
    print("wrote %s (%d prompts)" % (a.out, len(out)))


# ---------------------------------------------------------------- budget -----------------------------
def wilson_half_width(p, n, z=1.96):
    if n == 0:
        return float("nan")
    d = 1 + z * z / n
    return z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d


def word_counts(a):
    """Counts of APPROVED words: from the actual word list when given (only 'verified' rows count), else the manual numbers."""
    if a.wordlist:
        words = read_wordlist(a.wordlist)
        ver = [w for w in words if w["review_status"] == "verified"]
        unver = len(words) - len(ver)
        if unver:
            print("WARNING: %d word(s) in the word list are not 'verified' and are excluded from this budget" % unver)
        nv = sum(1 for w in ver if w["role"] == "vocab")
        no = sum(1 for w in ver if w["role"] == "oov")
        print("Approved words from %s: %d vocab, %d unknown%s" % (os.path.basename(a.wordlist), nv, no, "" if 30 <= nv <= 50 else "   (pilot guidance is 30-50 vocab words)"))
        return nv, no
    if a.vocab_words is None or a.oov_words is None:
        sys.exit("error: give --wordlist (preferred: the approved list) or both --vocab-words and --oov-words")
    print("NOTE: word counts were typed in by hand (%d vocab, %d unknown); prefer --wordlist so the budget follows the approved list" % (a.vocab_words, a.oov_words))
    return a.vocab_words, a.oov_words


def cmd_budget(a):
    sizes = [int(x) for x in a.splits.split(",")]
    all_rows, _ = load_conditions(a.conditions)
    errs, _ = check(a.conditions, a.matrix)
    if errs:
        sys.exit("error: spec invalid: " + "; ".join(errs[:3]))
    act = active_rows(all_rows, parse_with(a.with_, all_rows))
    active = {r["condition_id"] for r in act}
    mrows = read_csv(a.matrix, MATRIX_TAG)
    cells = [m for m in mrows if all(c in active for c in m["enrol_conditions"].split("+") + [m["query_condition"]])]
    used = set()
    for m in cells:
        used.update(m["enrol_conditions"].split("+"))
        used.add(m["query_condition"])
    nv, no = word_counts(a)
    unused = sorted(active - used)
    if unused:
        print("WARNING: active condition(s) %s appear in no active matrix cell: recording them would not answer any planned comparison" % ", ".join(unused))
    per_speaker = {}
    for r in act:
        if r["condition_id"] in used:
            per_speaker[r["condition_id"]] = int(r["repetitions"]) * (nv + (no if r["include_oov"] == "yes" else 0))
    print("Active conditions: %s (optional ones are on only via --with)" % ", ".join(r["condition_id"] for r in act))
    skipped = sorted(r["condition_id"] for r in all_rows if r["condition_id"] not in active)
    if skipped:
        print("Not active (optional/disabled): %s" % ", ".join("%s[%s]" % (c, next(r["requires"] for r in all_rows if r["condition_id"] == c)) for c in skipped))
    print("Matrix cells runnable with the active conditions: %d of %d" % (len(cells), len(mrows)))
    cat = {}
    for m in cells:
        cat[m["category"]] = cat.get(m["category"], 0) + 1
    print("  by category: " + ", ".join("%s %d" % kv for kv in sorted(cat.items())))
    print("Speakers: enrol=%d calib=%d test=%d (total %d)" % (*sizes, sum(sizes)))
    print("Utterances per speaker: %s = %d" % (", ".join("%s %d" % kv for kv in per_speaker.items()), sum(per_speaker.values())))
    print("Total utterances: %d" % (sum(per_speaker.values()) * sum(sizes)))
    print("")
    print("Queries per condition for ONE query split (calib or test), known-word / unknown-word, and Wilson 95% half-width at p=0.8 (recordings treated as independent: optimistic because speakers cluster):")
    for sp_name, n_spk in (("calib", sizes[1]), ("test", sizes[2])):
        print("  %s (%d speakers):" % (sp_name, n_spk))
        for r in act:
            if r["condition_id"] not in per_speaker:
                continue
            k = n_spk * nv * int(r["repetitions"])
            u = n_spk * no * int(r["repetitions"]) if r["include_oov"] == "yes" else 0
            print("    %-8s known %4d (+-%.1f pts)  unknown %3d" % (r["condition_id"], k, 100 * wilson_half_width(0.8, k), u))
    print("")
    print("Templates per vocab word available from the %d enrolment speakers:" % sizes[0])
    for r in act:
        if r["condition_id"] in per_speaker:
            print("    %-8s %d" % (r["condition_id"], sizes[0] * int(r["repetitions"])))
    print("")
    print("With only %d test speakers, between-speaker variability, not recording count, limits what the pilot can show: expect to detect large effects only." % sizes[2])


# ---------------------------------------------------------------- protocol views ---------------------
def sess_tail(session):
    m = re.search(r"-(S[0-9])$", session)
    return m.group(1) if m else ""


def cmd_views(a):
    """Derive the two protocol datasets from ONE recording pool. The pool's own split column is ignored.
    Speaker-independent: split = the speaker's allocated split for ALL their recordings; unknown-word recordings of
      enrolment speakers are dropped (unknown words may never be enrolled).
    Personalised: S1 vocab recordings are the participant's own templates (enrol); S2 recordings are queries, labelled test for
      test participants and calib for everyone else; S1 unknown-word recordings are dropped (a session cannot be both split)."""
    with open(os.path.join(a.pool, "dataset_meta.txt"), encoding="utf-8") as f:
        meta = f.read()
    m = re.search(r"^dataset_id=(.*)$", meta, re.M)
    if not m:
        sys.exit("error: pool dataset_meta.txt has no dataset_id")
    base_id = m.group(1)
    with open(os.path.join(a.pool, "recordings.csv"), encoding="utf-8", newline="") as f:
        tag = f.readline().rstrip("\r\n")
        if tag != "#s2w-recordings v1":
            sys.exit("error: pool recordings.csv has an unexpected first line")
        rd = csv.DictReader(f)
        fields, pool = rd.fieldnames, list(rd)
    words = {w["word_id"]: w for w in read_wordlist(os.path.join(a.pool, "wordlist.csv"))}
    with open(a.allocation, encoding="utf-8", newline="") as f:
        alloc = {r["speaker_id"]: r for r in csv.DictReader(f)}
    missing = sorted({r["speaker_id"] for r in pool} - set(alloc))
    if missing:
        sys.exit("error: pool speakers missing from the allocation: " + ", ".join(missing))
    if os.path.exists(a.out_dir):
        sys.exit("error: %s exists; refusing to overwrite" % a.out_dir)
    views = {}
    dropped = {"speaker_independent": 0, "personalised": 0}
    si_rows, pe_rows = [], []
    for r in pool:
        sp = alloc[r["speaker_id"]]
        is_oov = words[r["word_id"]]["role"] == "oov"
        tail = sess_tail(r["session_id"])
        # speaker-independent
        if sp["split"] == "enrol" and is_oov:
            dropped["speaker_independent"] += 1
        else:
            x = dict(r)
            x["split"] = sp["split"]
            si_rows.append(x)
        # personalised
        if tail == "S1" and is_oov:
            dropped["personalised"] += 1
        elif tail == "S1":
            x = dict(r)
            x["split"] = "enrol"
            pe_rows.append(x)
        elif tail == "S2":
            x = dict(r)
            x["split"] = "test" if sp["split"] == "test" else "calib"
            pe_rows.append(x)
        else:
            dropped["personalised"] += 1
    views = {"si": ("speaker_independent", si_rows, ".si"), "pers": ("personalised", pe_rows, ".pers")}
    for key, (proto, rows_, suffix) in views.items():
        d = os.path.join(a.out_dir, key)
        os.makedirs(d)
        new_id = base_id[:40 - len(suffix)] + suffix
        with open(os.path.join(d, "dataset_meta.txt"), "w", encoding="utf-8") as f:
            f.write(re.sub(r"^protocol=.*$", "protocol=%s" % proto, re.sub(r"^dataset_id=.*$", "dataset_id=%s" % new_id, meta, flags=re.M), flags=re.M))
        for name in ("wordlist.csv", "participants.csv"):
            src = os.path.join(a.pool, name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(d, name))
        with open(os.path.join(d, "recordings.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(tag + "\n")
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows_)
        for r in rows_:
            dst = os.path.join(d, r["path"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.lexists(dst):
                os.symlink(os.path.abspath(os.path.join(a.pool, r["path"])), dst)
        print("%-4s %-19s recordings=%d  (pool recordings not used by this protocol: %d)" % (key, proto, len(rows_), dropped["speaker_independent" if key == "si" else "personalised"]))
    print("Validate both with build/s2w_dataset_check; evaluate each ONLY with settings whose protocol matches (settings key protocol=personalised for the pers view).")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write-spec"); w.add_argument("outdir")
    c = sub.add_parser("check"); c.add_argument("--conditions", required=True); c.add_argument("--matrix", required=True)
    def consent_args(sp):
        sp.add_argument("--consent-register"); sp.add_argument("--participants-register"); sp.add_argument("--today")
        sp.add_argument("--synthetic-no-consent-check", action="store_true", help="software tests with synthetic ids ONLY")
    pl = sub.add_parser("plan"); pl.add_argument("--speakers", required=True); pl.add_argument("--conditions", required=True); pl.add_argument("--matrix")
    pl.add_argument("--splits", required=True); pl.add_argument("--seed", type=int, required=True); pl.add_argument("--out", required=True)
    pl.add_argument("--with", dest="with_", default="", help="optional conditions to switch on: 'all' or a comma list")
    consent_args(pl)
    pr = sub.add_parser("prompts"); pr.add_argument("--wordlist", required=True); pr.add_argument("--conditions", required=True); pr.add_argument("--allocation", required=True)
    pr.add_argument("--speaker", required=True); pr.add_argument("--out", required=True); pr.add_argument("--with-labels", action="store_true")
    consent_args(pr)
    b = sub.add_parser("budget"); b.add_argument("--conditions", required=True); b.add_argument("--matrix", required=True); b.add_argument("--splits", required=True)
    b.add_argument("--wordlist"); b.add_argument("--vocab-words", type=int); b.add_argument("--oov-words", type=int)
    b.add_argument("--with", dest="with_", default="")
    v = sub.add_parser("views"); v.add_argument("--pool", required=True); v.add_argument("--allocation", required=True); v.add_argument("--out-dir", required=True)
    a = p.parse_args(argv)
    if a.cmd == "write-spec":
        write_spec(a.outdir)
    elif a.cmd == "check":
        errs, warns = check(a.conditions, a.matrix)
        for w_ in warns:
            print("WARN:", w_)
        for e in errs:
            print("ERROR:", e, file=sys.stderr)
        print("result=%s errors=%d warnings=%d" % ("INVALID" if errs else "VALID", len(errs), len(warns)))
        sys.exit(1 if errs else 0)
    elif a.cmd == "plan":
        cmd_plan(a)
    elif a.cmd == "prompts":
        cmd_prompts(a)
    elif a.cmd == "views":
        cmd_views(a)
    else:
        cmd_budget(a)


if __name__ == "__main__":
    main()
