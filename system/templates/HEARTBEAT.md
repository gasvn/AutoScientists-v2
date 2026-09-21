---
name: multi-agent-focus-heartbeat
description: Template for per-agent HEARTBEAT.md. launch.py injects role + team sections to produce a complete self-contained file per agent.
---

# Agent Heartbeat

**This file is YOUR complete guide. Read it top to bottom on every invocation.**

The heartbeat has 5 parts. Part 0 (Mode Selector) is mandatory and routes you to the correct branch. **Do NOT skip Part 0. Do NOT execute Parts 1–5 until Part 0 has explicitly told you which branch to follow.**

```
Part 0  Mode Selector ......... pick your branch (5 min, mandatory)
Part 1  Boot ................... credentials, paths, identity
Part 2  Branch — Discussion .... CPU-only thinking, post [DISCUSSION], exit
Part 3  Branch — No-Team ....... exit cleanly, no work
Part 4  Branch — Normal Cycle .. orient, role-specific work, record, post
Part 5  Branch — Resume & Post . finish an unposted result from a prior session
Part 6  Always-Last ............ update AGENT.md, mirror to API, exit with promise
```

---

## Part 0: Mode Selector — DO THIS FIRST

Before ANY other work, you must determine which branch to execute. Follow these three checks in order. Stop at the first branch that matches.

### Check A: Did the launch prompt set MODE?

The orchestrator may include `MODE=discussion` or `MODE=execute` in your launch prompt. Read your launch prompt carefully now.

- **`MODE=discussion`** → go to **Part 2 (Discussion Branch)**. CPU-only. No experiments. Even if you are a GPU agent, you do thinking work this cycle.
- **`MODE=execute`** (or no MODE set) → continue to Check A2.

### Check A2: Workshop-triggered discussion — agents self-regroup

Agents can trigger a system-wide discussion round without orchestrator
intervention. Before executing a normal cycle, search the workshop for
an unresolved `[DISCUSSION-TRIGGER]` post:

```python
recent = requests.get(f"{API}/posts?workshop={WORKSHOP}&limit=30",
                      headers=HEADERS).json().get("data", [])
trigger_posts = [p for p in recent if "[DISCUSSION-TRIGGER]" in p.get("title", "")]

# A trigger is "active" if:
#   - it was posted within the last 3 rotations, AND
#   - fewer than `len(non_monitor)//2 + 1` DISTINCT agents hold
#     [DISCUSS-DONE] as their latest position (see tally_votes in
#     Part 2 2b2 — count agents, not comments, or an agent who
#     voted twice ends the round on its own)
if trigger_posts:
    active_trigger = trigger_posts[0]  # most recent
    done_count = count_comments_matching(active_trigger["id"], "[DISCUSS-DONE]")
    if done_count < 5:
        # Switch THIS agent into discussion mode
        print(f"[DISCUSSION-TRIGGER active] switching to Part 2")
        MODE = "discussion"
        # fall through to Part 2
```

If an active trigger exists → go to **Part 2 (Discussion Branch)**.
Otherwise → continue to Check B.

### Check B: Do teams exist in the roster?

```python
import json, requests, yaml
from pathlib import Path

AGENT_DIR = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}")
creds = json.load(open(AGENT_DIR / "credentials.json"))
HEADERS = {"Authorization": f"Bearer {creds['api_key']}", "Content-Type": "application/json",
           "X-Agent-Name": creds.get("agent_name", AGENT_NAME)}
# Endpoint precedence: the run's own record first, then the environment, then
# the default. The run file is authoritative because the default is wrong the
# moment more than one instance is running on this host — and being wrong there
# means writing into another run's workshop with a token it happens to accept.
_api_file = Path(f"{FOCUS_ROOT}/CLAWINSTITUTE_API")
API = (_api_file.read_text().strip() if _api_file.exists()
       else os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1"))
MAIN_WS_ID = open(f"{FOCUS_ROOT}/WORKSPACE_ID").read().strip()
WORKSHOP = open(f"{FOCUS_ROOT}/WORKSHOP_NAME").read().strip()

def parse_frontmatter(resp):
    content = resp.get("content", "")
    parts = content.split("---")
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}

roster_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster = parse_frontmatter(roster_raw).get("teams", {}) or {}

MY_TEAM = TEAM_WS_ID = None
ALL_TEAM_WS_IDS = {}
for name, t in roster.items():
    ALL_TEAM_WS_IDS[name] = t["workspace_id"]
    if AGENT_NAME in t.get("members", []):
        MY_TEAM = name
        TEAM_WS_ID = t["workspace_id"]

# Role, needed here because the team gate below does not apply to every role.
# AGENT.md may not exist yet on a very first boot — default to "unknown", which
# falls through to the normal team gate rather than silently bypassing it.
import re
_agent_md = (AGENT_DIR / "AGENT.md").read_text() if (AGENT_DIR / "AGENT.md").exists() else ""
_m = re.search(r"^role:\s*(\S+)", _agent_md, re.MULTILINE)
MY_ROLE = _m.group(1).strip() if _m else "unknown"

# The hypothesis graph is global — one per workshop, shared by every team.
# Requires a ClawInstitute server built with the /graphs routes (0.2.0+). If it
# is missing, say so and exit rather than silently falling back to freelancing:
# every role below depends on the graph for what to work on.
_g = requests.get(f"{API}/graphs/by-workshop/{WORKSHOP}", headers=HEADERS)
if _g.status_code == 404:
    raise RuntimeError(
        f"No hypothesis graph for workshop {WORKSHOP}. Either launch.py failed to "
        f"create it, or this ClawInstitute server predates /graphs (needs 0.2.0+).")
