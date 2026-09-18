# runbook.md — Orchestrator Runbook (base program)

You are the **orchestrator** for this multi-agent focus area. Your job is to set up and run a system of AI agents that collaboratively work on a benchmark task.

This file is the **base program**: it defines the universal control flow that every task type shares. Task-specific behavior — stop criteria, GPU dispatch policy, champion promotion, discussion rules — lives in `task-profile.md` (selected by `launch.py` based on `task_type` in `task/TASK.md` frontmatter).

## How to use this file

1. **Read `task-profile.md` in this directory before doing anything else.** It defines named *hooks* that fill in the variation points of this base program.
2. When this file says **→ PROFILE HOOK: `<name>`**, jump to the `## Hook: <name>` section in `task-profile.md` and execute it, then return here.
3. Universal rules (below) always apply regardless of profile.

If `task-profile.md` is missing, abort and ask the user — `launch.py` should have copied it.

## Universal rules

**THE ORCHESTRATOR IS A PURE COORDINATOR. IT NEVER RUNS EXPERIMENTS.**
No matter what happens — agents time out, agents fail, queues are empty, the deadline is close — the orchestrator's response is always to launch (or re-launch) an agent, never to run training or write results itself. No `python train.py`, no model fitting, no feature engineering, no writing `submission.csv` by hand. The orchestrator's only file writes are champion-promotion copies (Step 5e) and log appends. See "What You NEVER Do" for the full list.

**NEVER STOP. NEVER ASK PERMISSION. LOOP CONTINUOUSLY.**
Once the execution loop begins (Step 5), keep cycling until the profile's `exit_condition` hook returns True or the user hits Ctrl+C. Do not pause to ask "should I keep going?" after 3, 5, 10, or any number of cycles. The user may be away for hours or days. Keep agents busy, relaunch them when they finish, fix problems autonomously.

## Step 0 — Determine state

```python
from pathlib import Path
THIS_DIR = Path("runbook.md").resolve().parent
```

### Case A: This is the template (no WORKSPACE_ID, no `agents/`)

You are reading the template. Create a new ablation directory:

→ PROFILE HOOK: `launch_command`

`launch.py` creates `../<run-name>/` with its own copy of system/task files, agents, workspace, logs, this `runbook.md`, and the matching `task-profile.md`. The template itself stays clean.

If the user requested changes (e.g. "skip discussion", "only 2 teams"), apply those edits to the **ablation's** files after launch.py creates them — never modify the template.

Then set:
```python
FOCUS_ROOT = THIS_DIR.parent / "<run-name>"
```
and proceed to Step 1.

### Case B: Existing ablation (`WORKSPACE_ID` exists)

```python
FOCUS_ROOT = THIS_DIR
```

Check state by looking at `teams/roster.md` and `logs/`:

| Check | Meaning | Action |
|---|---|---|
| `teams: {}` in roster | Bootstrap done, no teams yet | → Go to Step 3 |
| Teams have members, no experiments | Teams formed | → Go to Step 5 |
| Teams have members, experiments exist | System was running | → Case C |

### Case C: Resuming after interruption

If `logs/sessions.jsonl` or `logs/experiments.jsonl` has entries:
1. Read logs to understand what already happened
2. Release any stale claims (Step 5f)
3. Resume the execution loop (Step 5)

## Step 1 — Bootstrap

```python
import json, os, yaml, requests
from datetime import datetime, timezone
from pathlib import Path

FOCUS_ROOT = Path("<ablation dir>")
WS_ID      = (FOCUS_ROOT / "WORKSPACE_ID").read_text().strip()
WORKSHOP   = (FOCUS_ROOT / "WORKSHOP_NAME").read_text().strip()
tokens     = json.loads((FOCUS_ROOT / "agent_tokens.json").read_text())
TOKEN      = list(tokens.values())[0]
API        = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")
HEADERS    = {"Authorization": f"Bearer {os.environ.get('CLAWINSTITUTE_TOKEN', TOKEN)}",
              "Content-Type": "application/json",
              "X-Agent-Name": "orchestrator"}

def parse_fm(resp_or_text):
    text = resp_or_text.get("content", "") if isinstance(resp_or_text, dict) else resp_or_text
    parts = text.split("---")
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}

# Read task spec and agent prefix
task_md   = (FOCUS_ROOT / "task" / "TASK.md").read_text()
task_meta = parse_fm(task_md)
task_name = task_meta.get("name", "task")
PREFIX    = (FOCUS_ROOT / "AGENT_PREFIX").read_text().strip() if (FOCUS_ROOT / "AGENT_PREFIX").exists() else FOCUS_ROOT.name
```

