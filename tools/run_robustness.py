#!/usr/bin/env python3
"""Run the recognition pipeline over SIMULATED degradation scenarios (software validation only).

  run_robustness.py --dataset DIR --settings FILE --work-dir DIR [--scenarios a,b,c] [--build build] [--seed 1]

For each scenario: s2w_degrade derives a dataset (enrol clean, calib degraded, test never read), then
s2w_eval run --split calib measures the matcher UNCHANGED with the given settings. Results are printed as a
table and written to <work-dir>/robustness_summary.json. Nothing here tunes anything or touches the test split.
Simulated degradations are not real rooms, microphones or speakers: a good number here proves the code
handles that signal change, not that Malayalam recognition is robust.
"""
import argparse
import json
import os
import subprocess
import sys

BANNER = ("*** SIMULATED CONDITIONS: software validation only. NOT evidence about real speakers, rooms, microphones or Malayalam accuracy. ***")


def rate(node):
    return None if node is None or node.get("value") is None else node["value"]


def fmt(x):
    return "  n/a" if x is None else "%5.1f%%" % (100 * x)


def run_one(build, dataset, settings, work, scenario, seed):
    out = os.path.join(work, "ds_" + scenario)
    runs = os.path.join(work, "runs")
    r = subprocess.run([os.path.join(build, "s2w_degrade"), "--dataset", dataset, "--out-dir", out, "--scenario", scenario, "--seed", str(seed)], capture_output=True, text=True)
    if r.returncode != 0:
        return {"scenario": scenario, "error": r.stderr.strip() or r.stdout.strip()}
    run_id = "rob-" + scenario
    r = subprocess.run([os.path.join(build, "s2w_eval"), "run", "--dataset", out, "--settings", settings, "--split", "calib", "--out-dir", runs,
                        "--run-id", run_id, "--created-utc", "1970-01-01T00:00:00Z", "--min-group-queries", "1000000"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"scenario": scenario, "error": r.stderr.strip()}
    with open(os.path.join(runs, run_id, "result.json"), encoding="utf-8") as f:
        res = json.load(f)
    m = res["metrics"]
    return {"scenario": scenario, "simulated_recordings": res["evidence"]["simulated_recordings"], "n_known": m["known_words"]["n"], "n_unknown": m["unknown_words"]["n"],
            "n_unusable": m["n_unusable_queries"], "top1": rate(m["known_words"]["top1_accuracy"]), "top3": rate(m["known_words"]["top3_accuracy"]),
            "false_reject": rate(m["known_words"]["false_reject_rate"]), "unknown_false_accept": rate(m["unknown_words"]["false_accept_rate"]),
            "rejection_active": m["rejection_active"], "scope": res["evidence"]["scope_statement"]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--settings", required=True)
    p.add_argument("--work-dir", required=True)
    p.add_argument("--scenarios")
    p.add_argument("--build", default="build")
    p.add_argument("--seed", type=int, default=1)
    a = p.parse_args(argv)
    if os.path.exists(a.work_dir) and os.listdir(a.work_dir):
        sys.exit("error: work dir is not empty: %s" % a.work_dir)
    os.makedirs(a.work_dir, exist_ok=True)
    if a.scenarios:
        names = a.scenarios.split(",")
    else:
        r = subprocess.run([os.path.join(a.build, "s2w_degrade"), "--list"], capture_output=True, text=True, check=True)
        names = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
    rows = [run_one(a.build, a.dataset, a.settings, a.work_dir, n, a.seed) for n in names]
    print(BANNER)
    print("%-16s %6s %7s %7s %9s %6s" % ("scenario", "known", "top1", "top3", "unusable", "unk"))
    for r in rows:
        if "error" in r:
            print("%-16s ERROR %s" % (r["scenario"], r["error"][:80]))
        else:
            print("%-16s %6d %7s %7s %9d %6d" % (r["scenario"], r["n_known"], fmt(r["top1"]), fmt(r["top3"]), r["n_unusable"], r["n_unknown"]))
    summary = {"schema": "s2w-robustness-summary", "banner": BANNER, "dataset": os.path.basename(os.path.normpath(a.dataset)), "settings_file": os.path.basename(a.settings),
               "seed": a.seed, "split": "calib (test split never read)", "rows": rows}
    with open(os.path.join(a.work_dir, "robustness_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")
    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