GRAPH = _g.json()
GID = GRAPH["graph"]["id"]
```

- **`roster` is empty (no teams formed yet)** → go to **Part 2 (Discussion Branch)**. An empty roster means the system is in cold-start bootstrap: every agent should contribute dimension proposals / hypothesis candidates so the team roster can be committed. Do NOT exit idle — that wastes an agent-slot. The alphabetically-last analyst who runs during bootstrap writes the roster per Step 0.25 of ROLE-ANALYST.
- **`MY_ROLE` is `theorist` or `monitor`** → the team gate does not apply to you; continue to Check C. These roles are global by design. A theorist works on the graph, which belongs to the workshop rather than to any team — and it is the role that clears verdict tickets, so routing it to a no-team exit would idle every GPU in the run until the orchestrator noticed.
- **`roster` has teams but `MY_TEAM is None` (you are not on any team)** → go to **Part 3 (No-Team Branch)**. Exit cleanly. (This case means teams exist but you were left out of the roster — a coordination bug; report it and exit rather than freelancing.)
- **`MY_TEAM` is set** → continue to Check C.

### Check C: Pending result from a prior session? (GPU agents only)

If a prior invocation backgrounded training and exited before posting `[RESULT]`,
finish that first. The sentinel is `agents/{AGENT_NAME}/workspace/result_latest.json`.
Only GPU agents create this sentinel, so skip this check for other roles.

```python
import json, os, re
from pathlib import Path

# MY_ROLE was already derived in Check B (the team gate needs it). Check C is
# GPU-only, so it just reuses that value.

if MY_ROLE != "gpu":
    pending_result = None  # non-GPU roles never create result_latest.json — skip to Check D
else:
    pending_path = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/result_latest.json")
    pending_result = json.loads(pending_path.read_text()) if pending_path.exists() else None

def _alive(pid):
    try: os.kill(int(pid), 0); return True
    except Exception: return False

if pending_result and not pending_result.get("posted_to_workshop"):
    status = pending_result.get("status", "complete")
    # Promote running→complete if PID died AND any training artifact landed.
    # Covers two failure modes:
    #   (a) Kaggle-style submission: submission_path file exists.
    #   (b) Autoresearch / training-only: stdout_path file exists and is
    #       non-empty (training subprocess wrote logs before the agent died).
    # Both mean training itself ran; only the post-train API trail was lost
    # (rate limit, OOM, ungraceful kill). Treat as complete so Part 5 can
    # salvage val_score from the on-disk log.
    pid_dead = not _alive(pending_result.get("pid"))
    sub_path = pending_result.get("submission_path")
    out_path = pending_result.get("stdout_path")
    train_artifact_exists = (
        (sub_path and Path(sub_path).exists()) or
        (out_path and Path(out_path).exists() and Path(out_path).stat().st_size > 0)
    )
    if status == "running" and pid_dead and train_artifact_exists:
        status = "complete"; pending_result["status"] = status
        pending_result["salvaged_from"] = "Check C promote: pid dead, artifact present"
        pending_path.write_text(json.dumps(pending_result, indent=2))

    if status == "running" and _alive(pending_result.get("pid")):
        branch_taken = "resume-waiting"   # GPU busy — log and exit via Part 6e, no new work
    elif status == "complete":
        branch_taken = "resume-and-post"  # go to Part 5 after minimal Part 1 boot
    # else status="posted" → fall through to Check D
```

Routing: missing / `posted` → Check D. `running`+alive → resume-waiting (straight to Part 6e). `complete` (or dead PID + any train artifact) → **Part 5**.

**Salvage path for orchestrator-driven recovery.** When an agent dies after
training but before posting (rate limit, OOM kill, ungraceful exit), simply
relaunching it triggers the Check C promotion above and Part 5 reads
`val_score` from the sentinel (or re-parses it from `stdout_path` if missing).
If for some reason the sentinel itself is corrupt and the agent can't
self-recover, the orchestrator may post the [RESULT] directly using the
agent's token (read `stdout_path` for the metric, write a [RESULT] post
tagged `salvaged:true`, report the result to the graph, mark sentinel posted). The
gpt-nano-agents 2026-05-26 run exercised this exact path for `throughput_v11`
when gpu5 hit a Claude rate limit mid-cycle.

### Check D: Normal cycle

You have a team, no pending result, and the launch prompt did not request discussion mode → go to **Part 4 (Normal Cycle Branch)**.

### Mode Selector summary table

| Launch MODE | Roster | MY_TEAM | Pending result? | Branch | What you do |
|---|---|---|---|---|---|
| any | any | any | (GPU only) unposted, training still alive | resume-waiting (Part 6 only) | Log, exit, don't claim new work |
| any | any | any | (GPU only) unposted, training finished | Part 5 | Post [RESULT], update champion, mark posted |
| `discussion` | any | any | none | Part 2 | CPU-only thinking, read + respond + propose |
| `execute` or unset | empty | — | none | Part 2 | Cold-start bootstrap: contribute to dimension discussion so a roster can be committed |
| `execute` or unset | non-empty | None | none | Part 3 | Exit cleanly (you are not on any team — coordination bug) |
| `execute` or unset | non-empty | set | none | Part 4 | Normal cycle: orient, role work, record |
| `execute` or unset | any | None, role is `theorist`/`monitor` | none | Part 4 | Normal cycle — these roles are global and never on a team |

**Rule of last resort:** If you are uncertain which branch applies, exit cleanly. It is always safer to do nothing than to freelance.

---

## Part 1: Boot

These imports and IDs are needed by every branch. You already loaded credentials and the roster in Part 0; this section just consolidates everything else.

```python
import os, shutil
from datetime import datetime, timezone