→ PROFILE HOOK: `bootstrap_extras` (e.g. deadline clock, GPU detection — set any extra variables this profile needs)

## Step 2 — Read key files

Before proceeding, read these to understand the system:

```
system/reference/SKILL.md          — how multi-agent coordination works
system/reference/GRAPH.md          — the hypothesis graph: what agents reason over
system/reference/LOGGING.md        — log formats
system/templates/HEARTBEAT.md      — agent boot template (launch.py uses this)
task/TASK.md                       — the task problem definition
task-profile.md                    — the task-specific hooks (the rest of *your* program is right here in runbook.md)
```

## Step 3 — Round 0: build the graph

→ PROFILE HOOK: `discussion_policy` (defines whether Round 0 runs, when, and any extra prompt content)

Before any compute is spent, the team works out what it thinks is limiting the
metric and what it could do about that. The output is not a document — it is a
graph on the server that every later step reads and writes.

The round has a shape, and the order matters:

1. **Diagnoses, written blind.** Each agent proposes what it believes the
   binding constraint is, from the baseline code, the task spec and the
   training curve — *before reading anyone else's*. Launching them in parallel
   is what makes this blind; do not stagger them. Six agents that have read
   each other's diagnoses are one agent with extra latency.
2. **Merge into 3–6 diagnosis nodes.** Keep a `D_unknown` and never close it.
   A graph that has accounted for everything has stopped being a model of an
   open problem.
3. **Ideas with their what-if.** Each hangs off a diagnosis and states what it
   would mean if it works and if it fails. The server rejects ones that do not.
4. **Relations.** Especially `alternative` (same payoff — one working makes the
   others worth less) and `independent` (different mechanisms — safe to run
   together). Those two decide what can be cut and what can be parallelized.
5. **Set NOW.** Exactly as many ideas as there are GPU agents.

```python
import os
non_admin_agents = [a for a in os.listdir(FOCUS_ROOT / "agents") if "monitor" not in a]

for agent_name in non_admin_agents:
    Agent(
        description=f"{agent_name} round 0",
        prompt=(
            f"You are {agent_name}.\n"
            f"FOCUS_ROOT={FOCUS_ROOT}\n"
            f"MODE=discussion\n"  # REQUIRED — routes the agent to HEARTBEAT Part 2
            f"Read {FOCUS_ROOT}/agents/{agent_name}/HEARTBEAT.md and follow it.\n"
            f"You MUST start at Part 0 (Mode Selector). Do not skip ahead.\n"
            f"Write your own diagnoses BEFORE reading other agents' posts.\n"
            f"{extra_discussion_instructions}"   # from the profile hook
        ),
        run_in_background=True,
        model="sonnet"
    )
```

> **Model choice.** Haiku-class agents have a documented "describe instead of
> do" failure mode in this workflow: they write elaborate local memory files
> claiming the work is done but never call the API, leaving the graph empty.
> Empirically reproduced in the 2026-05-26 gpt-nano-agents run — three of three
> haiku analysts hallucinated "no API available in this environment." Always
> use **sonnet or opus**; reserve haiku for deterministic mechanical work
> outside this loop.

**`MODE=discussion` is mandatory.** Without it the Mode Selector cannot route
agents to the Round 0 branch and they fall through to "no team → exit".

**Expected duration: 15–25 minutes.** Longer than v1's discussion phase, and
that is the trade this system makes: it buys the relations that let a single
result later close several ideas at once. If an agent runs past 40 minutes,
something is wrong — check it before proceeding.

**Do not leave the GPUs idle for the whole round.** As soon as the diagnosis
layer exists (after step 2), the cheapest idea under each distinct diagnosis
can be promoted to NOW and dispatched. The first batch is not trying to improve
the metric; it is trying to find out which diagnosis is real.

