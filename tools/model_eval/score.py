import difflib, json, os, re, statistics, sys, unicodedata
sp = os.path.dirname(os.path.abspath(__file__))
sys.stdout.reconfigure(encoding="utf-8")
LINES = [(6.62, "നീ പറയുന്ന പല കള്ള കഥകളും അലംങ്കൃതമായി തീരാം"), (11.73, "പകരുന്ന തരം പ്രവണതകളും കരളിതളായി വിരിയാം"),
         (16.13, "നിൻ തിരുമുന്നിലായി പതുങ്ങുന്നിതാ മടിക്കുന്നിതാ"), (20.88, "നിൻ്റെ താളത്തിൽ തുള്ളാൻ അംശമായി തീരാനേ"),
         (26.55, "നിൻ്റെ പല കള്ള കഥകളും അലംങ്കൃതമായി തീരാം"), (31.09, "പകരുന്ന തരം പ്രവണതകളും കരളിതളായി വിരിയാം"),
         (45.94, "പാതാളം ഊരായാലും"), (50.29, "നീ ഒന്ന് എന്നെ വിളിച്ചാൽ അന്നാശാഢം"), (54.57, "ഈ തത്തമ്മ പെണ്ണെന്നെ കടിച്ചാൽ ഞാൻ മുളകായി മാറും")]
CH = {"ൻ": "ന്", "ൺ": "ണ്", "ൽ": "ല്", "ൾ": "ള്", "ർ": "ര്", "ൿ": "ക്"}


def norm(s):
    s = unicodedata.normalize("NFC", s)
    s = "".join(CH.get(c, c) for c in s).replace("‌", "").replace("‍", "")
    s = s.replace("ം", "ം")
    return re.sub(r"[^ഀ-ൿ\s]", " ", s)


def words(s):
    return norm(s).split()


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


ref_words = [w for _, l in LINES for w in words(l)]
ref_chars = "".join(ref_words)
print("reference: %d words, %d chars, first 60 s of the song (9 lines)\n" % (len(ref_words), len(ref_chars)))
out = []
for name, f in [("indic-core", "r_indic_ml"), ("vegam-ml", "r_vegam_ml"), ("large-v3-turbo", "r_turbo_ml")]:
    j = json.load(open(os.path.join(sp, f + ".json"), encoding="utf-8"))
    hw = words(j["text"])
    wer = lev(ref_words, hw) / len(ref_words)
    cer = lev(ref_chars, "".join(hw)) / len(ref_chars)
    sm = difflib.SequenceMatcher(None, ref_chars, "".join(hw), autojunk=False)
    cov = sum(b.size for b in sm.get_matching_blocks()) / len(ref_chars)
    out.append((name, wer, cer, cov, j))
    print("%-15s WER %5.0f%%  CER %5.0f%%  ref chars recovered %4.0f%%  | load %.1fs, transcribe %.1fs for 60 s audio (RTF %.2f)" %
          (name, wer * 100, cer * 100, cov * 100, j["load_s"], j["transcribe_s"], j["transcribe_s"] / 60))

# --- timing: only Whisper-family models give word times; map hypothesis words to reference lines by character alignment
print("\nTiming of line starts (Whisper-family only; indic-core returns no timestamps)")
starts = [t for t, _ in LINES]
for name, wer, cer, cov, j in out:
    if "words" not in j or not j["words"]:
        print("%-15s no word timestamps" % name)
        continue
    hw = [(norm(w["t"]).replace(" ", ""), w["s"], w["e"]) for w in j["words"]]
    hw = [x for x in hw if x[0]]
    hyp_chars, owner = "", []
    for k, (t, s, e) in enumerate(hw):
        hyp_chars += t
        owner += [k] * len(t)
    ref_pos, line_of = "", []
    for li, (_, l) in enumerate(LINES):
        t = "".join(words(l))
        ref_pos += t
        line_of += [li] * len(t)
    sm = difflib.SequenceMatcher(None, ref_pos, hyp_chars, autojunk=False)
    first = {}
    for blk in sm.get_matching_blocks():
        if blk.size < 4:
            continue
        for d in range(blk.size):
            li, k = line_of[blk.a + d], owner[blk.b + d]
            if li not in first or hw[k][1] < first[li]:
                first[li] = hw[k][1]
    got = sorted(first)
    if 0 not in first:
        print("%-15s line 1 not matched; cannot sync" % name)
        continue
    off = first[0] - starts[0]
    errs = [first[li] - starts[li] - off for li in got if li > 0]
    raw = [first[li] - starts[li] for li in got]
    print("%-15s lines located %d/9; offset from line 1 = %+.2f s; error after sync (ms): %s | median abs %.0f ms"
          % (name, len(got), off, ", ".join("L%d %+d" % (li + 1, round((first[li] - starts[li] - off) * 1000)) for li in got if li > 0),
             statistics.median(abs(e) for e in errs) * 1000 if errs else float("nan")))
