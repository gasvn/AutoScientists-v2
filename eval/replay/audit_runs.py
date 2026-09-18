#!/usr/bin/env python3
"""Measure how much compute a v1 run spent on questions it had already answered.

This reads nothing but `logs/experiments.jsonl` from completed runs, so it can
be pointed at history that already exists. Every number here is mechanical —
no model is asked to judge anything — which is the point: it establishes the
headroom the hypothesis graph is aiming at before any of it is built, from
data that is already on disk.

The four things it counts, and why each one is what it is:

  dead-family   experiments run in a mechanism family that had already
                produced three or more DISCARDs and no KEEP. v1 had a rule
                for this (ROLE-ANALYST Step 2) but it only fired on an
                analyst's next cycle, so the experiments already in flight
                and in the queue still ran.

  post-KEEP     experiments in a family that ran AFTER that family had
                already banked a KEEP, and then discarded. This is the
                population the `alternative` relation operates on, not waste
                by itself — refining a family that just worked is exactly
                what you should do. Reported for scale, not as an indictment.

  exhausted     the strict subset that is hard to defend: the third or later
                CONSECUTIVE discard in a group since that group last produced
                a KEEP. By then the group has said no three times in a row and
                the run kept asking.

                Reported against two groupings. `fam` derives a mechanism
                family from the exp_id, which makes it sensitive to each run's
                naming convention and therefore NOT comparable across runs —
                ar_p2_s46 splits into 33 families and ar_p4_s46 into 5 purely
                because of how their ids were written. `team` uses the run's
                own team labels, which is both consistent and directly
                meaningful: v1 told teams to abandon a hypothesis after three
                refuting results (ROLE-ANALYST Step 0.3), so this column is
                how often a team kept going past the bar it had set itself.

  redundant     experiments that overlapped in wall-clock with another
  concurrency   experiment of the same family. Six GPU agents each picking
                from their own queue will do this; a batch endpoint that
                knows the relations will not.

  after-best    experiments that ran after the run's best metric had already
                been reached. Not all of this is waste — search has to
                continue past its own high-water mark — but it bounds how
                much of the run was spent not improving anything.

Usage:
    python audit_runs.py /path/to/claw --runs ar_p4_s46 selfopt_v1 ...
    python audit_runs.py /path/to/claw --all --json out.json
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# exp_id conventions differ per run. Strip the decoration and keep the first
# one or two meaningful tokens, which is what the teams actually used as a
# mechanism family ("exp_thr_001" -> "thr", "fe_006_ecfp" -> "fe").
_HEX = re.compile(r"^[0-9a-f]{6,}$")
_NUM = re.compile(r"^v?\d+[a-z]?$")


def family(exp_id: str) -> str:
    toks = [t for t in re.split(r"[_\-.]", exp_id or "") if t]
    if toks and toks[0] in ("exp", "experiment"):
        toks = toks[1:]
    toks = [t for t in toks if not _HEX.match(t) and not _NUM.match(t)]
    if not toks:
        return exp_id or "unknown"
    # Two tokens where the first is a very short tag ("tp", "fe"), else one.
    if len(toks[0]) <= 3 and len(toks) > 1:
        return toks[0]
    return "_".join(toks[:2])


def parse_ts(v):
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load(path: Path):
    """Normalize the several shapes experiments.jsonl has taken across runs."""
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        metric = r.get("metric")
        if metric is None:
            for k in ("val_bpb", "score", "val_score"):
                if r.get(k) is not None:
                    metric = r[k]
                    break
        outcome = (r.get("outcome") or r.get("verdict") or "").upper()
        end = parse_ts(r.get("completed_at")) or parse_ts(r.get("harvested_at"))
        start = parse_ts(r.get("started_at"))
        secs = r.get("training_seconds") or r.get("slot_seconds")
        if start is None and end is not None and secs:
            start = end - timedelta(seconds=float(secs))
        rows.append({
            "exp_id": r.get("exp_id") or "",
            "family": family(r.get("exp_id") or ""),
            "team": r.get("team"),
            "agent": r.get("agent"),
            "metric": float(metric) if isinstance(metric, (int, float)) else None,
            "delta": r.get("delta"),
            "outcome": outcome,
            "diff_applied": r.get("diff_applied"),
            "start": start,
            "end": end,
            "seconds": float(secs) if secs else None,
            "desc": r.get("change_summary") or r.get("description") or r.get("note") or "",
        })
    rows.sort(key=lambda x: (x["end"] or datetime.max.replace(tzinfo=timezone.utc)))
    return rows


def direction(rows):
    """minimize or maximize, inferred from the deltas the run itself kept."""
    keeps = [r["delta"] for r in rows if r["outcome"] == "KEEP" and isinstance(r["delta"], (int, float)) and r["delta"]]
    if keeps:
        return "minimize" if sum(1 for d in keeps if d < 0) >= len(keeps) / 2 else "maximize"
    return "minimize"


def audit(name, rows):
    if not rows:
        return None
    d = direction(rows)
    better = (lambda a, b: a < b) if d == "minimize" else (lambda a, b: a > b)

    scored = [r for r in rows if r["metric"] is not None]
    total_secs = sum(r["seconds"] or 0 for r in rows)

    # --- best-so-far curve and the point after which nothing improved ------
    best = None
    best_idx = -1
    for i, r in enumerate(rows):
        if r["metric"] is None:
            continue
        if best is None or better(r["metric"], best):
            best, best_idx = r["metric"], i
    after_best = rows[best_idx + 1:] if best_idx >= 0 else []

    # --- family histories --------------------------------------------------
    def exhausted_under(key):
        """3rd+ consecutive DISCARD within a group since its last KEEP."""
        out, streak = [], defaultdict(int)
        for r in rows:
            g = key(r)
            if g is None:
                continue
            if streak[g] >= 3 and r["outcome"] in ("DISCARD", "KEEP"):
                out.append(r)
            if r["outcome"] == "KEEP":
                streak[g] = 0
            elif r["outcome"] == "DISCARD":
                streak[g] += 1
        return out

    dead_family, post_keep, exhausted = [], [], []
    seen = defaultdict(lambda: {"keeps": 0, "discards": 0, "streak": 0})
    for r in rows:
        f = r["family"]
        h = seen[f]
        if h["discards"] >= 3 and h["keeps"] == 0 and r["outcome"] in ("DISCARD", "KEEP"):
            dead_family.append(r)
        if h["keeps"] >= 1 and r["outcome"] == "DISCARD":
            post_keep.append(r)
        # Third or later consecutive no from the same family. The streak
        # resets on a KEEP, so a family that is still producing gains is
        # never counted, however long the team stays on it.
        if h["streak"] >= 3 and r["outcome"] in ("DISCARD", "KEEP"):
            exhausted.append(r)
        if r["outcome"] == "KEEP":
            h["keeps"] += 1
            h["streak"] = 0
        elif r["outcome"] == "DISCARD":
            h["discards"] += 1
            h["streak"] += 1

    # --- concurrent same-family experiments --------------------------------
    timed = [r for r in rows if r["start"] and r["end"]]
    redundant = set()
    for i, a in enumerate(timed):
        for b in timed[i + 1:]:
            if b["start"] >= a["end"]:
                continue          # sorted by end: no overlap from here on out
            if a["family"] == b["family"] and a["exp_id"] != b["exp_id"]:
                redundant.add(a["exp_id"])
                redundant.add(b["exp_id"])

    exhausted = exhausted_under(lambda r: r["family"])
    teamed = [r for r in rows if r["team"]]
    exhausted_team = exhausted_under(lambda r: r["team"]) if teamed else None

    failed = [r for r in rows if r["outcome"] == "FAILED" or r["diff_applied"] is False]
    dup = [k for k, n in defaultdict(int, {
        e: sum(1 for r in rows if r["exp_id"] == e) for e in {r["exp_id"] for r in rows}
    }).items() if n > 1]

    def pct(x):
        return round(100.0 * x / len(rows), 1) if rows else 0.0

    return {
        "run": name,
        "experiments": len(rows),
        "direction": d,
        "gpu_hours": round(total_secs / 3600.0, 2),
        "keeps": sum(1 for r in rows if r["outcome"] == "KEEP"),
        "discards": sum(1 for r in rows if r["outcome"] == "DISCARD"),
        "failed": len(failed),
        "failed_pct": pct(len(failed)),
        "best_metric": best,
        "best_reached_at": best_idx + 1,
        "after_best": len(after_best),
        "after_best_pct": pct(len(after_best)),
        "dead_family": len(dead_family),
        "dead_family_pct": pct(len(dead_family)),
        "post_keep_discards": len(post_keep),
        "post_keep_pct": pct(len(post_keep)),
        "exhausted": len(exhausted),
        "exhausted_pct": pct(len(exhausted)),
        "teams": len({r["team"] for r in teamed}) if teamed else 0,
        "exhausted_team": len(exhausted_team) if exhausted_team is not None else None,
        "exhausted_team_pct": (round(100.0 * len(exhausted_team) / len(teamed), 1)
                               if exhausted_team is not None and teamed else None),
        "redundant_concurrent": len(redundant),
        "redundant_pct": pct(len(redundant)),
        "duplicate_exp_ids": len(dup),
        "families": len({r["family"] for r in rows}),
        "has_descriptions": sum(1 for r in rows if r["desc"]),
        "_dead_family_ids": [r["exp_id"] for r in dead_family][:20],
        "_post_keep_ids": [r["exp_id"] for r in post_keep][:20],
        "_exhausted_ids": [r["exp_id"] for r in exhausted][:20],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="directory holding the run directories")
    ap.add_argument("--runs", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--min-experiments", type=int, default=10)
    a = ap.parse_args()

    if a.all or not a.runs:
        paths = sorted(a.root.glob("*/logs/experiments.jsonl"))
    else:
        paths = [a.root / r / "logs" / "experiments.jsonl" for r in a.runs]

    out = []
    for p in paths:
        if not p.exists():
            print(f"skip (missing): {p}", file=sys.stderr)
            continue
        rep = audit(p.parent.parent.name, load(p))
        if rep and rep["experiments"] >= a.min_experiments:
            out.append(rep)

    if not out:
        print("no runs met the minimum experiment count")
        return

    hdr = (f"{'run':<18}{'exps':>5}{'gpuh':>7}{'keep':>5}{'fail%':>7}"
           f"{'fams':>5}{'exh/fam':>9}{'exh/team':>9}{'redundant':>10}{'after-best':>11}")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(out, key=lambda x: -x["experiments"]):
        print(f"{r['run']:<18}{r['experiments']:>5}{r['gpu_hours']:>7.1f}{r['keeps']:>5}"
              f"{r['failed_pct']:>6.0f}%{r['families']:>5}{r['exhausted_pct']:>8.0f}%"
              f"{('%d%%' % r['exhausted_team_pct']) if r['exhausted_team_pct'] is not None else '-':>9}"
              f"{r['redundant_pct']:>9.0f}%{r['after_best_pct']:>10.0f}%")

    tot = sum(r["experiments"] for r in out)
    gpuh = sum(r["gpu_hours"] for r in out)
    print("-" * len(hdr))
    print(f"{'TOTAL':<18}{tot:>5}{gpuh:>7.1f}"
          f"{sum(r['keeps'] for r in out):>5}"
          f"{100*sum(r['failed'] for r in out)/tot:>6.0f}%"
          f"{sum(r['families'] for r in out):>5}"
          f"{100*sum(r['exhausted'] for r in out)/tot:>8.0f}%"
          f"{('%d%%' % (100*sum(r['exhausted_team'] or 0 for r in out)/max(1,sum(r['experiments'] for r in out if r['exhausted_team'] is not None)))):>9}"
          f"{100*sum(r['redundant_concurrent'] for r in out)/tot:>9.0f}%"
          f"{100*sum(r['after_best'] for r in out)/tot:>10.0f}%")

    if a.json:
        a.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"\nwrote {a.json}")


if __name__ == "__main__":
    main()