# Identity
session_count_marker = AGENT_DIR / "memory" / ".session_count"
session_count = int(session_count_marker.read_text().strip()) if session_count_marker.exists() else 0
NOW = datetime.now(timezone.utc).isoformat()

# Read AGENT.md (your identity, role, focus, notes from last session)
agent_md = (AGENT_DIR / "AGENT.md").read_text()

# Read MEMORY.md index — pick what's relevant, don't read every memory file
memory_dir = AGENT_DIR / "memory"
if (memory_dir / "MEMORY.md").exists():
    memory_index = (memory_dir / "MEMORY.md").read_text()

# Read task spec — REQUIRED. Many tasks have constraints (fold splits, evaluation
# protocols) that invalidate work if missed.
task_spec = open(f"{FOCUS_ROOT}/task/TASK.md").read()

# IMPORTANT: HEARTBEAT.md is authoritative over your own memory files.
# This file may have been updated since your last session with new rules.
# If any memory file contains a procedural rule ("always X", "never Y",
# "the way to do Z") that contradicts the current HEARTBEAT.md, the
# HEARTBEAT wins: delete or rewrite that memory immediately before
# proceeding. This applies ONLY to memories about HOW to work — factual
# findings (experimental results, discovered load-bearing code, confirmed
# relationships, task-domain facts) remain valid regardless of rule
# changes and should be kept.

# Workspace IDs
MAIN_WS_ID = open(f"{FOCUS_ROOT}/WORKSPACE_ID").read().strip()
WORKSHOP = open(f"{FOCUS_ROOT}/WORKSHOP_NAME").read().strip()
```

### Biomlbench Deadline Awareness — READ THIS IF BIOMLBENCH=true

If your launch prompt contains `BIOMLBENCH=true`, this is a **fixed-deadline benchmark task**.
Read these values from your launch prompt now:

```python
import os

# The orchestrator injects TIME_REMAINING_MINUTES and DEADLINE_BUFFER_MINUTES
# as plain KEY=VALUE lines in your launch prompt (visible in your context above).
# Read them directly from the literal text of your launch prompt now.
# They look like:
#   TIME_REMAINING_MINUTES=420
#   DEADLINE_BUFFER_MINUTES=30
#   CUDA_VISIBLE_DEVICES=""
#
# Extract these values by scanning the lines at the top of your prompt.
# If you cannot find them (e.g. this is an old-style prompt), use safe defaults.

# TIME_REMAINING_MINUTES: minutes left before the wall-clock deadline
# Default 480 (8 h) if somehow missing — agents must not assume infinite time.
TIME_REMAINING_MINUTES = float("<value of TIME_REMAINING_MINUTES from your prompt>")

# DEADLINE_BUFFER_MINUTES: stop new experiments this many minutes before deadline
DEADLINE_BUFFER_MINUTES = float("<value of DEADLINE_BUFFER_MINUTES from your prompt, default 30>")

# IS_CPU_ONLY: True when CUDA_VISIBLE_DEVICES="" was set in the prompt
IS_CPU_ONLY = (os.environ.get("CUDA_VISIBLE_DEVICES", "unset") == "")

print(f"[BIOMLBENCH] Time remaining: {TIME_REMAINING_MINUTES:.0f} min  "
      f"buffer: {DEADLINE_BUFFER_MINUTES:.0f} min  cpu_only: {IS_CPU_ONLY}")
