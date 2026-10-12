#!/usr/bin/env python3
"""Noise-matched enrolment experiment (SIMULATED conditions, software validation only).

Question: with the matcher, features, vocabulary and split policy held fixed, do templates enrolled under noisy
conditions recover accuracy on noisy queries, compared with clean templates? The full enrolment x query matrix
is run, including MISMATCHED pairs, because matched noise can look good merely because the distortions are alike.

  run_enrolment_experiment.py --dataset DIR --settings FILE --work-dir DIR [--seeds 1,2,3]
        [--enrol clean,snr10_white,snr10_pink,far_mic,mix:snr10_white]
        [--queries identity,snr20_white,snr10_white,snr0_white,snr10_pink,far_mic,telephone,hum_snr10]
        [--build build]

Enrolment spec: "clean" | "<scenario>" (every template degraded) | "mix:<scenario>" (clean + degraded copies).
Only the calib split is evaluated and the test split is never read. Nothing is tuned. The matcher is untouched.
Outputs: <work-dir>/experiment.json and report.md. Rejection is reported threshold-free (AUC of correct-known vs
unknown best distances) plus an in-sample FRR at <=5% FAR; thresholds do not transfer between enrolment conditions.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys

BANNER = ("*** SIMULATED CONDITIONS, SYNTHETIC DATA: software validation only. NOT evidence about real speakers, "
          "rooms, microphones or Malayalam recognition. ***")
DEFAULT_ENROL = ["clean", "snr10_white", "snr10_pink", "far_mic", "mix:snr10_white"]
DEFAULT_QUERIES = ["identity", "snr20_white", "snr10_white", "snr0_white", "snr10_pink", "far_mic", "telephone", "hum_snr10"]


# ---- pure helpers (unit-tested) ------------------------------------------------------------------
def wilson(k, n, z=1.96):
    """95% Wilson interval for a proportion; (None, None) if n == 0."""
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def auc_lower_is_genuine(genuine, impostor):
    """P(genuine distance < impostor distance), ties count 1/2. None if either set is empty."""
    if not genuine or not impostor:
        return None
    s = 0.0
    for g in genuine:
        for i in impostor:
            s += 1.0 if g < i else 0.5 if g == i else 0.0
    return s / (len(genuine) * len(impostor))


def frr_at_far(genuine, impostor, max_far=0.05):
    """In-sample FRR when the accept threshold is the largest value letting <= max_far of impostors through."""
    if not genuine or not impostor:
        return None
    imp = sorted(impostor)
    allowed = min(int(math.floor(max_far * len(imp))), len(imp) - 1)
    t = math.nextafter(imp[allowed], -math.inf)
    return sum(1 for g in genuine if g > t) / len(genuine)


def recovery_fraction(clean_enrol, matched, ceiling):
    """Share of the loss (ceiling - clean_enrol) that the alternative recovers; None if there is no loss to recover."""
    if ceiling is None or clean_enrol is None or matched is None or ceiling - clean_enrol < 0.05:
        return None
    return (matched - clean_enrol) / (ceiling - clean_enrol)


def parse_enrol(spec):
    if spec == "clean":
        return None, None
    if spec.startswith("mix:"):
        return spec[4:], "mix"
    return spec, "replace"


def query_label(q):
    return "clean" if q == "identity" else q


# ---- running ---------------------------------------------------------------------------------------
def run_cell(build, dataset, settings, work, enrol, query, seed):
    tag = "%s__%s__s%d" % (enrol.replace(":", "-"), query, seed)
    out = os.path.join(work, "cells", tag)
    runs = os.path.join(work, "runs")
    cmd = [os.path.join(build, "s2w_degrade"), "--dataset", dataset, "--out-dir", out, "--scenario", query, "--seed", str(seed)]
    escn, mode = parse_enrol(enrol)
    if escn:
        cmd += ["--enrol-scenario", escn, "--enrol-mode", mode]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout).strip()[:300]}
    r = subprocess.run([os.path.join(build, "s2w_eval"), "run", "--dataset", out, "--settings", settings, "--split", "calib", "--out-dir", runs, "--run-id", tag,
                        "--created-utc", "1970-01-01T00:00:00Z", "--min-group-queries", "1000000"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": r.stderr.strip()[:300]}
    with open(os.path.join(runs, tag, "result.json"), encoding="utf-8") as f:
        res = json.load(f)
    with open(os.path.join(runs, tag, "summary.txt"), encoding="utf-8") as f:
        templates = int([ln for ln in f if ln.startswith("templates=")][0].split()[0].split("=")[1])
    gen, imp = [], []
    with open(os.path.join(runs, tag, "queries.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["usable"] != "1" or not row["top1"]:
                continue
            d = float(row["top1_distance"])
            if row["role"] == "vocab" and row["top1"] == row["word_id"]:
                gen.append(d)
            elif row["role"] == "oov":
                imp.append(d)
    k = res["metrics"]["known_words"]
    cell = {"templates": templates, "n_known": k["n"], "n_unknown": res["metrics"]["unknown_words"]["n"], "n_unusable": res["metrics"]["n_unusable_queries"],
            "top1": [k["top1_accuracy"]["num"], k["top1_accuracy"]["den"]], "top3": [k["top3_accuracy"]["num"], k["top3_accuracy"]["den"]],
            "top5": [k["top5_accuracy"]["num"], k["top5_accuracy"]["den"]], "auc": auc_lower_is_genuine(gen, imp), "frr_at_far5_insample": frr_at_far(gen, imp),
            "n_genuine_dist": len(gen), "n_impostor_dist": len(imp), "median_genuine_distance": sorted(gen)[len(gen) // 2] if gen else None,
            "median_impostor_distance": sorted(imp)[len(imp) // 2] if imp else None,
            "simulated_recordings": res["evidence"]["simulated_recordings"], "scope": res["evidence"]["scope_statement"]}
    return cell


def rate(pair):
    return None if not pair or pair[1] == 0 else pair[0] / pair[1]


def aggregate(cells_by_seed, enrols, queries, key):
    """pooled over seeds: (num, den) summed; mean/min/max of per-seed rates; Wilson on pooled counts."""
    out = {}
    for e in enrols:
        for q in queries:
            per = [cells_by_seed[s].get((e, q)) for s in sorted(cells_by_seed)]
            per = [c for c in per if c and "error" not in c]
            if not per:
                out[(e, q)] = None
                continue
            nums = sum(c[key][0] for c in per)
            dens = sum(c[key][1] for c in per)
            rates = [rate(c[key]) for c in per]
            lo, hi = wilson(nums, dens)
            out[(e, q)] = {"rate": nums / dens if dens else None, "num": nums, "den": dens, "per_seed": rates, "min": min(rates), "max": max(rates), "ci95": [lo, hi]}
    return out


def fmt(x, w=6):
    return ("%*s" % (w, "n/a")) if x is None else "%*.1f" % (w, 100 * x)


def table(title, agg, enrols, queries, key="rate"):
    lines = [title, "| enrolment \\ query | " + " | ".join(query_label(q) for q in queries) + " |", "|---|" + "---|" * len(queries)]
    for e in enrols:
        cells = []
        for q in queries:
            a = agg.get((e, q))
            cells.append("n/a" if not a or a[key] is None else ("%.1f" % (100 * a[key]) if key == "rate" else "%.3f" % a[key]))
        lines.append("| %s | %s |" % (e, " | ".join(cells)))
    return "\n".join(lines)


def git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--settings", required=True)
    p.add_argument("--work-dir", required=True)
    p.add_argument("--seeds", default="1")
    p.add_argument("--enrol", default=",".join(DEFAULT_ENROL))
    p.add_argument("--queries", default=",".join(DEFAULT_QUERIES))
    p.add_argument("--build", default="build")
    a = p.parse_args(argv)
    enrols, queries = a.enrol.split(","), a.queries.split(",")
    seeds = [int(x) for x in a.seeds.split(",")]
    if "clean" not in enrols or "identity" not in queries:
        sys.exit("error: the matrix must include the clean enrolment and the clean (identity) query condition: they are the baseline")
    if os.path.exists(a.work_dir) and os.listdir(a.work_dir):
        sys.exit("error: work dir is not empty: %s" % a.work_dir)
    os.makedirs(a.work_dir, exist_ok=True)

    cells_by_seed = {}
    for s in seeds:
        cells_by_seed[s] = {}
        for e in enrols:
            for q in queries:
                c = run_cell(a.build, a.dataset, a.settings, a.work_dir, e, q, s)
                cells_by_seed[s][(e, q)] = c
                if "error" in c:
                    print("ERROR cell enrol=%s query=%s seed=%d: %s" % (e, q, s, c["error"]), file=sys.stderr)
    errors = sum(1 for s in cells_by_seed.values() for c in s.values() if "error" in c)

    agg = {k: aggregate(cells_by_seed, enrols, queries, k) for k in ("top1", "top3", "top5")}
    auc = {}
    for e in enrols:
        for q in queries:
            vals = [cells_by_seed[s][(e, q)].get("auc") for s in seeds if "error" not in cells_by_seed[s][(e, q)]]
            vals = [v for v in vals if v is not None]
            auc[(e, q)] = {"auc": sum(vals) / len(vals)} if vals else None

    # comparisons per query condition
    comps = []
    ceiling = (agg["top1"].get(("clean", "identity")) or {}).get("rate")
    noisy_enrols = [e for e in enrols if e != "clean" and not e.startswith("mix:")]
    for q in queries:
        if q == "identity":
            continue
        clean = (agg["top1"].get(("clean", q)) or {}).get("rate")
        matched = (agg["top1"].get((q, q)) or {}).get("rate") if q in enrols else None
        mix = (agg["top1"].get(("mix:" + q, q)) or {}).get("rate") if ("mix:" + q) in enrols else None
        mism = [(agg["top1"].get((e, q)) or {}).get("rate") for e in noisy_enrols if e != q]
        mism = [m for m in mism if m is not None]
        comps.append({"query": q, "clean_enrol": clean, "matched": matched, "mix": mix,
                      "mismatched_noisy_mean": sum(mism) / len(mism) if mism else None, "n_mismatched": len(mism),
                      "matched_minus_clean": None if matched is None or clean is None else matched - clean,
                      "matched_minus_mismatched": None if matched is None or not mism else matched - sum(mism) / len(mism),
                      "recovery_matched": recovery_fraction(clean, matched, ceiling), "recovery_mix": recovery_fraction(clean, mix, ceiling)})
    clean_cost = {e: (agg["top1"].get((e, "identity")) or {}).get("rate") for e in enrols}

    with open(a.settings, "rb") as f:
        settings_sha = hashlib.sha256(f.read()).hexdigest()
    src = subprocess.run([os.path.join(a.build, "s2w_dataset_check"), a.dataset, "--no-audio"], capture_output=True, text=True).stdout
    manifest = next((ln.split("=", 1)[1] for ln in src.splitlines() if ln.startswith("manifest_sha256=")), "unknown")
    dataset_kind = next((ln.split("=", 1)[1] for ln in src.splitlines() if ln.startswith("kind=")), "unknown")
    meta = {"schema": "s2w-enrolment-experiment-v1", "banner": BANNER, "dataset_kind": dataset_kind, "source_manifest_sha256": manifest,
            "settings_file": os.path.basename(a.settings), "settings_file_sha256": settings_sha, "seeds": seeds, "enrol": enrols, "queries": queries,
            "split": "calib only; test split never read", "matcher": "unchanged (baseline matcher, no tuning)",
            "git_commit": git("rev-parse", "HEAD"), "git_describe": git("describe", "--tags", "--always"), "cell_errors": errors,
            "n_known_per_cell_per_seed": next((c["n_known"] for c in cells_by_seed[seeds[0]].values() if "error" not in c), None)}
    ser = lambda d: {"%s | %s" % (e, query_label(q)): v for (e, q), v in d.items()}  # noqa: E731
    result = {"meta": meta, "top1": ser(agg["top1"]), "top3": ser(agg["top3"]), "top5": ser(agg["top5"]), "auc": ser(auc), "comparisons": comps,
              "clean_query_top1_by_enrolment": clean_cost,
              "cells": {str(s): {"%s | %s" % (e, query_label(q)): c for (e, q), c in cs.items()} for s, cs in cells_by_seed.items()}}
    with open(os.path.join(a.work_dir, "experiment.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")

    md = [BANNER, "", "# Noise-matched enrolment experiment", "",
          "Dataset kind: %s | seeds: %s | calib split only | matcher unchanged | commit %s" % (dataset_kind, seeds, meta["git_describe"]), "",
          "Known-word queries per cell per seed: %s. Percentages are top-N accuracy on those queries, pooled over seeds." % meta["n_known_per_cell_per_seed"], "",
          table("## Top-1 accuracy (%)", agg["top1"], enrols, queries), "", table("## Top-3 accuracy (%)", agg["top3"], enrols, queries), "",
          table("## Top-5 accuracy (%)", agg["top5"], enrols, queries), "",
          table("## Rejection separability: AUC of correct-known vs unknown best distance (1.0 = perfectly separable, 0.5 = none)", auc, enrols, queries, key="auc"), "",
          "## Matched vs clean vs mismatched enrolment (top-1, per noisy query condition)", "",
          "| query | clean enrol | matched | mix (clean+noisy) | mismatched-noisy mean (n) | matched - clean | matched - mismatched | recovery(matched) | recovery(mix) |", "|---|---|---|---|---|---|---|---|---|"]
    for c in comps:
        f1 = lambda v: "n/a" if v is None else "%.1f" % (100 * v)  # noqa: E731
        md.append("| %s | %s | %s | %s | %s (%d) | %s | %s | %s | %s |" % (query_label(c["query"]), f1(c["clean_enrol"]), f1(c["matched"]), f1(c["mix"]),
                  f1(c["mismatched_noisy_mean"]), c["n_mismatched"], f1(c["matched_minus_clean"]), f1(c["matched_minus_mismatched"]),
                  "n/a" if c["recovery_matched"] is None else "%.0f%%" % (100 * c["recovery_matched"]), "n/a" if c["recovery_mix"] is None else "%.0f%%" % (100 * c["recovery_mix"])))
    md += ["", "recovery = (alternative - clean enrol) / (clean-enrol/clean-query ceiling - clean enrol); n/a when there is little loss to recover.", "",
           "## Cost on clean queries (top-1 %, by enrolment condition)", "", "| enrolment | top-1 on clean queries |", "|---|---|"]
    for e in enrols:
        md.append("| %s | %s |" % (e, "n/a" if clean_cost[e] is None else "%.1f" % (100 * clean_cost[e])))
    md += ["", "## Reading these numbers", "",
           "- Matched-noise cells can look good because template and query distortions are alike; compare with the mismatched columns and with clean-query cost.",
           "- Per-seed spread and Wilson 95% intervals (pooled counts, approximate because the same recordings recur across seeds) are in experiment.json; small differences are noise.",
           "- Distance scales differ by enrolment condition, so rejection thresholds calibrated for one condition do not transfer. AUC is threshold-free.",
           "- A fixture of tone sequences, simulated noise, one matcher. This cannot say whether real Malayalam speech behaves the same way.", ""]
    with open(os.path.join(a.work_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("\n".join(md))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
