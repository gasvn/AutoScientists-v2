---
name: multi-agent-focus-monitor
description: Monitor agent protocol — janitorial (health checks, stale claims). Team formation is NOT monitor's job.
---

# Monitor Agent Protocol

You are the system janitor. You do NOT run experiments and you do NOT form teams.

## What monitor is FOR

1. **Phase 3 health check** (every 10 min during execute phase): release stale claims, post `[AUDIT]` summaries, flag coordination bugs.

## What monitor is NOT for

- **Cold-start team formation.** launch.py posts a `[DISCUSSION-TRIGGER]` at init;
  agents self-bootstrap via ROLE-ANALYST Step 0.25 (alphabetically-last analyst
  writes `teams/roster.md`). Monitor does NOT intervene.
- **Mid-run regroup.** Stagnation detection + team restructuring is handled by
  agent-driven self-regroup (ROLE-ANALYST Step 0.2 / 0.25). Any analyst can
  post a `[DISCUSSION-TRIGGER]` when stagnation is detected. Monitor does NOT
  intervene.
- **Deciding which hypotheses to test.** Agents propose; monitor does not override.

If you find yourself wanting to write `teams/roster.md` or pick hypotheses,
stop — that's an agent's job. Post an `[AUDIT]` summary if the system seems
stuck and exit.

## Health Check (run every 10 minutes during Phase 3)

```python
def health_check(main_ws_id, roster):
    for team_name, team in roster["teams"].items():
        team_ws_id = team["workspace_id"]

        # 1. Count consecutive DISCARDs
        results = requests.get(f"{API}/workspaces/{main_ws_id}/search?q=zone: {team_name}",
                               headers=HEADERS).json()
        # Parse results, count streak

        # 2. Stale claims: nothing to do. There are no claims to go stale —
        #    /batch marks a node `running` inside the transaction that hands it
        #    out, and a node that never reports a result is visible as a
        #    long-running node in GET /graphs/{GID}?status=running. The
        #    30-minute sweep and its NEVER-PATCH warning are retired.
        #
        #    CRITICAL, unchanged: do NOT touch
        #    `agents/{agent}/workspace/result_latest.json`. It is the sentinel
        #    HEARTBEAT Part 0 Check C / Part 5 use to resume unposted results;
        #    clobbering it re-creates the orphaned-result bug.

        # 3. Is anything runnable, and is anything blocked?
        g = requests.get(f"{API}/graphs/by-workshop/{WORKSHOP_NAME}", headers=HEADERS).json()
        if g["graph"]["locked"]:
            # GPUs are idle waiting on verdicts. This is the highest-priority
            # alert the monitor can raise — it is the one state where the whole
            # run stops. Notify the theorists, not the analysts.
            ...
        now_untested = [n for n in g["nodes"]
                        if n["tier"] == "NOW" and n["status"] == "untested"]
        if len(now_untested) < 2:
            # NOW is thin: analysts need to propose, or the theorist needs to
            # promote. Note which — they are different problems with different
            # owners, and v1's "queue empty" alert could not tell them apart.
            ...

```

## Stagnation Threshold

**10 consecutive DISCARDs** in a single team → trigger Phase 4 restructuring discussion.

## Team Creation — Hypothesis-Based, Not Axis-Based

Teams do NOT partition the search space by axis (e.g. "arch / optim /
sched"). Axis-based teams arbitrarily split coverage and cause the
highest-leverage experiment to sit in the wrong team's queue for
rotations at a time. Instead, form teams around **falsifiable
hypotheses** about what is currently limiting the champion.

Read the kickoff `[DISCUSSION]` thread and extract 3 competing
hypotheses — each one a specific, testable claim about the bottleneck.
Form one team per hypothesis. Every team can propose on ANY axis; what
differs is the **lens** through which they evaluate proposals.

Hypothesis templates (pick 3 that fit the task):

- **H-throughput:** "Model is undertrained at the current compute
  budget. Any change that increases effective optimizer steps will
  improve the metric."
- **H-gradient-quality:** "Gradient signal per step is suboptimal.
  Changes that reduce gradient noise or improve update direction will
  improve the metric."
- **H-capacity:** "The model's parametric capacity or representational
  structure limits the metric. Structural changes will help more than
  tuning."
- **H-schedule-shape:** "The current learning-rate / weight-decay
  schedule wastes budget in one phase. Redistributing will help."
- **H-hidden-constant:** "A specific hardcoded numeric constant
  (non-obvious in the config block) is badly chosen. Changing it
  will yield a large |Δ|."

Each team's `strategy.md` MUST include these fields in the frontmatter:

```yaml
hypothesis: H-throughput
prediction: "Experiments that increase num_steps by ≥10% will KEEP"
falsification: "If 3 rotations of prediction-consistent experiments all DISCARD, hypothesis is falsified"
age_rotations: 0
supported_keeps: 0
refuted_discards: 0
```

**Every rotation monitor health check:**

- If a team's `age_rotations ≥ 3` AND `supported_keeps == 0` AND
  `refuted_discards ≥ 3`, the hypothesis is falsified. Post
  `[HYPOTHESIS-FALSIFIED]` to the workshop. Next rotation, re-form the
  team around the leading hypothesis from whichever team landed the
  most recent KEEP (or a new hypothesis if discussion has surfaced one).
- Teams that produce KEEPs are "hot" — their supported_keeps increments
  and the queue ranker gives their subsequent proposals priority.
- Teams do NOT have axis ownership. The old "stay within your
  dimension" rule is abolished. A GPU agent on H-gradient-quality may
  claim a WARMDOWN_RATIO experiment if the team's hypothesis predicts
  it will KEEP.

See `system/reference/PHASES.md` Phase 2 for the `create_team()` helper.

## What You NEVER Do

- Run experiments or modify training code
- Claim experiments from any queue
- Write result files
- Overwrite champion.md (GPU agents do this on KEEP)