```

**Hard rules for biomlbench agents — these override your normal cycle logic:**

**ISOLATION RULE (read this first):** You MUST NOT write to `task/submission.csv` or
`champion/train.py` directly. Save all outputs to your own agent-local workspace:
`agents/{AGENT_NAME}/workspace/repo/submission_<expid>.csv` and `train_<expid>.py`.
Then write `agents/{AGENT_NAME}/workspace/result_latest.json` with your score and paths.
The orchestrator is the ONLY entity that copies to `task/submission.csv` and `champion/train.py`
when the score strictly improves. Violating this causes agents to overwrite each other's work.

1. **If `TIME_REMAINING_MINUTES < DEADLINE_BUFFER_MINUTES + 20`:**
   - Do NOT claim or start any new experiment that takes more than 10 minutes.
   - If you have a working `train.py` (from your workspace or `champion/`), run it
     immediately, save your submission to `agents/{AGENT_NAME}/workspace/repo/submission_<expid>.csv`,
     write `result_latest.json`, and exit. The orchestrator will promote it.
   - If you have no working `train.py`, write the simplest possible model from `task/TASK.md`,
     run it, save to agent-local paths, write `result_latest.json`, and exit. No second experiment.

2. **If `TIME_REMAINING_MINUTES < DEADLINE_BUFFER_MINUTES`:**
   - STOP. Do not run any training.
   - If `{FOCUS_ROOT}/task/submission.csv` exists (orchestrator already promoted one), exit immediately.
   - If it does not exist, check your own workspace for `submission_*.csv` files; if found,
     write `result_latest.json` pointing to the best one so the orchestrator can promote it.
     Do NOT copy it to `task/submission.csv` yourself.
   - If no submission exists anywhere in your workspace, write one using random/zero scores for
     all test rows, save to agent-local path, write `result_latest.json`, and exit.

3. **`submission.csv` takes priority over val metric.** A run that produces a submission but
   has a low val score is worth more than a run that produces no submission.

3b. **Prioritize fundamentally new approaches over incremental HP tuning.** For biomlbench
    tasks, experiments that change the model family, featurization strategy, or training
    objective are strongly preferred over fine-grained tuning of a model that has already
    been reasonably optimized. Light HP tuning of a new approach is fine; running multiple
    consecutive experiments that only adjust regularization coefficients, search trial
    counts, or seed counts on the same architecture is not recommended — these tend to
    produce deltas inside the CV noise band without improving held-out generalization.
    See ROLE-GPU Step 2a for full guidance.

4. **Every experiment must save a stamped `submission_<expid>.csv` to your agent workspace
   and update `result_latest.json` before exiting** — not just the last one. Never write to
   `task/submission.csv` directly. The orchestrator propagates the best one; your job is to
   ensure `result_latest.json` always points to a valid submission file.

5. **No champion/train.py on cycle 1:** For biomlbench tasks, `champion/train.py` does not
   exist at the start. When you reach Step 2 (Read Champion Config) of ROLE-GPU and
   `champion/train.py` is missing, skip the copy step and instead write `train.py` from scratch
   in your workspace (`{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo/train.py`) using the
   instructions in `task/TASK.md`. This IS your baseline experiment.

6. **GPU step for CPU-only tasks.** If `CUDA_VISIBLE_DEVICES` is empty in your launch prompt
   (`CUDA_VISIBLE_DEVICES=""`), skip `nvidia-smi`. Proceed directly to Step 1.5 (baseline
   coordination). All training runs on CPU. However, `GPU_AVAILABLE=False` does NOT restrict
   your method choice — see ROLE-GPU Step 2a for the full CPU-friendly paradigm menu; do not
   default to RDKit+XGBoost just because it is familiar.

7. **Approach diversity (REQUIRED before any experiment).** Read `GPU_AVAILABLE` from your
   launch prompt. Before claiming any experiment or self-designing one, read the approach
   registry at `{FOCUS_ROOT}/logs/approach_registry.json`. Do NOT run an approach already
   registered by another agent this cycle. Follow the registration protocol in ROLE-GPU Step 2a-i.

8. **Compute-mode declaration (REQUIRED if GPU_AVAILABLE=True).** After registering your
   approach and before any training, write your compute mode to a one-line file:
   `echo 'gpu' > {FOCUS_ROOT}/logs/{AGENT_NAME}.gpu_claim`  (GPU experiment)
   `echo 'cpu' > {FOCUS_ROOT}/logs/{AGENT_NAME}.gpu_claim`  (CPU-only experiment)
   The orchestrator reads this to decide whether to serialize or parallelize the next agent.
   Write it as early as possible — within ~60 s of starting. See ROLE-GPU Step 2a-ii for full
   guidance on which experiments are GPU vs CPU and how to balance the mix across the team.

9. **After every training run, write a local result summary** so the orchestrator can find
   your best score AND so Part 0 Check C can tell whether a prior session's result still
   needs narrating. Always point to the stamped agent-local paths (never `task/` or
   `champion/`):
   ```python
   import json
   from pathlib import Path

   agent_workspace = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo")

   # Save stamped copies (isolation rule — never write to task/ or champion/ directly)
   import shutil
   shutil.copy(agent_workspace / "submission.csv", agent_workspace / f"submission_{exp_id}.csv")
   shutil.copy(agent_workspace / "train.py",       agent_workspace / f"train_{exp_id}.py")

   result_summary = {
       # Score + paths — orchestrator promotes best agent's files.
       "val_score": your_val_metric_value,
       "direction": "maximize",  # or "minimize"
       "exp_id": exp_id, "agent": AGENT_NAME,
       "submission_path": str(agent_workspace / f"submission_{exp_id}.csv"),
       "train_path":      str(agent_workspace / f"train_{exp_id}.py"),
       # Resume fields — read by HEARTBEAT Part 0 Check C. REQUIRED.
       "status": "complete",         # "running" | "complete" | "posted"
       "posted_to_workshop": False,  # flip True after [RESULT] post succeeds
       "result_post_id": None,
       "pid": None, "monitor_id": None,
       "stdout_path": None, "stderr_path": None,
       "item": item if "item" in dir() else None,
       "queue_claimed": True,
       "timestamp": datetime.now(timezone.utc).isoformat(),
   }
   (Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace") / "result_latest.json").write_text(
       json.dumps(result_summary, indent=2, default=str)
   )
   # NEVER copy to task/ or champion/ yourself — orchestrator handles promotion.
   ```

### YAML Frontmatter Parsing

The API does NOT auto-parse YAML. Always parse client-side:

```python
def parse_frontmatter(resp):
    content = resp.get("content", "")
    parts = content.split("---")
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}
```

---

## Part 2: Branch — Discussion Mode (CPU-only)

You reached this branch because `MODE=discussion` was set. **You will
NOT run any experiments this cycle.** Discussion mode is for thinking,
reading, debating, proposing, and building consensus — before or
between experimental rounds.

The orchestrator may run MULTIPLE discussion rounds before launching
experiments. Each round, you read everything posted so far and
contribute something NEW. The conversation evolves naturally across
rounds: early rounds are brainstorming, later rounds become synthesis
and ranking. You do not need a special MODE to shift from brainstorming
to synthesis — just read what's there and do whatever is most valuable.

### 2a. Read everything

```python
# Read task spec
task_spec = open(f"{FOCUS_ROOT}/task/TASK.md").read()

# Read champion code (if baseline exists)
champion_path = Path(f"{FOCUS_ROOT}/champion/train.py")
champion_code = champion_path.read_text() if champion_path.exists() else None

# Read ALL recent workshop posts — not just the first few
recent = requests.get(f"{API}/posts?workshop={WORKSHOP}&limit=50",
                      headers=HEADERS).json().get("data", [])

# For each post, also read its comments
for post in recent:
    body = requests.get(f"{API}/posts/{post['id']}",
                        headers=HEADERS).json().get("content", "")
    comments = requests.get(f"{API}/posts/{post['id']}/comments",
                            headers=HEADERS).json().get("data", [])
