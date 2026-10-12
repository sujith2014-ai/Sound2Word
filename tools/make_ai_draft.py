#!/usr/bin/env python3
"""Generates data/drafts/ai_draft_candidates.{csv,xlsx}: an AI-GENERATED, UNVERIFIED candidate list.

Nothing here is ground truth. The words, meanings, romanisations and every proposed contrast come from a
language model that did not consult a dictionary, a native speaker or any audio. They exist only to give a
native Malayalam speaker something concrete to accept, correct or reject. The output is deliberately NOT
loadable by review_sheet.py or the dataset tools. Regenerate: python3 tools/make_ai_draft.py
"""
import csv
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "drafts")
TAG = "#s2w-ai-draft v1 UNVERIFIED - NOT A WORD LIST - DO NOT IMPORT AS VERIFIED"
COLS = ["draft_id", "role", "malayalam_ai_draft", "romanisation_approx", "english_meaning_ai", "proposed_pair_id",
        "proposed_contrast", "proposed_contrast_detail", "ai_self_rating", "ai_concerns", "oov_closeness_guess", "status"]

MORE, LESS = "more sure", "less sure"
V = "vowel_length"
G = "gemination"

# (word, roman, meaning) ; pair rows: (pair_id, contrast, detail, rating, concern, a, b)
PAIRS = [
    ("DP01", V, "a vs aa (short vs long a)", MORE, "Both very common. Check the long vowel is clearly longer in isolated speech.",
     ("കലം", "kalaṁ", "pot / cooking vessel"), ("കാലം", "kālaṁ", "time, era, season")),
    ("DP02", V, "aa vs a (long vs short a)", MORE, "'താരം' may be heard as 'star (celebrity)'; 'തരം' as 'kind, type, grade'. Confirm both are natural.",
     ("തരം", "taraṁ", "kind, type, grade"), ("താരം", "tāraṁ", "star; celebrity")),
    ("DP03", V, "a vs aa", MORE, "Regional pronunciation of 'പടം' (picture/film) may vary.",
     ("പടം", "paṭaṁ", "picture, film"), ("പാടം", "pāṭaṁ", "paddy field")),
    ("DP04", V, "a vs aa", MORE, "Short words; confirm vowel length is the only audible difference.",
     ("മല", "mala", "hill, mountain"), ("മാല", "māla", "garland, necklace")),
    ("DP05", V, "i vs ii (short vs long i)", MORE, "'നില' has several senses (floor, level, state); pick one context if needed.",
     ("നില", "nila", "floor, level, state"), ("നീല", "nīla", "blue")),
    ("DP06", V, "o vs oo (short vs long o)", MORE, "Short ൊ vs long ോ: confirm the contrast is length, not quality.",
     ("കൊടി", "koṭi", "flag"), ("കോടി", "kōṭi", "ten million, crore")),
    ("GP01", G, "single ṭ vs geminate ṭṭ", MORE, "Different parts of speech/semantic fields; confirm both familiar.",
     ("അട", "aṭa", "rice dumpling (snack)"), ("അട്ട", "aṭṭa", "leech")),
    ("GP02", G, "single ḷ vs geminate ḷḷ", MORE, "Close in sound to GP03 (കളം/വളം, കള്ളം/വള്ളം): useful hard negatives but may confuse a learner.",
     ("കളം", "kaḷaṁ", "court, square, drawn pattern"), ("കള്ളം", "kaḷḷaṁ", "lie, falsehood")),
    ("GP03", G, "single ḷ vs geminate ḷḷ", MORE, "Close in sound to GP02. 'വളം' = fertiliser (also other senses).",
     ("വളം", "vaḷaṁ", "fertiliser, manure"), ("വള്ളം", "vaḷḷaṁ", "boat, canoe")),
    ("GP04", G, "single ṭ vs geminate ṭṭ", MORE, "Both extremely common.",
     ("പടി", "paṭi", "step, stair"), ("പട്ടി", "paṭṭi", "dog")),
    ("GP05", G, "single ṭ vs geminate ṭṭ", LESS, "'കട്ടി' has several senses (thickness, solid, lump); meaning may need fixing to one sense.",
     ("കടി", "kaṭi", "bite"), ("കട്ടി", "kaṭṭi", "thick(ness), solid")),
    ("GP06", G, "റ vs റ്റ", LESS, "Is റ vs റ്റ a pure single/geminate contrast, or does quality also differ? Needs a phonetically aware reviewer.",
     ("കുറി", "kuṟi", "mark, chit"), ("കുറ്റി", "kuṟṟi", "peg, stump, post")),
]
SINGLES = [
    ("ആന", "āna", "elephant"), ("പൂച്ച", "pūcca", "cat"), ("ചെവി", "cevi", "ear"), ("മഴ", "maḻa", "rain"),
    ("പുഴ", "puḻa", "river"), ("പഴം", "paḻaṁ", "banana, fruit"), ("അമ്മ", "amma", "mother"), ("ചക്ക", "cakka", "jackfruit"),
    ("തേങ്ങ", "tēṅṅa", "coconut"), ("പുസ്തകം", "pustakaṁ", "book"), ("ആകാശം", "ākāśaṁ", "sky"),
    ("വിദ്യാലയം", "vidyālayaṁ", "school"),
]
SINGLE_CONCERN = {
    "ആന": "", "പൂച്ച": "", "ചെവി": "", "മഴ": "Contains ഴ; shares it with പുഴ, പഴം (intentional spread, but similar endings).",
    "പുഴ": "Shares ഴ with മഴ, പഴം.", "പഴം": "'banana' vs general 'fruit': pick the sense speakers use.", "അമ്മ": "Geminate mm in a single word (no pair).",
    "ചക്ക": "", "തേങ്ങ": "Conjunct ങ്ങ: check spelling.", "പുസ്തകം": "Conjunct സ്ത: check spelling.",
    "ആകാശം": "Longer word (3 syllables) for length variety.", "വിദ്യാലയം": "Long word (5 syllables); formal register.",
}
OOV = [
    ("ചിരി", "ciri", "laugh, smile", "far"), ("പക്ഷി", "pakṣi", "bird", "far"), ("തീ", "tī", "fire", "far (one syllable)"),
    ("കുട്ടി", "kuṭṭi", "child", "near (കട്ടി, കടി, പടി, പട്ടി)"), ("വഴി", "vaḻi", "way, road", "near (പഴം, മഴ, പുഴ share ഴ)"),
    ("ഓണം", "ōṇaṁ", "Onam (festival)", "far"), ("മുറി", "muṟi", "room", "near (കുറി)"),
    ("ചന്ദനം", "candanaṁ", "sandalwood", "far"), ("നക്ഷത്രം", "nakṣatraṁ", "star (formal)", "far (same meaning as താരം, unrelated sound)"),
    ("ഗ്രാമം", "grāmaṁ", "village", "far"),
]


