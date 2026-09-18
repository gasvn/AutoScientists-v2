# task-profile.md — autoresearch

This profile fills in the hooks from `runbook.md` for **open-ended optimization** of the [karpathy/autoresearch](https://github.com/karpathy/autoresearch) nanoGPT pre-training loop (`task_type: optimization`) — the goal is to drive `val_bpb` down indefinitely. There is no wall-clock deadline; the loop runs until stagnation or user interrupt.

**Key shape:** 2× H100 GPUs, sequential GPU dispatch, mandatory meta-improvement every 3 cycles, optional dimension discussion.

---

## Hook: launch_command

```bash
cd THIS_DIR
python3 launch.py <run-name> --task task-autoresearch
# (run task-autoresearch/download_repo.sh once before the first launch)
```

`launch.py` walks up from `--task` looking for the nearest `LAUNCH.md` and copies it into the run directory as `task-profile.md`. For autoresearch that resolves to `task-autoresearch/LAUNCH.md` (this file).

---

## Hook: bootstrap_extras

No extras. The base bootstrap (`FOCUS_ROOT`, `WS_ID`, `tokens`, `HEADERS`, `API`, `task_md`, `PREFIX`) is sufficient.

---

## Hook: discussion_policy

**OPTIONAL on cold start.** Skip the standalone discussion phase if the user wants fastest possible first GPU dispatch. Instead, run it *in parallel* with the first GPU agent's training in Step 5.

If you do run it standalone, cap discussion at ONE round of posts (3–8 minutes total) before proceeding to seeding. Do not let it grow to 9+ posts; that consumes the GPU-dispatch budget.

### Cold-start fast path

**Goal: first GPU dispatch within 5 minutes of orchestrator start, NOT 23 minutes.**

| Window | Activity |
|---|---|
| 0–3 min | Read TASK.md, form roster (3 teams of ~3 agents) |
| 3–5 min | Each team posts **1** seed proposal (not 3) — see `seeding_policy` |
| 5–7 min | **First GPU agent dispatched** to the highest-priority seed proposal |
| 7–25 min | Parallel: training runs continue; analysts post more proposals; discussion threads grow |
| 25–30 min | Harvest results, update champion, run meta-improvement note |

Rules:
1. Skip extended discussion before any training.
2. Seed minimum, not maximum — one idea per diagnosis, chosen to tell them apart.
3. Dispatch GPU #1 the moment the first idea is in NOW.
4. 2× H100 GPUs available — use `CUDA_VISIBLE_DEVICES=0` and `=1`.
5. Do NOT block on perfect discussion before training starts. Get GPUs busy first.

If you find yourself ≥10 minutes in with zero GPU agents dispatched, cut whatever step you are on and dispatch a GPU immediately with whatever `POST /graphs/{GID}/batch` returns (or "rerun champion train.py unchanged for sanity" if NOW is empty).

**`extra_discussion_instructions` (empty if discussion is skipped):** no additions beyond the base prompt.

---

## Hook: seeding_policy

**Orchestrator-seeded into NOW.** After teams are formed, the orchestrator posts
ONE `[PROPOSAL]` per diagnosis and adds it to the graph at `tier: NOW`. Dispatch
the first GPU agent as soon as the first idea is in NOW — do not wait for all
diagnoses to be seeded.

The seeds are chosen to *discriminate*, not to win. One idea per distinct
diagnosis, cheapest first: the first batch's job is to find out which stated
cause is real, and a batch that tests three ideas under one diagnosis answers
one question with three GPUs.

```python
g   = requests.get(f"{API}/graphs/by-workshop/{WORKSHOP}", headers=HEADERS).json()
GID = g["graph"]["id"]

for team_name, team_info in teams.items():
    diagnosis = team_info["diagnosis"]          # the node this team owns
    for exp in seed_experiments[team_name]:     # one entry per team on cold start
        requests.post(f"{API}/posts", headers=HEADERS, json={
            "workshop": WORKSHOP,
            "title":   f"[PROPOSAL] {exp['id']}: {exp['description']}",
            "content": f"## Mechanism\n{exp['rationale']}\n\n## Diff\n```python\n{exp['diff']}\n```\n\n## Team\n{team_name}",
            "notify_agents": team_info["members"],
            "tags": [f"team:{team_name}", "type:proposal"],
        })
        requests.post(f"{API}/graphs/{GID}/nodes", headers=HEADERS, json={
            "id": exp["id"], "kind": "idea", "title": exp["description"],
            "diagnosis": diagnosis, "tier": "NOW", "cost_min": 5,
            "rationale": exp["rationale"],
            # Seeds need a real what-if like anything else — the server refuses
            # them otherwise, and a seed whose failure teaches nothing has
            # spent a GPU to tell you that it did not work.
            "if_works": exp["if_works"],
            "if_fails": exp["if_fails"],
            "reason": "cold-start seed, one per diagnosis",
        })
```

Seeding more than one idea per diagnosis will fail with `409 NOW_FULL` once NOW
is full, which is the intended outcome: NOW has exactly as many slots as there
are GPU agents, so a cold start that wants to seed eight ideas is really asking
to run six and queue two. Put the remainder at `NEXT`.

**`extra_monitor_instructions`:** none — monitor forms teams using its default heartbeat behavior.

## Hook: pre_cycle_check

No-op.

```python
def pre_cycle_check():
    return False
```

---

## Hook: analyst_prompt_extras

```python
analyst_prompt_extras = ""   # no additions beyond base
```

---

## Hook: gpu_dispatch

**2× H100 GPUs available. GPU agents run ONE AT A TIME PER GPU.** Up to two GPU agents may run concurrently — one pinned to `CUDA_VISIBLE_DEVICES=0` and one to `CUDA_VISIBLE_DEVICES=1`. NEVER launch two GPU agents on the same device.

For each GPU agent, launch in its own message and wait before launching the next on the same device:

```python
gpu_agents = [f"{PREFIX}_gpu{i}" for i in range(1, 7)]

# Simple sequential model: alternate GPUs, wait between dispatches.
# On cold start, dispatch GPU #1 as soon as the first idea is in NOW (don't wait
# for analysts to finish their cycle).

for i, agent_name in enumerate(gpu_agents):
    cuda = "0" if i % 2 == 0 else "1"
    Task(
        subagent_type="general-purpose",
        description=f"{agent_name} experiment",
        prompt=(
            f"You are {agent_name}.\n"
            f"FOCUS_ROOT={FOCUS_ROOT}\n"
            f"CUDA_VISIBLE_DEVICES={cuda}\n"
            f"MODE=execute\n"
            f"Read {FOCUS_ROOT}/agents/{agent_name}/HEARTBEAT.md and follow it.\n"
            f"Start at Part 0 (Mode Selector).\n"
            f"When done: <promise>{agent_name} cycle complete</promise>"
        ),
    )
    # Wait for this agent to finish (or pair with the other GPU) before the next.
```

That's it. No workspace IDs, no team names, no step-by-step instructions in the prompt. HEARTBEAT.md tells agents how to discover identity, team, workspace, and protocol.

---

## Hook: champion_promotion

**Champion = best `train.py` provenance for the optimized metric (e.g. `val_bpb`).** After each GPU agent finishes, if it reported a KEEP, copy its `train.py` to `champion/`:

```python
import shutil

if outcome == "KEEP":
    agent_train = FOCUS_ROOT / "agents" / agent_name / "workspace" / "repo" / "train.py"
    shutil.copy(agent_train, FOCUS_ROOT / "champion" / "train.py")
    (FOCUS_ROOT / "champion" / "SOURCE").write_text(
        f"{agent_name} {exp_id} {val_bpb} {datetime.now(timezone.utc).isoformat()}\n"
    )
    print(f"Champion propagated: {agent_name} → champion/train.py ({val_bpb})")
```

This is the SINGLE SOURCE OF TRUTH for champion code. All GPU agents read from `{FOCUS_ROOT}/champion/train.py`.

### Auto-bracket big wins

After a KEEP with `abs(delta) > 0.001` (a "big win"), the orchestrator posts bracketing `[PROPOSAL]`s to find the true optimum:

```python
if outcome == "KEEP" and abs(delta) > 0.001:
    # Parse the experiment to identify the changed parameter.
    # Generate 2 bracketing experiments:
    #   1. Midpoint between old and new value
    #   2. 50% overshoot beyond new value
    # Example: batch_size went from 2^18 to 2^17 → propose 2^17.5 and 2^16.5
    for bracket_exp in generate_brackets(old_value, new_value):
        requests.post(f"{API}/posts", headers=HEADERS, json={
            "workshop": WORKSHOP,
            "title":   f"[PROPOSAL] {bracket_exp['id']}: {bracket_exp['description']}",
            "content": f"Auto-bracketing from big win {exp_id} (delta={delta}).",
            "tags":    ["type:proposal", "auto:bracket", f"team:{team_name}"],
        })
        # Also add it to the graph under the relevant diagnosis at tier NOW.
```

The orchestrator generates these — no analyst action needed.

---

## Hook: stagnation_response

**STOP the loop.** Stagnation is a real exit condition for open-ended optimization — when 0 KEEPs occur in the last 10 experiments, the search has converged and the user should intervene.

```python
def stagnation_response(cycle_count):
    print(f"STAGNATION: 0 KEEPs in last 10 experiments (cycle {cycle_count})")
    print("Stopping loop — wait for user input.")
    raise SystemExit(0)
```

(`exit_condition` is also checked at the bottom of the loop, but `stagnation_response` exits directly because stagnation is a permanent state.)

---

## Hook: periodic_hooks

**MANDATORY meta-improvement every 3 cycles.** This is not optional — the system improves itself by editing role templates in response to diagnostic patterns. Skipping this step removes the only mechanism by which the multi-agent system adapts to what it has learned.

```python
def periodic_hooks(cycle_count):
    if cycle_count % 3 == 0:
        meta_improve(cycle_count)

def meta_improve(cycle_count):
    import sys, shutil
    sys.path.insert(0, str(FOCUS_ROOT / "scripts"))
    from meta_diagnostics import analyze_experiments, print_diagnostics

    print(f"\n{'='*60}\nMETA-IMPROVEMENT (Cycle {cycle_count})\n{'='*60}\n")

    # ── Step 1: Harvest cycle_result.json from every GPU agent ──────────────
    # GPU agents write results here but experiments.jsonl may be missing entries.
    experiments_log = FOCUS_ROOT / "logs" / "experiments.jsonl"
    gpu_agents = [f"{PREFIX}_gpu{i}" for i in range(1, 7)]
    harvested = 0
    for agent_name in gpu_agents:
        result_file = FOCUS_ROOT / "agents" / agent_name / "cycle_result.json"
        if result_file.exists():
            try:
                result = json.loads(result_file.read_text())
                result["agent"] = agent_name
                result["cycle"] = cycle_count
                result["harvested_at"] = datetime.now(timezone.utc).isoformat()
                with open(experiments_log, "a") as f:
                    f.write(json.dumps(result) + "\n")
                harvested += 1
            except Exception as e:
                print(f"[WARN] Could not harvest {agent_name}: {e}")
    print(f"Harvested {harvested} cycle results into experiments.jsonl")

    # ── Step 2: Diagnose ────────────────────────────────────────────────────
    diagnostics = analyze_experiments(experiments_file=experiments_log,
                                       last_n=30, num_analysts=3)
    print_diagnostics(diagnostics)

    # ── Step 3: Identify ONE concrete improvement and APPLY IT ──────────────
    # Do NOT just write a report. Read the target file, edit it, save it.
    role_analyst = FOCUS_ROOT / "system" / "templates" / "ROLE-ANALYST.md"
    role_gpu     = FOCUS_ROOT / "system" / "templates" / "ROLE-GPU.md"
    applied      = False
    pattern      = "none"

    if diagnostics.has_high_duplicates:
        pattern = "high_duplicates"
        block = (
            "\n### Step 3b: Cross-Team Deduplication (AUTO-ADDED)\n"
            "Before adding any experiment, search the graph — GET /graphs/{GID}/search\n"
            "and dead_ends.md for semantic overlap. If a similar mechanism exists, skip it.\n"
        )
        content = role_analyst.read_text()
        if "Step 3b" not in content:
            role_analyst.write_text(content + block)
            applied = True
            print("  ✓ Added Step 3b deduplication to ROLE-ANALYST.md")

    elif diagnostics.has_low_activation:
        pattern = "low_activation"
        block = (
            "\n### Step 0.5: Activation Guardrail (AUTO-ADDED)\n"
            "Verify these files exist before starting work. If any are missing, exit.\n"
            "- workspace champion.md\n- the hypothesis graph\n- teams/roster.md\n"
        )
        content = role_analyst.read_text()
        if "Step 0.5" not in content:
            role_analyst.write_text(block + content)
            applied = True
            print("  ✓ Added Step 0.5 guardrail to ROLE-ANALYST.md")

    elif diagnostics.has_slow_propagation:
        pattern = "slow_propagation"
        block = (
            "\n### Step 8b: KEEP Broadcast (AUTO-ADDED)\n"
            "After a KEEP, immediately post [INSPIRATION] to the workshop notifying ALL agents.\n"
            "Include: exp_id, metric delta, mechanism summary, suggested follow-ups.\n"
        )
        content = role_gpu.read_text()
        if "Step 8b" not in content:
            role_gpu.write_text(content + block)
            applied = True
            print("  ✓ Added Step 8b broadcast to ROLE-GPU.md")

    elif diagnostics.has_low_keep_rate:
        pattern = "low_keep_rate"
        block = (
            "\n### Step 2.5: Champion Gap Analysis (AUTO-ADDED)\n"
            "Before proposing an experiment, explicitly state:\n"
            "1. Current champion score\n2. Why this experiment could beat it (mechanism)\n"
            "3. Which weak folds it addresses\n"
            "Do not propose if you cannot answer all three.\n"
        )
        content = role_analyst.read_text()
        if "Step 2.5" not in content:
            role_analyst.write_text(content + block)
            applied = True
            print("  ✓ Added Step 2.5 gap analysis to ROLE-ANALYST.md")

    else:
        print("\nNo critical patterns detected. System operating normally.")

    # ── Step 4: Log outcome ─────────────────────────────────────────────────
    with open(FOCUS_ROOT / "logs" / "meta_results.tsv", "a") as f:
        f.write(
            f"{cycle_count}\t{diagnostics.keep_rate:.3f}\t{diagnostics.efficiency:.1f}\t"
            f"{diagnostics.duplicate_rate:.3f}\t{pattern}\t"
            f"{'applied' if applied else 'skipped'}\t"
            f"{datetime.now(timezone.utc).isoformat()}\n"
        )
    print(f"\nMeta-improvement logged → logs/meta_results.tsv\n{'='*60}\n")
```

**Key rules:**
- Step 1 is mandatory — without harvesting `cycle_result.json`, diagnostics have no data.
- Step 3 must edit a file — do not just print a report.
- One change per meta-improvement cycle; do not bundle multiple edits.

---

## Hook: exit_condition

```python
def exit_condition():
    return False   # never exit voluntarily; only stagnation_response stops the loop
```

---

## Hook: final_report

```python
def final_report():
    print()
    print("=" * 60)
    print("  OPTIMIZATION RUN COMPLETE")
    print("=" * 60)
    print(f"  Task:        {task_name}")
    print(f"  Cycles:      {cycle_count}")
    champ_path = FOCUS_ROOT / "champion" / "train.py"
    src_path   = FOCUS_ROOT / "champion" / "SOURCE"
    if src_path.exists():
        print(f"  Champion:    {src_path.read_text().strip()}")
    print(f"  Code:        {champ_path}")
    print("=" * 60)
```

---

## Hook: never_do_extras

(No additions beyond the universal list.)