```

**Read the champion code thoroughly.** Not just the config section —
read the full training loop, the optimizer setup, the model forward
pass, every numeric constant. The code IS the search space.

### 2b. Contribute to the graph

Round 0 produces a graph, not a pile of posts. Posts are where you argue; the
graph is what the rest of the run reads. A brilliant `[DISCUSSION]` thread that
never became a diagnosis or an idea node has no effect on anything.

**First, and before reading anyone else's posts: write your own diagnoses.**

What do *you* think is limiting the metric? Read the champion code, the task
spec and the baseline training curve, and post a `[DIAGNOSIS]` thread with two
or three candidates and the evidence for each. Then create them as diagnosis
nodes:

```python
requests.post(f"{API}/graphs/{GID}/nodes", headers=HEADERS, json={
    "id": "D3", "kind": "diagnosis",
    "title": "The optimizer is badly conditioned on the 2D parameters",
    "rationale": "grad norm spikes every ~300 steps and the spikes line up "
                 "with the loss plateaus; the 1D params show no such pattern",
})
```

Doing this before reading others is not a formality. Six agents that have read
each other's diagnoses produce one diagnosis with five endorsements, and the
disagreement between independently-formed views is the cheapest signal this
system has about where an experiment would actually settle something. Post
yours, then read.

**Then, after reading what others posted**, pick whichever of these the graph
most needs:

1. **Disagree with a diagnosis.** If someone's stated cause is contradicted by
   the code or by a result, say so with evidence. Comment on their thread and,
   if you are confident, propose the replacement as a node. Disagreement is
   worth more than agreement — an unopposed diagnosis that turns out wrong
   costs the whole team several rotations.

2. **Add ideas with real what-ifs.** Each hangs off one diagnosis and states
   what its success and its failure would mean. Write the `if_works` first: if
   you cannot make it say anything interesting, you have learned something
   about the idea.

   **Account for every quantity your diagnosis names.** Write out the relation
   the diagnosis asserts, find where each named quantity is set in the code,
   and say for each one either what you propose to change it to or why you are
   leaving it alone. The usual failure is not ignorance but asymmetry —
   proposing several ways to move one side of a relation while never mentioning
   the other, because the other looks like a given. A module-level constant is
   not a given; it is the most direct lever you have and the cheapest to test.
   If your rationale states `A = f(B, C)` and you propose three variations on
   `B`, you owe an explicit sentence about `C`.

3. **Relate what already exists.** The highest-value contribution in a graph
   that already has ideas. Ask of each pair: do these collect the same payoff
   (`alternative` — one working makes the other worth less), do they act
   through different mechanisms (`independent` — they can share a batch), or
   are they simply the same thing said twice (`restates`)? Those edges decide
   what gets cut and what runs in parallel.

   **If you are a theorist, this is your round and it has two extra duties** —
   merging duplicate diagnoses and filling NOW. Read `ROLE-THEORIST.md` Step 0
   and do that instead of adding more ideas of your own. Nobody else may fill
   NOW, and until someone does, every GPU in the run idles.

4. **Find a gap.** What constant, mechanism or failure mode has nobody stated
   a diagnosis for? Post `[GAPS]` and add the diagnosis. The most valuable
   experiments are often the ones nobody thought to propose.

5. **Trace the training loop.** How many steps fit in the budget? What fraction
   at peak LR? What controls step count? Post `[DYNAMICS]`. This usually
   produces a diagnosis nobody had.

6. **Enumerate every number in the code** — not just named top-level constants
   but inline values inside calls, computed divisors, magic numbers in class
   methods, ratio constants coupling two values. Any number a human could have
   chosen differently is a candidate. Post `[CONSTANTS]`.

7. **Propose both directions.** If a knob has only been proposed in one
   direction, add the other as its own idea and relate the two.

When ranking anything, rank by what it would teach per GPU-hour, not by how
likely it is to win. And note the structural asymmetry on a fixed time budget:
changes that increase effective training steps compound over the whole run,
while per-step quality changes are a one-time constant — so a proposal that
*reduces* throughput needs a strong per-step argument to pay for the steps it
costs.

### 2b2. Discussion self-termination vote — REQUIRED

Before exiting a discussion cycle, decide whether ONE more round is needed or
whether the system should start spending compute. **No orchestrator decides
this. The agents do, by majority vote.** That was true in v1 and it is still
true — the only thing that changed is that you now have facts to vote on
instead of an impression.

```python
# What changed since the last round, and where the graph is thin.
mark = last_round_marker()   # the `latest_seq` recorded on the active trigger
                             # post; 0 on a cold start
facts = requests.get(f"{API}/graphs/{GID}/readiness",
                     headers=HEADERS, params={"since": mark}).json()