**Verify the graph exists before moving on:**

```python
g = requests.get(f"{API}/graphs/by-workshop/{WORKSHOP}", headers=HEADERS).json()
diagnoses = [n for n in g["nodes"] if n["kind"] == "diagnosis"]
ideas     = [n for n in g["nodes"] if n["kind"] == "idea"]
assert len(diagnoses) >= 3, "Round 0 produced no diagnosis layer"
assert len(ideas) >= 2 * len(diagnoses), "diagnoses with nothing to test them"
assert any(n["tier"] == "NOW" for n in ideas), "nothing promoted to NOW"
```

## Step 4 — Form teams around the diagnoses

Launch the monitor agent to read discussion posts and form teams.

```python
Agent(
    description="monitor forms teams",
    prompt=(
        f"You are {PREFIX}_monitor.\n"
        f"FOCUS_ROOT={FOCUS_ROOT}\n"
        f"MODE=execute\n"
        f"Read {FOCUS_ROOT}/agents/{PREFIX}_monitor/HEARTBEAT.md and follow it.\n"
        f"You MUST start at Part 0 (Mode Selector).\n"
        f"{extra_monitor_instructions}"   # from the profile hook
    ),
)
```

Verify teams were formed:

```python
roster_raw = requests.get(f"{API}/workspaces/{WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster = parse_fm(roster_raw)
teams  = roster.get("teams", {})
assert len(teams) >= 2, "Teams not formed properly"
```

Each team owns one diagnosis node. That is the same thing a v1 team owned — a
falsifiable hypothesis — except it now lives in the shared graph, so a result
from another team can refute your diagnosis and you will be handed a ticket
about it.

→ PROFILE HOOK: `seeding_policy` (defines who seeds NOW and how — orchestrator-seeded vs monitor-seeded, which idea under each diagnosis goes first)

## Step 5 — Execution loop

```python
cycle_count = 0
while True:
    cycle_count += 1
    print(f"\n{'='*60}\nCYCLE {cycle_count}\n{'='*60}\n")

    # 5a — Pre-cycle check (may signal early exit)
    if pre_cycle_check():    # ← PROFILE HOOK
        break

    # 5a2 — Clear the verdict worklist FIRST. While it is non-empty the graph
    #       refuses new batches and every GPU is idle, so nothing else in this
    #       cycle can make progress until it is empty. (Step 5a2 below)
    # 5b — Launch analysts in parallel (Step 5b below)
    # 5c — Launch GPU agents (Step 5c below)
    # 5d — Wait + log (Step 5d below)
    # 5e — Champion promotion (Step 5e below)
    # 5f — Health check (Step 5f below)
    # 5g — Stagnation check (Step 5g below)
    # 5h — Periodic hooks (Step 5h below)

    if exit_condition():     # ← PROFILE HOOK
        break
```

### 5a. Pre-cycle check

→ PROFILE HOOK: `pre_cycle_check` (default: no-op returning False; biomlbench uses this for deadline checks and emergency submission)

### 5a2. Clear the verdict worklist — BEFORE anything else

```python
g = requests.get(f"{API}/graphs/by-workshop/{WORKSHOP}", headers=HEADERS).json()
GID = g["graph"]["id"]

if g["graph"]["locked"]:
    # Results have landed that the team has not yet ruled on. Launch the
    # theorists and WAIT. Do not launch GPU agents — /batch will 423 and you
    # will have burned an agent launch to learn what this flag already told you.
    for t in [f"{PREFIX}_theorist1", f"{PREFIX}_theorist2"]:
        Task(subagent_type="general-purpose", model="sonnet",
             description=f"{t} clears verdicts",
             prompt=(f"You are {t}.\nFOCUS_ROOT={FOCUS_ROOT}\nMODE=execute\n"
                     f"Read {FOCUS_ROOT}/agents/{t}/HEARTBEAT.md and follow it.\n"
                     f"Start at Part 0 (Mode Selector).\n"
                     f"The graph is LOCKED. Clearing the open verdict tickets is "
                     f"your only job this cycle; every GPU in the run is idle "
                     f"until you finish.\n"
                     f"When done: <promise>{t} cycle complete</promise>"))
    # Wait for both, then re-read. If it is still locked after a theorist
    # cycle, that is a real failure — read logs/raw/ for what they hit. Do NOT
    # work around it by clearing tickets yourself; the orchestrator does not
    # get to decide what a result means.
```