def rows():
    out = []
    n = 0
    for pid, ct, detail, rating, concern, a, b in PAIRS:
        for w in (a, b):
            n += 1
            out.append(["DRAFT-V%02d" % n, "vocab", w[0], w[1], w[2], pid, ct, detail, rating, concern, "", "UNVERIFIED"])
    for w, r, m in SINGLES:
        n += 1
        out.append(["DRAFT-V%02d" % n, "vocab", w, r, m, "", "none", "", MORE, SINGLE_CONCERN[w], "", "UNVERIFIED"])
    for i, (w, r, m, close) in enumerate(OOV, start=1):
        out.append(["DRAFT-U%02d" % i, "oov", w, r, m, "", "none", "", MORE, "Must NOT be enrolled; unknown-word test only.", close, "UNVERIFIED"])
    return out


def write_csv(path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(TAG + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLS)
        w.writerows(rows())


README = [
    ("h", "AI-GENERATED DRAFT - UNVERIFIED"),
    ("b", "Every word, meaning, romanisation and proposed contrast in this file was generated by an AI language model. "
          "It did not consult a dictionary, a native speaker, or any audio. Spelling, meaning, dialect, familiarity and every "
          "'minimal pair' claim may be wrong. This is NOT ground truth and must not be used as a word list."),
    ("t", "Purpose: give a native Malayalam speaker something concrete to accept, correct or reject. You are free to discard any or all of it; your judgement overrides this list."),
    ("s", "Rules"),
    ("t", "1. Do not import these rows into the master review workbook as if they were your own proposal. If you adopt a word, enter it yourself in the Master sheet, in your own judgement and spelling."),
    ("t", "2. Reviewers must NOT see this file's 'AI self-rating' as evidence. It is the model's own guess, not a measurement."),
    ("t", "3. Check each word independently: spelling (incl. any ZWJ/ZWNJ), meaning, familiarity, pronunciation and regional variants, and for each pair whether the stated contrast is genuine and is the only audible difference."),
    ("t", "4. A word can be perfectly good Malayalam and still not form a useful minimal pair."),
    ("s", "Design choices (not linguistic claims)"),
    ("t", "36 vocab candidates: 6 'vowel length' pairs, 6 'gemination' pairs, 12 single words; 10 unknown-word (oov) candidates that must never be enrolled. "
          "No chillu letters are used on purpose, to avoid ZWJ encoding ambiguity. Romanisation is approximate, NOT IPA. "
          "Several candidates are phonetically close to one another (e.g. കളം/വളം, മല/മാല/മഴ); that is intended as a harder test but may not suit a first pilot."),
    ("t", "This file cannot be loaded by review_sheet.py or the Sound2Word dataset tools by design."),
]


def write_xlsx(path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    wb = Workbook()
    ws = wb.active
    ws.title = "READ FIRST"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 100
    r = 1
    for kind, text in README:
        c = ws.cell(row=r, column=1, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if kind == "h":
            c.font = Font(name="Arial", size=16, bold=True, color="C00000")
        elif kind == "b":
            c.font = Font(name="Arial", size=11, bold=True)
            c.fill = PatternFill("solid", fgColor="FFC7CE")
            ws.row_dimensions[r].height = 14 * (1 + len(text) // 90)
        elif kind == "s":
            c.font = Font(name="Arial", size=11, bold=True, color="1F3864")
        else:
            c.font = Font(name="Arial", size=10)
            ws.row_dimensions[r].height = 14 * (1 + len(text) // 95)
        r += 1
    d = wb.create_sheet("AI draft (UNVERIFIED)")
    thin = Side(style="thin", color="BFBFBF")
    heads = ["Draft ID", "Role", "Malayalam (AI draft)", "Romanisation (approx., not IPA)", "English meaning (AI)", "Proposed pair ID",
             "Proposed contrast", "Contrast detail (AI)", "AI self-rating (a guess, not evidence)", "AI concerns", "Unknown-word closeness to enrolled (AI guess)", "Status"]
    widths = [12, 8, 20, 20, 28, 12, 16, 24, 16, 52, 30, 14]
    for i, (h, w) in enumerate(zip(heads, widths), start=1):
        c = d.cell(row=1, column=i, value=h)
        c.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="7F0000")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        d.column_dimensions[c.column_letter].width = w
    d.row_dimensions[1].height = 48
    for ri, row in enumerate(rows(), start=2):
        for ci, v in enumerate(row, start=1):
            c = d.cell(row=ri, column=ci, value=v)
            c.font = Font(name="Arial", size=10, bold=(ci == 12))
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            c.number_format = "@"
            if ci == 12:
                c.fill = PatternFill("solid", fgColor="FFC7CE")
    d.freeze_panes = "D2"
    wb.save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    write_csv(os.path.join(OUT, "ai_draft_candidates.csv"))
    try:
        write_xlsx(os.path.join(OUT, "ai_draft_candidates.xlsx"))
    except ImportError:
        print("note: openpyxl missing; wrote CSV only", file=sys.stderr)
    print("wrote", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