```

`readiness` has **no `ready` field, on purpose**. Whether reasoning has gone as
far as it usefully can is a scientific judgement, and it is yours. What the
server will tell you is:

- `since_last_round` — diagnoses, ideas and relations added; whether NOW's
  composition actually changed
- `coverage` — diagnoses nobody has proposed a test for, diagnoses with nothing
  in NOW, ideas related to nothing
- `open_verdicts` — results nobody has ruled on yet

Post exactly ONE of the following as a comment on the active
`[DISCUSSION-TRIGGER]` thread, **citing the facts you used**:

- **`[DISCUSS-MORE] your-reason`** — a real gap remains. Good reasons: a
  diagnosis has no idea under it; ideas are related to nothing, so their
  results would teach us nothing about each other; NOW does not cover the
  distinct diagnoses, so the batch cannot tell them apart; you disagree with
  a stated diagnosis and the disagreement is resolvable by argument rather
  than by experiment.
- **`[DISCUSS-DONE] your-reason`** — another round would not change the plan.
  The strongest evidence is mechanical: this round added no diagnosis, no
  relation, and did not change NOW. When thinking stops moving the plan, only
  data will — and that is the moment to spend GPUs, not a problem to fix.

**Do not vote `[DISCUSS-MORE]` merely because you can imagine more ideas.**
There are always more ideas. The question is whether the next experiment would
be chosen differently after another round of talking. If not, vote DONE.

**Threshold — compute it, do not hardcode it.** The round ends when **more than
half of the non-monitor agents** hold `[DISCUSS-DONE]` as their latest position:

```python
def tally_votes(trigger_post_id):
    """Each agent gets one vote: its LATEST one.

    Count distinct agents, not comments. Agents change their minds — that is
    the point of holding a second round — and an agent who voted MORE last
    rotation and DONE this one has one position, not two. Counting comment
    occurrences leaves the stale MORE in the tally, and worse, counts a repeat
    DONE twice. The dangerous direction is the second one: the round could end
    on fewer than a majority of agents, which is precisely what the vote exists
    to prevent.

    Observed live in the nanoGPT run: comment-counting gave MORE=4 DONE=3 while
    the agents' actual positions were MORE=2 DONE=3.
    """
    comments = requests.get(f"{API}/posts/{trigger_post_id}/comments",
                            headers=HEADERS).json()
    comments = comments.get("data") or comments.get("comments") or []
    comments.sort(key=lambda c: c.get("created_at") or "")

    latest = {}
    for c in comments:
        who = c.get("author_name")           # NOT `author` — that field is the id
        if not who:
            continue
        if "[DISCUSS-DONE]" in c.get("content", ""):
            latest[who] = "DONE"
        elif "[DISCUSS-MORE]" in c.get("content", ""):
            latest[who] = "MORE"

    done = sum(1 for v in latest.values() if v == "DONE")
    return done, latest


import os
non_monitor = [a for a in os.listdir(f"{FOCUS_ROOT}/agents") if "monitor" not in a]
threshold = len(non_monitor) // 2 + 1      # 11 agents -> 6

done, positions = tally_votes(trigger_post_id)
round_over = done >= threshold
```

v1 hardcoded "5 of 9" and that number then silently stopped being a majority
when the roster grew. Count the agent directory.

**Changing your vote is normal and expected.** If you voted `[DISCUSS-MORE]`
last rotation and the gap you named has since been closed, post
`[DISCUSS-DONE]` now and say which gap closed. Holding a stale position because
you already spoke is not caution, it is noise.

### 2b3. When the vote closes, somebody has to commit the roster — REQUIRED

A discussion round that converges and changes nothing is worse than no round:
the agents agreed, and the team structure they agreed to replace is still in
place.

**If `done >= threshold` after your vote, and you are the alphabetically-last
analyst who participated in this round, commit `teams/roster.md` now** — follow
`ROLE-ANALYST.md` Step 0.25 and then post `[TEAM-REFORMED]`.

Do it from here, in Part 2. Step 0.25 says it is required during discussion
rounds, but it lives in the role protocol that only Part 4 reaches, and
discussion rounds never get to Part 4. Nothing else writes the roster during a
discussion round, so without this the round ends and the roster does not move.

Two ways that bites, and the second is worse:

- **Cold start.** An empty roster sends every agent to Part 2 via Check B. If
  the step that fills the roster is itself only reachable from Part 4, the
  system discusses forever. (The monitor normally breaks this at bootstrap —
  runbook Step 4 — but do not rely on it having run.)
- **Mid-run reform.** A team's hypothesis is falsified, an analyst posts
  `[DISCUSSION-TRIGGER]`, everyone converges on a better structure, the vote
  closes — and then execution resumes under the old, already-refuted teams,
  with no error anywhere. The reasoning happened and was silently discarded.

If you are not the last analyst, say in your vote comment who is, so it is
visible that the step is owed and by whom.

### 2c. Engagement rules

- Post at most **1 new thread** per round (avoid flooding)
- Comment on at most **5 existing threads** (substantive, not "I agree")
- Every comment must add NEW information — a critique, a data point,
  a dependency, a counter-proposal. "+1" comments waste everyone's time.
- If you find yourself repeating what another agent already posted,
  STOP — find something nobody said instead.

### 2d. Update AGENT.md and exit

Record what you contributed this round. Note what you think the most
important remaining gap is for the next round. Exit with promise tag.

---

## Part 3: Branch — No-Team Exit

You reached this branch because no team is assigned to you (either teams haven't been formed, or you weren't placed on one).

### 3a. Do nothing

You have no team workspace to write to and no team to tag results with. Anything you produce will be orphan work invisible to the rest of the system.

### 3b. Exit cleanly

```python
print(f"[EXIT] {AGENT_NAME}: no team assignment "
      f"(roster has {len(roster)} teams: {list(roster.keys())}). "
      f"Waiting for monitor to form teams. No work performed.")
import sys; sys.exit(0)
```

**Forbidden in this branch:**
- Running ANY training code
- Editing `champion/train.py` or any file under `champion/`
- POSTing to the workshop (you have no team tag)
- "Just doing useful analysis while we wait" — analysts also exit here. Useful work requires a team context.

---

## Part 4: Branch — Normal Cycle

You reached this branch because you have a team (`MY_TEAM` is set) and `MODE=execute` (or unset). This is the steady-state branch where actual experiment work happens.

### 4a. Orient — discover workspace state

```python
# YOUR team workspace
team_files = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files",
                          headers=HEADERS).json().get("files", [])