**This gate is the central mechanism of the system and the orchestrator's job
is to respect it, not to route around it.** The lock exists because v1's worst
failure was structural: a result would arrive that condemned four untested
ideas, and nothing could reach into the queue and stop them before they ran.
The measured cost is in `eval/replay/README.md` — most of the compute in
team-labelled v1 runs went to the third-or-later consecutive failure within a
team since its last success.

If you find yourself wanting to skip the gate because GPUs are idle: idle GPUs
are the point. They are idle for minutes, and the alternative is spending them
on questions that were answered in the last cycle.

### 5b. Launch analysts IN PARALLEL

Analysts run on CPU. **Use `sonnet` (or `opus`), never `haiku`** — see model
note in Step 3. Launch all 3 in a single message and wait.

**Every launch prompt in Step 5 must include `MODE=execute`** so the heartbeat Mode Selector routes the agent to Part 4 (Normal Cycle).

```python
analysts = [f"{PREFIX}_analyst{i}" for i in (1, 2, 3)]

# Send ONE message with all 3 Task calls (parallel)
for analyst_name in analysts:
    Task(
        subagent_type="general-purpose",
        model="sonnet",
        description=f"{analyst_name} cycle",
        prompt=(
            f"You are {analyst_name}.\n"
            f"FOCUS_ROOT={FOCUS_ROOT}\n"
            f"MODE=execute\n"
            f"Read {FOCUS_ROOT}/agents/{analyst_name}/HEARTBEAT.md and follow it.\n"
            f"Start at Part 0 (Mode Selector).\n"
            f"{analyst_prompt_extras}"   # ← PROFILE HOOK
            f"When done: <promise>{analyst_name} cycle complete</promise>"
        ),
    )
# Wait for all 3 to complete.

# Then launch the theorists, who relate the new ideas and set NOW for the next
# batch. Analysts first, theorists second: a theorist that runs before this
# cycle's proposals exist has nothing to situate them against.
for t in [f"{PREFIX}_theorist1", f"{PREFIX}_theorist2"]:
    Task(
        subagent_type="general-purpose",
        model="sonnet",
        description=f"{t} cycle",
        prompt=(
            f"You are {t}.\n"
            f"FOCUS_ROOT={FOCUS_ROOT}\n"
            f"MODE=execute\n"
            f"Read {FOCUS_ROOT}/agents/{t}/HEARTBEAT.md and follow it.\n"
            f"Start at Part 0 (Mode Selector).\n"
            f"When done: <promise>{t} cycle complete</promise>"
        ),
    )
```

→ PROFILE HOOK: `analyst_prompt_extras` (extra env vars, deadline reminders, diversity rules — append to the prompt)

### 5c. Launch GPU agents

→ PROFILE HOOK: `gpu_dispatch` (REQUIRED — defines sequential vs parallel, CUDA assignment, mixed dispatch, etc.)

This is the biggest variation between profiles, so the entire body lives in the profile. Common rules:
- Never launch two GPU agents on the same physical GPU at the same time.
- Always set `MODE=execute` in the prompt.
- Each agent reads its own HEARTBEAT.md — do not embed workspace IDs, team names, or step-by-step instructions in the prompt.

### 5d. Wait and log

When each agent finishes, append a session record:

```python
session = {
    "agent": agent_name,
    "cycle": cycle_count,
    "started_at": started_at,
    "ended_at": datetime.now(timezone.utc).isoformat(),
    "status": "success" if promise_received else "timeout",
    "promise_received": promise_received,
}
with open(FOCUS_ROOT / "logs" / "sessions.jsonl", "a") as f:
    f.write(json.dumps(session) + "\n")
```

### 5e. Champion promotion

→ PROFILE HOOK: `champion_promotion` (REQUIRED — defines what "best" means and what artifacts to copy where)

