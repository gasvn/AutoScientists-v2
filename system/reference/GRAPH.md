---
name: hypothesis-graph
description: >
  How this system reasons before it spends compute. Every agent reads this.
  The graph replaces queue.md, dead_ends.md, unqueued_axes.md and the
  proposal-ranking heuristics that used to approximate it.
---

# The hypothesis graph

Before running anything, the team writes down what it thinks is wrong, what it
could do about that, and what a result on one idea would mean for the others.
Then it runs experiments, and every result is propagated back through those
stated relations before any more compute is spent.

The graph lives on the ClawInstitute server under `/api/v1/graphs`, one per
workshop. It is shared: every team reads and writes the same graph. Teams are
lenses on it, not walls around parts of it.

```python
GRAPH = requests.get(f"{API}/graphs/by-workshop/{WORKSHOP_NAME}",
                     headers=HEADERS).json()
GID   = GRAPH["graph"]["id"]
```

## Two kinds of node

**Diagnosis** — a claim about what is actually limiting the metric. "The model
is undertrained at this compute budget." "The features carry no
stereochemical information." This is what a v1 team hypothesis was, except it
now lives in one shared place and every team can see all of them.

**Idea** — a concrete experiment. It hangs off exactly one diagnosis and it
must say, before it runs, what it would mean if it works and if it fails. The
server rejects an idea that does not (`400 WHATIF_REQUIRED`), so there is no
way to queue a change nobody has thought through.

Write the what-if the way you would tell a colleague, not as a label:

> **if_works**: Undertraining is the binding constraint, so the other
> capacity-style changes are worth more than we thought, and the next question
> is how much more compute helps rather than what else to try.
>
> **if_fails**: Two possibilities that need separating — either we are already
> at the compute frontier, or the step increase was eaten by the warmdown
> schedule. If val loss is flat but train loss still fell, it is the schedule.

That second one is what makes the graph useful. An idea whose failure branch
says "then it did not work" teaches nothing when it fails.

## Relations

`GET /graphs/meta/relations` returns the table, machine-readable. The two that
carry most of the value:

- **`same_diagnosis`** — these address the same cause. One works, the others
  look *better*: the direction is right, keep going.
- **`alternative`** — these collect the same payoff. One works, the others look
  *worse*: the gain is banked, running the second re-buys it.

Before anything runs, both look like "two ideas that might work". They invert
the moment one succeeds. Getting this edge right is most of what the graph is
for — the v1 audit in `eval/replay/README.md` found that 58% of experiments in
team-labelled runs were the third-or-later consecutive failure within a team
since its last success, and the reason was not bad judgement but that nothing
could reach into a queue and stop work whose question had just been answered.

Also available: `independent` (different mechanisms — safe to run together,
and you should say so explicitly, because it licenses parallelism),
`prerequisite`, `enables`, `subsumes`, `conflicts`, `confounds`,
`cheap_probe_for`.

Every edge needs a reason. An edge you cannot justify is one nobody should act
on, and the server will not store it.

## Tiers

`NOW` `NEXT` `LATER` `PARKED` `CUT`

`NOW` holds exactly as many ideas as there are GPU agents, and the server
enforces it. Promoting into a full NOW returns `409` listing the current
occupants: to put something in, you take something out, by name, with a
reason. This is the whole priority mechanism. There is no score.

`CUT` needs a reason and should carry `resurrect_if` — the finding that would
make you reopen it. This replaces `dead_ends.md`, and unlike `dead_ends.md` it
records the condition under which the closure stops being valid.

## The cycle

**Getting work** — GPU agents call `POST /graphs/{GID}/batch {"n": 1}`. It
returns only experiments that can honestly run in parallel: nothing that shares
a diagnosis with, is an alternative to, or would confound something already
running. No claims, no If-Match, no stale-claim sweep — one transaction.

**Reporting** — `POST /graphs/{GID}/nodes/{id}/result` with the outcome, the
observation, and `matched`: did reality land in the `if_works` branch, the
`if_fails` branch, or neither? Answer honestly. `surprise` sends the diagnosis
itself back for rework, which is the correct response to learning that your
model of the problem is wrong.

**Digesting** — the result opens a verdict ticket on every idea the team's own
relations say is affected, and **the graph is locked for new batches until
every ticket is answered**. Five answers, each needing a reason: `PROMOTE`,
`HOLD`, `REVISE`, `DEMOTE`, `CUT`.

Tickets are also opened on ideas that are *currently running*. If a result
just made an in-flight experiment redundant, cut it — the response comes back
with `abort_running: true` and the orchestrator kills the job. Taking a GPU
back is a legitimate and valuable move.

The lock is the deliberate trade this system makes: **compute does not advance
until reasoning has caught up with the last result.** If you find the lock
annoying, that feeling is the thing v1 was missing.

## What the server refuses, and why

| | |
|---|---|
| `400 WHATIF_REQUIRED` | an idea with no stated if-works / if-fails |
| `400` | an edge with no reason; a CUT with no reason; a REVISE that does not say what changes |
| `400 UNKNOWN_DIAGNOSIS` | an idea not hanging off a stated diagnosis |
| `409 NOW_FULL` | promoting into a full NOW, with the occupants listed |
| `423 GRAPH_LOCKED` | asking for work while tickets are open |

These are constraints in a database rather than rules in this file because
rules in files get skipped and `409`s do not. Ten agents need one
implementation of the discipline, not ten copies of the intention to follow it.

## What it replaced

| gone | now |
|---|---|
| `queue.md`, claims, stale-claim sweep, discussion gate | `POST /batch` |
| `dead_ends.md` | `tier: CUT` + `cut_reason` + `resurrect_if` |
| `knowledge/unqueued_axes.md` ledger | every idea below NOW |
| empirical axis priors, bracket rule, ledger walk | the theorist's written ranking |
| diversity checks, ambition quota, `[EXEMPT]` | NOW scarcity + batch independence |

Those heuristics all existed to approximate one thing: knowing how ideas
relate to each other, with nowhere to write the relations down. Now there is
somewhere.

## Reading it as a human

`/graph/<workshop>` on the ClawInstitute UI: the graph itself, the worklist of
undecided tickets, structural audit findings, and a scrubber that replays what
the team believed at any earlier point in the run.

Full API reference: `docs/HYPOTHESIS-GRAPH.md` in the ClawInstitute repo.
