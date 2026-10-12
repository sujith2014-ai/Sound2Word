#!/usr/bin/env python3
"""Malayalam word-list review workflow for Sound2Word (see docs/WORD_REVIEW.md).

  review_sheet.py template  OUT.xlsx [--vocab-slots 40] [--oov-slots 10]
  review_sheet.py split     MASTER.xlsx --out-dir DIR            # blind copy per reviewer
  review_sheet.py merge     MASTER.xlsx --r1 R1.xlsx --r2 R2.xlsx [--out MERGED.xlsx] [--prefill-final]
  review_sheet.py status    MASTER.xlsx [--write]               # recompute after humans settle disagreements
  review_sheet.py export    MASTER.xlsx --out wordlist.csv --reviewer1-id ID --reviewer2-id ID
                            [--allow-non-malayalam]

This tool never invents, translates or "fixes" Malayalam. Disagreement is reported as UNRESOLVED and
blocks export; humans resolve it. Needs Python 3 and openpyxl.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import unicodedata

TOOL_VERSION = "1"
SCHEMA = "s2w-review-sheet-v1"
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
ROLES = ["vocab", "oov"]
CONTRASTS = ["none", "vowel_length", "gemination", "other"]
DECISIONS = ["Accept", "Correct", "Reject"]
YESNO = ["Yes", "No"]
CONTRAST_OK = ["Yes", "No", "N/A"]
ZW = "‌‍"

# (key, header, width, owner)   owner decides colour; "proposal" columns are hashed for blind copies
COLS = [
    ("id", "Word ID", 11, "proposal"),
    ("role", "Role (vocab = enrolled word; oov = unknown-word test)", 16, "proposal"),
    ("word", "Malayalam word (candidate spelling)", 24, "proposal"),
    ("roman", "Romanisation (optional)", 18, "proposal"),
    ("meaning", "English meaning / context", 26, "proposal"),
    ("ipa", "Pronunciation (IPA) - optional; only if checked by someone competent in phonetic transcription", 24, "proposal"),
    ("pair", "Contrast pair ID", 12, "proposal"),
    ("ctype", "Contrast type (none / vowel_length / gemination / other)", 16, "proposal"),
    ("proposer", "Proposed by (pseudonymous ID)", 14, "info"),
    ("r1_dec", "Reviewer 1 decision (Accept / Correct / Reject)", 14, "r1"),
    ("r1_fix", "Reviewer 1 corrected spelling (if Correct)", 22, "r1"),
    ("r1_con", "Reviewer 1: contrast genuine? (Yes / No / N/A)", 14, "r1"),
    ("r1_fam", "Reviewer 1: familiar to speakers? (Yes / No)", 14, "r1"),
    ("r1_var", "Reviewer 1: pronunciation / regional variants accepted", 24, "r1"),
    ("r1_note", "Reviewer 1 notes (spelling, pronunciation, dialect, meaning)", 34, "r1"),
    ("r2_dec", "Reviewer 2 decision (Accept / Correct / Reject)", 14, "r2"),
    ("r2_fix", "Reviewer 2 corrected spelling (if Correct)", 22, "r2"),
    ("r2_con", "Reviewer 2: contrast genuine? (Yes / No / N/A)", 14, "r2"),
    ("r2_fam", "Reviewer 2: familiar to speakers? (Yes / No)", 14, "r2"),
    ("r2_var", "Reviewer 2: pronunciation / regional variants accepted", 24, "r2"),
    ("r2_note", "Reviewer 2 notes (spelling, pronunciation, dialect, meaning)", 34, "r2"),
    ("status", "Agreement status (computed by review_sheet.py merge/status - do not edit)", 40, "computed"),
    ("final", "Final verified spelling (approved form)", 24, "final"),
    ("variants", "Regional variants accepted (final)", 24, "final"),
    ("approved", "Recording approved (Yes / No)", 12, "final"),
    ("resolution", "Resolution notes (how disagreements were settled)", 34, "final"),
]
KEYS = [c[0] for c in COLS]
HEAD = {c[0]: c[1] for c in COLS}
PROPOSAL_KEYS = [c[0] for c in COLS if c[3] == "proposal"]
REVIEWER_KEYS = {1: ["r1_dec", "r1_fix", "r1_con", "r1_fam", "r1_var", "r1_note"],
                 2: ["r2_dec", "r2_fix", "r2_con", "r2_fam", "r2_var", "r2_note"]}
FILLS = {"proposal": "FFF2CC", "info": "FFF2CC", "r1": "DDEBF7", "r2": "E2EFDA", "computed": "D9D9D9", "final": "FCE4D6"}


def need_openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        sys.exit("error: openpyxl is required (pip install openpyxl)")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def clean(v):
    if v is None:
        return ""
    return nfc(str(v).strip())


def strip_zw(s):
    return "".join(ch for ch in s if ch not in ZW)


def is_malayalam_text(s):
    """Malayalam block, ZWJ/ZWNJ, and plain space only."""
    return bool(s) and all(("ഀ" <= ch <= "ൿ") or ch in ZW or ch == " " for ch in s)


# ---------------------------------------------------------------- agreement logic (pure) ----------
def effective_spelling(row, n):
    d = row["r%d_dec" % n]
    if d == "Accept":
        return row["word"], None
    if d == "Correct":
        fix = row["r%d_fix" % n]
        if not fix:
            return None, "reviewer %d chose Correct without a corrected spelling" % n
        return fix, None
    return None, None


def evaluate_row(row):
    """Returns (status, agreed_spelling or None). Status strings start with a stable prefix:
    'Empty slot', 'Pending review', 'Invalid entry', 'UNRESOLVED', 'Agreed: ...'."""
    reviewed = any(row[k] for k in ("r1_dec", "r2_dec"))
    if not row["word"] and not reviewed:
        return "Empty slot", None
    if not row["word"]:
        return "Invalid entry: reviewed row has no candidate word", None
    if not ID_RE.match(row["id"]):
        return "Invalid entry: Word ID must match [A-Za-z0-9._-]{1,40}", None
    if row["role"] not in ROLES:
        return "Invalid entry: Role must be vocab or oov", None
    if row["ctype"] not in CONTRASTS:
        return "Invalid entry: Contrast type must be none / vowel_length / gemination / other", None
    in_pair = bool(row["pair"])
    if in_pair != (row["ctype"] != "none"):
        return "Invalid entry: Contrast pair ID and Contrast type must be set together", None
    if in_pair and row["role"] == "oov":
        return "Invalid entry: unknown-word (oov) rows cannot be in a contrast pair", None
    if in_pair and not ID_RE.match(row["pair"]):
        return "Invalid entry: Contrast pair ID must match [A-Za-z0-9._-]{1,40}", None
    for n in (1, 2):
        d = row["r%d_dec" % n]
        if d and d not in DECISIONS:
            return "Invalid entry: reviewer %d decision must be Accept / Correct / Reject" % n, None
    d1, d2 = row["r1_dec"], row["r2_dec"]
    if not d1 or not d2:
        return "Pending review", None

    if d1 == "Reject" and d2 == "Reject":
        return "Agreed: reject", None
    if (d1 == "Reject") != (d2 == "Reject"):
        return "UNRESOLVED: one reviewer rejects, the other does not", None

    sp = []
    for n in (1, 2):
        s, err = effective_spelling(row, n)
        if err:
            return "Invalid entry: " + err, None
        sp.append(s)
    for n in (1, 2):
        for key, allowed, label in (("fam", YESNO, "familiarity"), ("con", CONTRAST_OK, "contrast")):
            if key == "con" and not in_pair:
                continue            # contrast answers matter only while the word is in a pair
            v = row["r%d_%s" % (n, key)]
            if not v:
                return "Pending review", None
            if v not in allowed:
                return "Invalid entry: reviewer %d %s answer must be %s" % (n, label, " / ".join(allowed)), None
            if key == "con" and v == "N/A":
                return "Invalid entry: reviewer %d answered N/A but the word is in a contrast pair" % n, None

    if sp[0] != sp[1]:
        if strip_zw(sp[0]) == strip_zw(sp[1]):
            return "UNRESOLVED: spellings differ only in ZWJ/ZWNJ (chillu/conjunct encoding) - decide the canonical form", None
        return "UNRESOLVED: reviewers disagree on the spelling", None
    if row["r1_fam"] != row["r2_fam"]:
        return "UNRESOLVED: reviewers disagree on whether the word is familiar", None
    if in_pair and row["r1_con"] != row["r2_con"]:
        return "UNRESOLVED: reviewers disagree on whether the contrast is genuine", None
    if row["r1_fam"] == "No":
        return "Agreed: not familiar to speakers (do not record)", None
    if in_pair and row["r1_con"] == "No":
        return "Agreed: contrast not genuine (redesign or remove the pair)", None
    return "Agreed: accept", sp[0]


# ---------------------------------------------------------------- workbook helpers ----------------
def styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style="thin", color="BFBFBF")
    return {
        "font": Font(name="Arial", size=10),
        "bold": Font(name="Arial", size=10, bold=True),
        "head_font": Font(name="Arial", size=10, bold=True, color="FFFFFF"),
        "head_fill": PatternFill("solid", fgColor="1F3864"),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
        "wrap": Alignment(wrap_text=True, vertical="top"),
        "headwrap": Alignment(wrap_text=True, vertical="center"),
        "fill": lambda hexcol: PatternFill("solid", fgColor=hexcol),
    }


def add_validations(ws, first, last, keys):
    from openpyxl.worksheet.datavalidation import DataValidation
    col = {k: i + 1 for i, k in enumerate(keys)}
    from openpyxl.utils import get_column_letter as L

    def dv(key, formula, strict=True, prompt=None):
        if key not in col:
            return
        v = DataValidation(type="list", formula1='"%s"' % formula, allow_blank=True)
        v.errorStyle = "stop" if strict else "warning"
        v.showErrorMessage = True
        v.error = "Choose one of: " + formula.replace(",", ", ")
        if prompt:
            v.showInputMessage = True
            v.prompt = prompt
        ws.add_data_validation(v)
        v.add("%s%d:%s%d" % (L(col[key]), first, L(col[key]), last))

    dv("role", ",".join(ROLES))
    dv("ctype", ",".join(CONTRASTS))
    for n in (1, 2):
        dv("r%d_dec" % n, ",".join(DECISIONS), prompt="Accept = spelling/meaning/pronunciation right. Correct = give corrected spelling. Reject = do not use.")
        dv("r%d_con" % n, ",".join(CONTRAST_OK))
        dv("r%d_fam" % n, ",".join(YESNO))
    dv("approved", ",".join(YESNO))
    dv("pair", ",".join("P%02d" % i for i in range(1, 21)), strict=False, prompt="Pick a suggested ID or type your own (letters, digits, . _ -). Both members of a pair share one ID.")


def write_header(ws, keys, S, comments=True):
    from openpyxl.comments import Comment
    for i, k in enumerate(keys, start=1):
        c = ws.cell(row=1, column=i, value=HEAD[k] if k in HEAD else k)
        c.font, c.fill, c.alignment, c.border = S["head_font"], S["head_fill"], S["headwrap"], S["border"]
    ws.row_dimensions[1].height = 62
    notes = {
        "id": "Stable identifier, independent of spelling. Never change it once recordings exist.",
        "ipa": "Leave blank unless a person competent in phonetic transcription has checked it. Not used by the software.",
        "ctype": "vowel_length (short vs long vowel), gemination (single vs geminate consonant), other, or none.",
        "r1_dec": "Judge independently. Do not look at Reviewer 2's answers before submitting yours.",
        "r2_dec": "Judge independently. Do not look at Reviewer 1's answers before submitting yours.",
        "status": "Written by the tool. Disagreement stays UNRESOLVED until humans settle it.",
        "final": "Fill in only after agreement. Must equal the agreed spelling exactly (including ZWJ/ZWNJ).",
        "approved": "Yes only for words agreed as acceptable AND familiar. Blank is an error at export.",
    }
    if comments:
        for i, k in enumerate(keys, start=1):
            if k in notes:
                ws.cell(row=1, column=i).comment = Comment(notes[k], "Sound2Word")


def style_rows(ws, keys, first, last, S, unlocked=None, owners=None):
    from openpyxl.styles import Protection
    owners = owners or {c[0]: c[3] for c in COLS}
    for r in range(first, last + 1):
        for i, k in enumerate(keys, start=1):
            c = ws.cell(row=r, column=i)
            c.font, c.alignment, c.border = S["font"], S["wrap"], S["border"]
            c.number_format = "@"
            c.fill = S["fill"](FILLS.get(owners.get(k, "info"), "FFFFFF"))
            if unlocked is not None:
                c.protection = Protection(locked=k not in unlocked)
    for i, k in enumerate(keys, start=1):
        from openpyxl.utils import get_column_letter as L
        w = next((c[2] for c in COLS if c[0] == k), 18)
        ws.column_dimensions[L(i)].width = w


def meta_sheet(wb, items):
    ws = wb.create_sheet("_meta")
    for r, (k, v) in enumerate(items, start=1):
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v)
    ws.sheet_state = "hidden"


def read_meta(wb):
    if "_meta" not in wb.sheetnames:
        return {}
    ws = wb["_meta"]
    return {str(r[0].value): "" if r[1].value is None else str(r[1].value) for r in ws.iter_rows(min_row=1, max_col=2)}


def read_rows(ws, headers_to_keys):
    """Read data rows by header text (robust to column order). Returns list of (excel_row, dict)."""
    hdr = {}
    for c in ws[1]:
        h = clean(c.value)
        if h in headers_to_keys:
            hdr[headers_to_keys[h]] = c.column
    out = []
    for r in range(2, ws.max_row + 1):
        d = {k: clean(ws.cell(row=r, column=col).value) for k, col in hdr.items()}
        if any(d.values()):
            d["_row"] = r
            out.append(d)
    return out, hdr


def load_master(path):
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path)
    except Exception as e:  # unreadable / not xlsx
        sys.exit("error: cannot open %s: %s" % (path, e))
    if read_meta(wb).get("kind") != "master" or "Master" not in wb.sheetnames:
        sys.exit("error: %s is not a master review workbook" % path)
    return wb


def master_rows(wb):
    ws = wb["Master"]
    rows, hdr = read_rows(ws, {v: k for k, v in HEAD.items()})
    missing = [HEAD[k] for k in KEYS if k not in hdr]
    if missing:
        sys.exit("error: Master sheet is missing columns: %s" % "; ".join(missing))
    for d in rows:
        for k in KEYS:
            d.setdefault(k, "")
    return rows


def proposal_hash(rows):
    h = hashlib.sha256()
    for d in sorted(rows, key=lambda x: x["id"]):
        h.update(("\x1f".join(d[k] for k in PROPOSAL_KEYS) + "\x1e").encode("utf-8"))
    return h.hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- commands -----------------------
def readme_sheet(wb, S, n_vocab, n_oov):
    ws = wb.active
    ws.title = "README"
    ws.sheet_view.showGridLines = False
    lines = [
        ("h", "Sound2Word - Malayalam word-list review"),
        ("t", "Purpose: two Malayalam speakers independently verify each candidate word BEFORE any recording is treated as ground truth. "
              "Nothing in this workbook is verified until both reviewers have judged it and any disagreement has been settled by people."),
        ("t", "Slots: %d enrolled-vocabulary slots (IDs W001...) and %d unknown-word slots (IDs U001...). Unknown words are spoken only by calibration/test speakers "
              "and must never be words in the enrolled vocabulary." % (n_vocab, n_oov)),
        ("s", "Workflow"),
        ("t", "1. Proposer (a native speaker) fills the yellow columns of the Master sheet: word, meaning, optional romanisation/IPA, and contrast pairs."),
        ("t", "2. Run `review_sheet.py split` to make one blind copy per reviewer. Each reviewer fills ONLY their own copy, without seeing the other's answers."),
        ("t", "3. Run `review_sheet.py merge`. Agreement is computed; any disagreement shows UNRESOLVED (red). Nothing is forced to agree."),
        ("t", "4. Reviewers meet, settle UNRESOLVED rows, and the project owner fills Final verified spelling, Regional variants, Recording approved and Resolution notes (orange)."),
        ("t", "5. Run `review_sheet.py export` to write the versioned wordlist.csv for Sound2Word. It refuses to export while anything is pending or unresolved."),
        ("t", "6. Only then: consented recordings, then `s2w_dataset_check`."),
        ("s", "How to judge a word"),
        ("t", "Accept = spelling, intended meaning and pronunciation are right as written.   Correct = right word but spelled wrongly; type the corrected spelling.   "
              "Reject = unsuitable (wrong, obscure, offensive, ambiguous...). Say why in the notes."),
        ("t", "Contrast genuine? (only for words in a contrast pair): does this pair really differ in the stated feature (short vs long vowel, single vs geminate consonant, other)? "
              "Familiar to speakers?: would ordinary speakers say this word naturally?"),
        ("t", "Note acceptable regional pronunciation variants. If you are unsure, say so in the notes - 'unsure' is better than a guess."),
        ("s", "Malayalam text rules"),
        ("t", "Type or paste spellings exactly as they should be written. ZWJ/ZWNJ characters (used for chillu letters and conjuncts) are part of the spelling and are NOT ignored: "
              "two spellings that differ only by ZWJ/ZWNJ are reported UNRESOLVED so you choose one canonical form. Text is normalised to Unicode NFC when read."),
        ("t", "If Malayalam shows as boxes, change the font of the cells to one that supports Malayalam (e.g. Noto Sans Malayalam, Nirmala UI, Malayalam MN)."),
        ("s", "Column colours"),
        ("t", "Yellow = proposal.   Blue = Reviewer 1.   Green = Reviewer 2.   Grey = computed by the tool.   Orange = final decision by people."),
        ("s", "Format example (placeholders only - NOT a proposal and NOT real data)"),
        ("t", "Word ID: W007 | Role: vocab | Malayalam word: <word in Malayalam script> | English meaning: <meaning and context> | Contrast pair ID: P03 | "
              "Contrast type: vowel_length | Reviewer decision: Correct | Corrected spelling: <corrected word> | Contrast genuine?: Yes | Familiar?: Yes | Notes: <free text>"),
        ("s", "Privacy"),
        ("t", "Refer to people by pseudonymous IDs (e.g. rev-a, rev-b), not names, in cells and notes. Keep completed sheets in private storage."),
        ("t", "Tool: review_sheet.py v%s, schema %s." % (TOOL_VERSION, SCHEMA)),
    ]
    ws.column_dimensions["A"].width = 100
    r = 1
    from openpyxl.styles import Font, Alignment
    for kind, text in lines:
        c = ws.cell(row=r, column=1, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if kind == "h":
            c.font = Font(name="Arial", size=14, bold=True)
        elif kind == "s":
            c.font = Font(name="Arial", size=11, bold=True, color="1F3864")
            r += 0
        else:
            c.font = Font(name="Arial", size=10)
            ws.row_dimensions[r].height = 14 * (1 + len(text) // 95)
        r += 1


def cmd_template(a):
    need_openpyxl()
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import PatternFill
    if os.path.exists(a.out):
        sys.exit("error: %s exists; refusing to overwrite" % a.out)
    nv, no = a.vocab_slots, a.oov_slots
    if nv < 1 or no < 0 or nv > 999 or no > 999:
        sys.exit("error: slot counts out of range")
    if not 30 <= nv <= 50:
        print("note: pilot guidance is 30-50 vocabulary words (you asked for %d)" % nv, file=sys.stderr)
    S = styles()
    wb = Workbook()
    readme_sheet(wb, S, nv, no)
    ws = wb.create_sheet("Master")
    write_header(ws, KEYS, S)
    last = 1 + nv + no
    style_rows(ws, KEYS, 2, last, S)
    for i in range(nv):
        ws.cell(row=2 + i, column=1, value="W%03d" % (i + 1))
        ws.cell(row=2 + i, column=2, value="vocab")
        ws.cell(row=2 + i, column=8, value="none")
    for i in range(no):
        ws.cell(row=2 + nv + i, column=1, value="U%03d" % (i + 1))
        ws.cell(row=2 + nv + i, column=2, value="oov")
        ws.cell(row=2 + nv + i, column=8, value="none")
    for r in range(2, last + 1):
        ws.cell(row=r, column=KEYS.index("status") + 1, value="Empty slot")
    add_validations(ws, 2, last, KEYS)
    sc = "$%s2" % __import__("openpyxl").utils.get_column_letter(KEYS.index("status") + 1)
    rng = "%s2:%s%d" % (sc[1:], sc[1:], last)
    for prefix, color in (("UNRESOLVED", "F8CBAD"), ("Invalid", "FF9999"), ("Agreed: accept", "C6E0B4"), ("Pending", "FFE699")):
        ws.conditional_formatting.add(rng, FormulaRule(formula=['LEFT(%s,%d)="%s"' % (sc, len(prefix), prefix)], fill=PatternFill("solid", bgColor=color, fgColor=color)))
    ws.freeze_panes = "D2"
    meta_sheet(wb, [("schema", SCHEMA), ("tool_version", TOOL_VERSION), ("kind", "master"), ("vocab_slots", nv), ("oov_slots", no)])
    wb.save(a.out)
    print("template written: %s (%d vocab + %d unknown-word slots)" % (a.out, nv, no))


def cmd_split(a):
    need_openpyxl()
    from openpyxl import Workbook, load_workbook
    wb = load_master(a.master)
    rows = master_rows(wb)
    ph = proposal_hash(rows)
    bad = [d for d in rows if d["word"] and (not ID_RE.match(d["id"]))]
    if bad:
        sys.exit("error: invalid Word ID in row %d" % bad[0]["_row"])
    live = [d for d in rows if d["word"]]
    if not live:
        sys.exit("error: no candidate words proposed yet")
    os.makedirs(a.out_dir, exist_ok=True)
    S = styles()
    for n in (1, 2):
        path = os.path.join(a.out_dir, "review_copy_reviewer%d.xlsx" % n)
        if os.path.exists(path):
            sys.exit("error: %s exists; refusing to overwrite" % path)
        keys = PROPOSAL_KEYS + REVIEWER_KEYS[n]
        w2 = Workbook()
        readme_sheet(w2, S, sum(1 for d in live if d["role"] == "vocab"), sum(1 for d in live if d["role"] == "oov"))
        w2["README"].insert_rows(1)
        w2["README"]["A1"] = "THIS IS REVIEWER %d'S PRIVATE COPY. Fill only the %s columns. Do not discuss answers with the other reviewer until both copies are submitted." % (n, "blue" if n == 1 else "green")
        ws = w2.create_sheet("Review")
        write_header(ws, keys, S, comments=False)
        # reviewer columns share generic headers but keep master header text so merge can map them
        for i, k in enumerate(keys, start=1):
            ws.cell(row=1, column=i, value=HEAD[k])
        owners = {k: "proposal" for k in PROPOSAL_KEYS}
        owners.update({k: "r%d" % n for k in REVIEWER_KEYS[n]})
        style_rows(ws, keys, 2, 1 + len(live), S, unlocked=set(REVIEWER_KEYS[n]), owners=owners)
        for r, d in enumerate(live, start=2):
            for i, k in enumerate(PROPOSAL_KEYS, start=1):
                ws.cell(row=r, column=i, value=d[k])
        add_validations(ws, 2, 1 + len(live), [k.replace("r%d_" % n, "r%d_" % n) for k in keys])
        ws.freeze_panes = "D2"
        ws.protection.sheet = True
        ws.protection.formatColumns = False
        ws.protection.formatRows = False
        # note: sheet protection has no password; it prevents accidental edits of the proposal only
        meta_sheet(w2, [("schema", SCHEMA), ("kind", "reviewer"), ("reviewer_slot", n), ("proposal_sha256", ph), ("n_rows", len(live))])
        w2.save(path)
        print("wrote %s" % path)
    print("proposal_sha256=%s" % ph)


def load_reviewer(path, n, master_hash):
    from openpyxl import load_workbook
    wb = load_workbook(path)
    meta = read_meta(wb)
    if meta.get("kind") != "reviewer" or meta.get("reviewer_slot") != str(n):
        sys.exit("error: %s is not the Reviewer %d copy" % (path, n))
    if meta.get("proposal_sha256") != master_hash:
        sys.exit("error: %s was made from a different proposal than the current Master (proposal changed after splitting); regenerate the copies and re-review" % path)
    rows, hdr = read_rows(wb["Review"], {v: k for k, v in HEAD.items()})
    for k in PROPOSAL_KEYS:
        if k not in hdr:
            sys.exit("error: %s: missing column '%s'" % (path, HEAD[k]))
    return rows


def cmd_merge(a):
    need_openpyxl()
    from openpyxl import load_workbook
    wb = load_master(a.master)
    rows = master_rows(wb)
    ph = proposal_hash(rows)
    ws = wb["Master"]
    col = {k: i + 1 for i, k in enumerate(KEYS)}
    by_id = {d["id"]: d for d in rows}
    nonnfc = 0
    for n, path in ((1, a.r1), (2, a.r2)):
        rrows = load_reviewer(path, n, ph)
        seen = set()
        for rr in rrows:
            m = by_id.get(rr["id"])
            if m is None:
                sys.exit("error: %s row %d: unknown Word ID %r" % (path, rr["_row"], rr["id"]))
            if rr["id"] in seen:
                sys.exit("error: %s: duplicate Word ID %r" % (path, rr["id"]))
            seen.add(rr["id"])
            for k in PROPOSAL_KEYS:
                if rr.get(k, "") != m[k]:
                    sys.exit("error: %s row %d: proposal column '%s' was edited (%r != %r); reviewers must use 'Correct' instead of editing the candidate" % (path, rr["_row"], HEAD[k], rr.get(k, ""), m[k]))
            for k in REVIEWER_KEYS[n]:
                m[k] = rr.get(k, "")
                ws.cell(row=m["_row"], column=col[k], value=m[k] or None)
        missing = [d["id"] for d in rows if d["word"] and d["id"] not in seen]
        if missing:
            sys.exit("error: %s lacks rows for: %s" % (path, ", ".join(missing)))
    counts = recompute(rows)
    for d in rows:
        ws.cell(row=d["_row"], column=col["status"], value=d["_status"])
        if a.prefill_final and d["_agreed"] and not d["final"]:
            ws.cell(row=d["_row"], column=col["final"], value=d["_agreed"])
    out = a.out or a.master
    if out != a.master and os.path.exists(out):
        sys.exit("error: %s exists" % out)
    wb.save(out)
    print_summary(rows, counts)
    print("merged workbook: %s" % out)


def recompute(rows):
    counts = {}
    for d in rows:
        status, agreed = evaluate_row(d)
        d["_status"], d["_agreed"] = status, agreed
        key = status.split(":")[0] if status.startswith(("UNRESOLVED", "Invalid")) else status
        counts[key] = counts.get(key, 0) + 1
    return counts


def print_summary(rows, counts):
    for k in sorted(counts):
        print("%-60s %d" % (k, counts[k]))
    for d in rows:
        st = d["_status"]
        if st.startswith(("UNRESOLVED", "Invalid")):
            print("  row %d %s: %s" % (d["_row"], d["id"], st))


def cmd_status(a):
    need_openpyxl()
    from openpyxl import load_workbook
    wb = load_master(a.master)
    rows = master_rows(wb)
    counts = recompute(rows)
    print_summary(rows, counts)
    if a.write:
        ws = wb["Master"]
        col = KEYS.index("status") + 1
        for d in rows:
            ws.cell(row=d["_row"], column=col, value=d["_status"])
        wb.save(a.master)
        print("statuses written to %s" % a.master)


def cmd_export(a):
    need_openpyxl()
    from openpyxl import load_workbook
    for rid in (a.reviewer1_id, a.reviewer2_id):
        if not ID_RE.match(rid):
            sys.exit("error: reviewer IDs must match [A-Za-z0-9._-]{1,40} (use pseudonyms, not names)")
    if a.reviewer1_id == a.reviewer2_id:
        sys.exit("error: reviewer IDs must differ")
    if os.path.exists(a.out):
        sys.exit("error: %s exists; refusing to overwrite" % a.out)
    wb = load_master(a.master)
    rows = [d for d in master_rows(wb) if evaluate_row(d)[0] != "Empty slot"]
    recompute(rows)
    errors, included, excluded = [], [], []
    for d in rows:
        st, agreed = d["_status"], d["_agreed"]
        loc = "row %d %s" % (d["_row"], d["id"])
        if st.startswith("Agreed: accept"):
            if d["approved"] not in YESNO:
                errors.append("%s: 'Recording approved' must be Yes or No (blank is not assumed)" % loc)
            elif d["approved"] == "No":
                excluded.append((d["id"], "agreed acceptable but Recording approved = No"))
            elif d["final"] != agreed:
                errors.append("%s: Final verified spelling %r must equal the agreed spelling %r exactly" % (loc, d["final"], agreed))
            elif not (a.allow_non_malayalam or is_malayalam_text(d["final"])):
                errors.append("%s: final spelling contains characters outside the Malayalam block (use --allow-non-malayalam only for synthetic tests)" % loc)
            elif not d["meaning"]:
                errors.append("%s: English meaning is required for export" % loc)
            else:
                included.append(d)
        elif st.startswith("Agreed:"):
            if d["approved"] == "Yes":
                errors.append("%s: Recording approved = Yes contradicts '%s'" % (loc, st))
            else:
                excluded.append((d["id"], st))
        else:
            errors.append("%s: %s" % (loc, st))
    pairs = {}
    for d in rows:
        if d["pair"]:
            pairs.setdefault(d["pair"], []).append(d)
    inc_ids = {d["id"] for d in included}
    for pid, members in sorted(pairs.items()):
        ok = [m for m in members if m["id"] in inc_ids]
        if len(members) != 2 or len(ok) != 2:
            errors.append("pair %s must have exactly 2 members, both approved (found %d members, %d approved)" % (pid, len(members), len(ok)))
        elif members[0]["ctype"] != members[1]["ctype"]:
            errors.append("pair %s: members disagree on contrast type" % pid)
    # drop pairs' members that are excluded only if pair already errored (export refused anyway)
    for d in included:
        if len(d["meaning"].encode()) > 95 or len(d["final"].encode()) > 159 or len(d["roman"].encode()) > 95 or len(d["variants"].encode()) > 95:
            errors.append("row %d %s: meaning/romanisation/variants must be <= 95 bytes and spelling <= 159 bytes (UTF-8)" % (d["_row"], d["id"]))
        if not any(x["role"] == "vocab" for x in included):
            errors.append("no vocab words approved")
            break
    ids = [d["id"] for d in included]
    if len(set(ids)) != len(ids):
        errors.append("duplicate Word IDs among approved rows")
    if errors:
        print("EXPORT REFUSED (%d problem(s)); nothing written:" % len(errors), file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)
    included.sort(key=lambda d: (d["role"] != "vocab", d["id"]))
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write("#s2w-wordlist v1\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["word_id", "label", "romanisation", "meaning_en", "role", "pair_id", "contrast_type", "review_status", "reviewer_1", "reviewer_2", "variants"])
        for d in included:
            w.writerow([d["id"], d["final"], d["roman"], d["meaning"], d["role"], d["pair"], d["ctype"], "verified", a.reviewer1_id, a.reviewer2_id, d["variants"]])
    prov = {
        "schema": "s2w-wordlist-provenance-v1", "tool_version": TOOL_VERSION,
        "master_sha256": file_sha256(a.master), "wordlist_sha256": file_sha256(a.out),
        "reviewer_1": a.reviewer1_id, "reviewer_2": a.reviewer2_id,
        "n_included": len(included), "n_vocab": sum(d["role"] == "vocab" for d in included), "n_oov": sum(d["role"] == "oov" for d in included),
        "excluded": [{"word_id": i, "reason": r} for i, r in excluded],
        "ipa_note": "IPA and per-reviewer notes stay in the Master workbook; they are not part of schema v1.",
    }
    pp = os.path.splitext(a.out)[0] + ".provenance.json"
    with open(pp, "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print("wrote %s (%d words: %d vocab, %d unknown-word) and %s" % (a.out, len(included), prov["n_vocab"], prov["n_oov"], pp))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template"); t.add_argument("out"); t.add_argument("--vocab-slots", type=int, default=40); t.add_argument("--oov-slots", type=int, default=10)
    s = sub.add_parser("split"); s.add_argument("master"); s.add_argument("--out-dir", required=True)
    m = sub.add_parser("merge"); m.add_argument("master"); m.add_argument("--r1", required=True); m.add_argument("--r2", required=True)
    m.add_argument("--out"); m.add_argument("--prefill-final", action="store_true")
    st = sub.add_parser("status"); st.add_argument("master"); st.add_argument("--write", action="store_true", help="save recomputed statuses into the workbook")
    e = sub.add_parser("export"); e.add_argument("master"); e.add_argument("--out", required=True)
    e.add_argument("--reviewer1-id", required=True); e.add_argument("--reviewer2-id", required=True); e.add_argument("--allow-non-malayalam", action="store_true")
    a = p.parse_args(argv)
    {"template": cmd_template, "split": cmd_split, "merge": cmd_merge, "status": cmd_status, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