# OTHER teams' workspaces (read suggestions/, analysis/, knowledge/ if relevant)
for other_team, ws_id in ALL_TEAM_WS_IDS.items():
    if ws_id != TEAM_WS_ID:
        other_files = requests.get(f"{API}/workspaces/{ws_id}/files",
                                   headers=HEADERS).json().get("files", [])
```

### 4b. Check workshop — respond to RELEVANT posts only (max 3 comments)

```python
recent = requests.get(f"{API}/posts?workshop={WORKSHOP}&limit=20",
                      headers=HEADERS).json().get("data", [])
# Comment on: [SUGGESTION], [NEAR-MISS], [PROPOSAL] from your team, [RESULT] cross-team if relevant
# Cap at 3 comments. Then move on.
```

### 4c. Self-triggered discussion (optional escape hatch)

If `keeps_in_last_10 == 0` (read recent results from main workspace), you may switch to Part 2 (Discussion Mode) for this cycle instead of running an experiment. This is the only legitimate way for a normal-cycle invocation to do discussion work.

### 4d. Execute your role

Follow your role-specific protocol below (Part 4-Role) and team coordination protocol (Part 4-Team).

### 4e. Mandatory API trail

Every experiment, proposal, or knowledge artifact you produce in this branch MUST be reflected in the AnonAPI API:
- **GPU agents**: `POST /graphs/{GID}/batch` → write `results/{exp_id}.md` to main workspace → `POST .../result` → POST `[RESULT]` to workshop. If KEEP, also PUT `champion.md`.
- **Analysts**: POST `[PROPOSAL]` to workshop → POST the idea node to the graph.
- **Theorists**: clear open verdict tickets → relate new ideas → set NOW → POST `[GRAPH]`.

If you cannot complete the API trail for an artifact, do not produce the artifact. Local-only work (writing only to `agents/{AGENT_NAME}/memory/`, mutating `champion/train.py` without the trail) is FREELANCING and is forbidden.

---

## Part 4-Role: Your Role-Specific Protocol

<!-- ROLE_CONTENT_PLACEHOLDER -->
<!-- launch.py replaces this with system/templates/ROLE-{role}.md -->

---

## Part 4-Team: Team Coordination

<!-- TEAM_CONTENT_PLACEHOLDER -->
<!-- launch.py replaces this with system/templates/ROLE-TEAM.md -->

---

## Part 5: Branch — Resume-and-Post (GPU agents only)

Finish a prior session's unposted result. Do NOT claim new work, do NOT touch `train.py`. **If `MY_ROLE != "gpu"`, you should never have been routed here — skip Part 5 entirely and fall through to Part 6.** Only GPU agents write `result_latest.json`; an analyst/monitor reaching this branch indicates a bug upstream, and the only safe action is to exit without doing anything. Inlines the champion-update path from ROLE-GPU.md Step 7.0 (noise gate) + Step 7b (champion.md PUT); both required on KEEP.

```python
import json, yaml
from datetime import datetime, timezone

# 5a. Rehydrate from sentinel (loaded in Part 0 Check C). If val_score is
# missing (agent died before Step 5 wrote it), re-parse from stdout_path so
# we still post a [RESULT] instead of losing the experiment. Worst case the
# parse fails → val_score stays None → Part 5 marks FAILED, the graph claim
# is released, and the proposal stays available for a fresh agent.
exp_id      = pending_result["exp_id"]
our_metric  = pending_result.get("val_score")
direction   = pending_result.get("direction", "maximize")
item        = pending_result.get("item") or {}
description = pending_result.get("description") or item.get("diff") or f"Resumed ({exp_id})"

if our_metric is None and (out := pending_result.get("stdout_path")):
    import re
    try:
        log = Path(out).read_text(errors="ignore")
        # Both "val_bpb: 0.984" and "val_score=0.842" forms are accepted.
        m = re.search(r"(?:val_bpb|val_score|val_metric)[:=\s]+([0-9.eE+-]+)", log)
        if m:
            our_metric = float(m.group(1))
            pending_result["val_score"] = our_metric
            pending_result["salvaged_from"] = (pending_result.get("salvaged_from", "") +
                                                "; val_score re-parsed from stdout")
    except Exception as e:
        print(f"[salvage] stdout re-parse failed: {e}")

# 5b. KEEP/DISCARD/FAILED vs CURRENT champion (may have moved while we were gone).
# If the prior session recorded `diff_applied: false` in the sentinel (Step 4's
# edit didn't land — Edit tool reported old_string not found, patch -p1 rejected
# hunks, etc.), the metric in result_latest.json is just baseline noise: the
# proposal was never actually tested. Mark FAILED so the champion isn't
# promoted to a phantom and analysts can re-queue with a fresh diff.
diff_applied = bool(pending_result.get("diff_applied", item.get("diff_applied", True)))
champ_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json()
champ = parse_frontmatter(champ_raw)
metric_name = champ.get("metric_name", "val_score")
current_best = champ.get(metric_name, float("-inf") if direction == "maximize" else float("inf"))
improved = (direction == "maximize" and our_metric > current_best) or \
           (direction == "minimize" and our_metric < current_best)
if not diff_applied:
    outcome = "FAILED"
else:
    outcome = "KEEP" if improved else "DISCARD"
delta   = (our_metric - current_best) if direction == "maximize" else (current_best - our_metric)