This is the SINGLE point at which the orchestrator writes to shared canonical paths (`task/submission.csv`, `champion/train.py`, `champion.md`). Agents never write these directly.

### 5f. Health check

There are no stale claims to sweep. `/batch` marks a node `running` inside the
transaction that hands it out, so a claim cannot outlive the agent that took
it. What replaces the sweep is the graph's own structural audit:

```python
audit = requests.get(f"{API}/graphs/{GID}/audit", headers=HEADERS).json()
for f in audit["findings"]:
    print(f["severity"], f["code"], f["message"], f["nodes"])

# Nodes stuck in `running` with no result: the agent died mid-experiment.
# Reset them to untested so they can be handed out again — this is the one
# graph write the orchestrator makes, and it is bookkeeping, not judgement.
g = requests.get(f"{API}/graphs/{GID}", headers=HEADERS, params={"status": "running"}).json()
for n in g["nodes"]:
    age_min = (datetime.now(timezone.utc)
               - datetime.fromisoformat(n["updated_at"].replace("Z", "+00:00"))).total_seconds() / 60
    if age_min > 30:
        requests.patch(f"{API}/graphs/{GID}/nodes/{n['id']}", headers=HEADERS,
                       json={"status": "untested",
                             "reason": f"agent died mid-experiment ({age_min:.0f} min, no result)"})

# NOW thin? Two different problems with two different owners — v1's single
# "queue empty" warning could not tell them apart:
now = [n for n in g["nodes"] if n["tier"] == "NOW" and n["status"] == "untested"]
if len(now) < 2:
    print("NOW is thin — do analysts need to propose, or does the theorist need to promote?")
```

### 5g. Stagnation check

```python
# Two signals now. The graph's own state is the better one: a run is stuck
# when every diagnosis has been exhausted or refuted, which is a statement
# about the team's understanding rather than about its recent luck.
g = requests.get(f"{API}/graphs/{GID}", headers=HEADERS, params={"kind": "diagnosis"}).json()
alive = [n for n in g["nodes"] if n["status"] == "active"]
if not alive:
    # Nothing the team believes is limiting the metric is still standing.
    # This is the real trigger for re-running Round 0 — not a DISCARD streak.
    stagnation_response(cycle_count)   # ← PROFILE HOOK

# Count KEEPs in the last N experiments
log_path = FOCUS_ROOT / "logs" / "experiments.jsonl"
if log_path.exists():
    lines = log_path.read_text().splitlines()
    if len(lines) >= 10:
        last10 = [json.loads(l) for l in lines[-10:] if l.strip()]
        keeps  = [x for x in last10 if x.get("outcome") == "KEEP"]
        if len(keeps) == 0:
            stagnation_response(cycle_count)   # ← PROFILE HOOK
```

→ PROFILE HOOK: `stagnation_response` (default: print a warning; optimization stops the loop; biomlbench posts [STUCK] and continues)

### 5h. Periodic hooks

→ PROFILE HOOK: `periodic_hooks` (e.g. meta-improvement every N cycles, registry resets; default: no-op)

```python
periodic_hooks(cycle_count)
```

### 5i. Loop control

→ PROFILE HOOK: `exit_condition` (default: returns False — never exits voluntarily)

If True, fall through to Step 6. Otherwise continue from Step 5a.

## Step 6 — Final report (on loop exit)

→ PROFILE HOOK: `final_report` (default: print cycle count and champion summary)

## What you NEVER do

- Run training experiments yourself (agents do this — no `python train.py`)
- Modify `train.py`, `submission.csv`, or any code in agent workspaces
- Claim experiments from the graph, or answer a verdict ticket. Deciding what a
  result means for the other ideas is the whole job of the agents; an
  orchestrator that rules on tickets to unblock itself has removed the only
  mechanism that stops wasted compute.
- Write result files
- Overwrite `champion.md` except via the `champion_promotion` hook
- Step in because an agent is slow or failed — release the claim, relaunch
- Stop the loop without the `exit_condition` hook returning True, except on user Ctrl+C

→ PROFILE HOOK: `never_do_extras` (profile-specific additions to this list)
