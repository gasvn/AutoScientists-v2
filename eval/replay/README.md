# Phase 0 — what v1 actually spent its compute on

Before building anything that claims to save compute, it is worth measuring
how much there is to save. `audit_runs.py` reads nothing but
`logs/experiments.jsonl` from completed v1 runs, so it works on history that
already exists, and it asks no model to judge anything — every number is
mechanical.

```bash
python audit_runs.py /path/to/claw --all --json v1_waste_audit.json
```

## Result across 10 runs — 525 experiments, 93.5 GPU-hours

```
run                exps   gpuh keep  fail% fams  exh/fam exh/team redundant after-best
selfopt_v1          111   59.2    1    19%   51       7%        -        0%        84%
ar_p4_s46            80    6.5    4     1%    5      75%      77%       62%        94%
ar_p1_s46            67    4.3    6     0%    4      67%      67%        0%        51%
ar_llm_v1            61    7.9    3     3%   33       3%        -        3%        59%
ar_p2_s46            52    4.5    4     2%   33       0%      57%        0%         4%
gpt-nano-pubrun      51    4.1    7     0%    3      47%        -        0%         8%
shareboard           37    0.0    4     0%    7       0%       3%        0%         8%
ar_p4c_s46           31    2.6    5     0%   10      29%      54%       58%         0%
ar_async_v1          23    2.4    3     9%   10       0%        -       70%        35%
multiround           12    2.1    1     0%   10       0%        -       33%        33%
TOTAL               525   93.5   38     5%  166      28%      58%       17%        49%
```

## What the columns mean, and which ones to believe

**`exh/team` — 58%. This is the headline, and the one to trust.**

An experiment is counted when it is the third or later *consecutive* DISCARD
within its own team since that team last produced a KEEP. The streak resets
on every KEEP, so a team that is still producing gains is never counted, no
matter how long it stays on its hypothesis.

This is not an arbitrary threshold. v1 told teams exactly this bar: abandon
the hypothesis after three refuting results and no supporting KEEP
(`ROLE-ANALYST.md` Step 0.3). So the column measures how often a team kept
spending past the line it had drawn for itself — and in the runs that carried
team labels, that is most of the compute they spent.

The reason is structural rather than a failure of discipline. The rule fires
only when an analyst next runs its cycle and re-reads the workspace. By then
the queue is already full of proposals from the same hypothesis and GPU agents
are already executing them. Nothing in v1 could interrupt work that had
already been queued on a question that had just been answered.

**`redundant` — 17%, but 58–70% in the runs that actually ran wide.**

Experiments that overlapped in wall-clock with another experiment of the same
family. `ar_async_v1` 70%, `ar_p4_s46` 62%, `ar_p4c_s46` 58% — the parallel
runs, which is exactly where it matters, because that is where GPUs are being
spent simultaneously. Six agents each picking independently from their own
queue will collide; the collisions are invisible to every one of them.

**`exh/fam` — 28%. Same measure, weaker grouping. Do not compare across runs.**

Here the group is a mechanism family derived from the `exp_id`, which makes it
hostage to each run's naming convention: `ar_p2_s46` splits into 33 families
and `ar_p4_s46` into 5, entirely because of how their ids happened to be
written. Useful within a run, meaningless between them.

**`after-best` — 49%. Reported for scale, not as waste.**

Experiments that ran after the run's best metric had already been reached.
Search has to continue past its own high-water mark; this bounds the question
rather than answering it.

**`fail%` — 5% overall, 19% in `selfopt_v1`.**

Experiments where the diff never applied — the agent edited nothing, or the
edit was rejected. Pure loss: GPU time spent measuring the baseline again.

## What this says about the graph

The two numbers that the hypothesis graph targets directly are `exh/team` and
`redundant`, and they are the two large ones.

- `POST /graphs/:id/batch` refuses to hand out two experiments that share a
  diagnosis, are alternatives, or would confound each other. That is the
  `redundant` column, removed by construction rather than by vigilance.

- A result opens a verdict ticket on every idea the team's own relations say
  is affected, and the graph is locked for new batches until each is answered
  — including ideas that are already running, which can then be aborted. That
  is the `exh/team` column: the mechanism that was missing is not a better
  threshold, it is the ability to interrupt queued and in-flight work the
  moment the question behind it has been answered.

This does not show that v2 will be faster. It shows what a graph would have to
prevent to be worth its cost, and that the quantity is large enough to be
worth trying to prevent.

## Caveats

- Ten runs on two task families (nanoGPT autoresearch, self-optimization).
  Nothing here has been checked on BioML-Bench or ProteinGym.
- `exh/team` needs the `team` field, present in 5 of the 10 runs.
- Duplicate `exp_id`s appear in several runs (`ar_p4_s46` logs a
  `race condition duplicate baseline` by name) — counted as written.
- Richer per-experiment text (the `[PROPOSAL]` mechanism prose) lives in the
  workspace tables of the ClawInstitute database, not in these logs. The
  backup at `claw/clawinstitute_db_backup_*` is a Postgres 16 data directory;
  opening it needs a PG16 binary (`conda install -c conda-forge postgresql=16`),
  which would let the replay harness reason over what each experiment actually
  proposed rather than over its id.