# 5c. Report the result to the graph (same as ROLE-GPU.md Step 6). There is no
# claim to release and no queue row to move — one call records the outcome and
# opens verdict tickets on whatever it affected.
#
# `matched` still has to be answered honestly even on a resume. Re-read the
# node's if_works / if_fails before deciding; a session that died mid-training
# is exactly the case where it is tempting to shrug and write "fails".
try:
    requests.post(f"{API}/graphs/{GID}/nodes/{exp_id}/result", headers=HEADERS, json={
        "outcome": "worked" if outcome == "KEEP" else "failed",
        "matched": matched_branch,
        "metric": our_metric,
        "observation": observation_from_sentinel_or_stdout + " (reported on resume)",
    })
except Exception as e:
    print(f"[RESUME] graph result post failed: {e!r}")

# 5d. If KEEP: run the multi-seed noise gate from ROLE-GPU.md Step 7.0, then PUT
#     champion.md per ROLE-GPU.md Step 7a/7b (with If-Match on champ_raw version for
#     race safety — another agent may have promoted while you were gone). Near-noise
#     delta without second-seed confirmation → demote to DISCARD and skip the PUT.

# 5e. Post [RESULT] — THE whole point of this branch
r = requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP,
    "title": f"[RESULT] {exp_id}: {metric_name}={our_metric} ({outcome})",
    "content": f"## Experiment\n{description}\n\n## Result\n{metric_name}: {our_metric}\n"
               f"Delta: {delta:+.6f}\nOutcome: {outcome}\nResumed-from-prior-session: true\n\n"
               f"## Team\n{MY_TEAM}",
    "tags": [f"team:{MY_TEAM}", "type:result", f"outcome:{outcome}", "resumed:true"]
})

# 5f. Mark posted (prevents duplicate post next cycle — DO NOT SKIP)
pending_result.update({"status": "posted", "posted_to_workshop": True,
                       "result_post_id": r.json().get("id") if r.ok else None,
                       "posted_at": datetime.now(timezone.utc).isoformat()})
pending_path.write_text(json.dumps(pending_result, indent=2, default=str))
```

Then fall through to Part 6 (update AGENT.md with `last_branch="resume-and-post"`, exit with promise). Do NOT enter Part 4.

---

## Part 6: Always-Last — Record and Exit

Run this regardless of which branch you took — Part 2 (discussion), Part 3 (no-team), Part 4 (normal), Part 5 (resume-and-post), or the resume-waiting exit from Part 0 Check C. At minimum do 6a (update AGENT.md with the branch you took) and 6e (exit with promise tag). For resume-waiting, run 6a → 6d → 6e and skip 6b/6c.

### 6a. Update AGENT.md

```python
agent_content = f"""---
name: {AGENT_NAME}
role: {MY_ROLE}
team: {MY_TEAM if MY_TEAM else 'null'}
last_seen: "{NOW}"
session_count: {session_count + 1}
last_branch: "{branch_taken}"  # discussion / no-team / normal / resume-waiting / resume-and-post
last_experiment: "{exp_id if 'exp_id' in dir() else 'none'}"
last_outcome: "{outcome if 'outcome' in dir() else 'none'}"
---

# {AGENT_NAME}

{MY_ROLE.title()} agent. Team: {MY_TEAM or 'unassigned'}.

## Current Focus
{what_you_are_investigating}

## Notes for Next Session
{what_to_try_next}
"""
(AGENT_DIR / "AGENT.md").write_text(agent_content)
(memory_dir / ".session_count").write_text(str(session_count + 1))
```

### 6b. Post [SUGGESTION] if uncertain (optional)

If you noticed something worth flagging but aren't ready to propose an experiment, post a `[SUGGESTION]`:

```python
requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP,
    "title": f"[SUGGESTION] {title}",
    "content": f"## Problem\n{what_you_noticed}\n\n## Idea\n{what_might_help}\n\n## Questions\n{what_you_are_unsure_about}",
    "tags": [f"team:{MY_TEAM}", "type:suggestion"]
})
```

Examples of suggestions worth sharing:
- "Queue has stale items baselined on an old champion — needs cleanup"
- "All single-parameter sweeps exhausted — should try multi-parameter combinations"
- "Target code has dead branches that should be pruned"
- "Team X's finding in {topic} could improve our experiments in {adjacent topic}"

### 6c. Save memories

When you learn something reusable across sessions:
```python
memory_file = memory_dir / "feedback_{topic}.md"
memory_file.write_text("""---
name: {topic}
description: {one_line}
type: feedback
---

{detailed_finding_with_evidence}
""")
# Update MEMORY.md index: - [Title](file.md) — one-line hook
```

### 6d. Mirror AGENT.md to API

```python
requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/agents/{AGENT_NAME}.md",
    headers=HEADERS, json={"content": agent_content})
```

### 6e. Exit with promise tag

```python
print(f"<promise>{AGENT_NAME} cycle complete (branch={branch_taken})</promise>")
```

---

## Quick reference: branch checklist

Before you do ANY work, confirm in your head:

- [ ] I read my launch prompt and noted whether `MODE=discussion` or `MODE=execute` was set.
- [ ] I read `teams/roster.md` and determined `MY_TEAM`.
- [ ] I checked `agents/{AGENT_NAME}/workspace/result_latest.json` for an unposted prior result (Part 0 Check C).
- [ ] I picked exactly ONE branch from the Part 0 table.
- [ ] If resume-waiting: I will NOT claim new work; the GPU is still busy with my own training.
- [ ] If Part 5 (resume-and-post): I will post the prior result and set `posted_to_workshop=true`; I will NOT start a new experiment.
- [ ] If Part 2 (discussion): I will NOT touch any training code.
- [ ] If Part 3 (no-team): I will exit immediately after recording.
- [ ] If Part 4 (normal): every artifact I produce will have a corresponding AnonAPI API call.

If you cannot tick all boxes, exit cleanly via Part 6e.
