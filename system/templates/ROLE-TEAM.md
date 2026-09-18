---
name: multi-agent-focus-team
description: How agents within a team coordinate using their team workspace
---

# Team Coordination Protocol

Each team has its own workspace. All team members can read/write all files.

## Experiment Flow

```
1.  Analyst searches the graph for the same bet       ← GET /graphs/{id}/search
2.  Analyst posts [PROPOSAL] on workshop              ← public discussion
3.  Analyst adds the idea node with its what-if       ← POST /graphs/{id}/nodes
4.  Theorist relates it to what already exists        ← POST /graphs/{id}/edges
5.  Theorist sets NOW — every promotion costs one     ← PATCH .../nodes/{id}
6.  GPU agent asks for work                           ← POST /graphs/{id}/batch
7.  GPU agent copies champion/train.py to workspace   ← canonical source
8.  GPU agent applies ONE change and trains           ← local GPU
9.  GPU agent re-reads champion (race condition)      ← version check
10. GPU agent writes result to main workspace         ← results/{exp_id}.md
11. GPU agent reports outcome + which branch it hit   ← POST .../nodes/{id}/result
12. GPU agent posts [RESULT] on workshop              ← cross-team visibility
13. Theorist rules on every affected idea             ← POST .../verdicts/{id}
       ↑ the graph is locked for new batches until step 13 is done
```

## File Discovery Protocol

Agents do NOT follow hardcoded lists of files to read. Instead, they discover what exists and decide what is relevant to their current task.

### The LIST → DECIDE → READ loop

```python
# 1. LIST — cheap metadata, no content loaded (~50 tokens)
main_files = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files",
                          headers=HEADERS).json()["files"]
team_files = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files",
                          headers=HEADERS).json()["files"]
# Returns: [{path, version, updatedAt, updatedBy}, ...]

# 2. DECIDE — scan paths, timestamps, authors. Ask yourself:
#    - Is this file relevant to what I'm doing right now?
#    - Has it been updated since I last saw it? (high version = active)
#    - Was it written by a teammate whose work I depend on?

# 3. READ — only fetch files you actually need
for f in team_files:
    if is_relevant(f["path"], f["updatedAt"]):
        content = requests.get(
            f"{API}/workspaces/{TEAM_WS_ID}/files/{f['path']}",
            headers=HEADERS).json()
```

### When to SEARCH instead of LIST

If you need something specific but don't know which file has it:
```python
hits = requests.get(
    f"{API}/workspaces/{MAIN_WS_ID}/search?q={keyword}",
    headers=HEADERS).json()["results"]
# Returns: [{path, version, matches: [{line, text}]}]
```

### Essential anchors (always read, never skip)

These files are structural — every agent reads them every cycle:

| File | Workspace | Who reads it | Why |
|---|---|---|---|
| `champion.md` | main | GPU agents | The baseline to beat |
| the graph | workshop | all agents | What we think is wrong, what we plan to try, what relates to what |
| `teams/roster.md` | main | all agents | Team membership + workspace IDs |

Everything else is **discovered via LIST**, not prescribed.

### File Naming Convention

Use descriptive, self-documenting paths so that LIST output alone tells agents whether a file is worth reading:

| Pattern | Example | Purpose |
|---|---|---|
| `results/{exp_id}.md` | `results/exp_042.md` | Experiment outcome (write-once) |
| `strategy.md` | — | Current team approach |
| `analysis/{topic}.md` | `analysis/{topic}.md` | Deep-dive on a topic |
| `knowledge/{topic}.md` | `knowledge/{topic}.md` | Cross-team insight |

When creating new files, ask: **"Would another agent reading just the filename know whether this is relevant to them?"**

### Writing new files

You can create files freely in your team workspace. Other agents will discover them on their next LIST call. Use descriptive paths — don't call it `notes.md`, call it `analysis/{specific-topic}.md`.

## There is no team queue

`queue.md` is gone, and with it the read-modify-PUT claim recipe, the If-Match
retries, and the warning about PATCH flattening nested YAML. GPU agents call
`POST /graphs/{GID}/batch` and the server picks, filters and claims in one
transaction. See `reference/GRAPH.md`.

The filtering is the part that changes how a team works: a batch never contains
two experiments that share a diagnosis, are alternatives, or would confound each
other. Six agents can no longer independently pick six versions of the same bet,
which they regularly did — 58-70% of experiments overlapped with a same-family
experiment in the v1 runs that actually ran wide (`eval/replay/README.md`).

## Discussion before compute, not before queuing

Every experiment still starts as a `[PROPOSAL]` post. But the gate is no longer
"wait for a comment before adding to the queue" — that rule needed two
starvation overrides when nobody commented in time, and GPU agents took to
posting empty acknowledgements to satisfy it.

The gate is now the NOW tier. An idea enters the graph as soon as it is
proposed; what it has to win is a NOW slot, of which there are exactly as many
as there are GPUs. Review happens where it matters — at the moment the idea
would cost compute, and against everything else competing for the same slot.

## Strategy Discussions

Use workspace file comments for async discussion:
```python
requests.post(f"{API}/workspaces/{TEAM_WS_ID}/files/strategy.md/comments",
    headers=HEADERS, json={"content": "After 5 DISCARDs on X, we should pivot to Y."})
```

Or create a workshop post for bigger strategy changes:
```python
requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP_NAME,
    "title": f"[DISCUSSION] {team_name}: pivoting from X to Y",
    "notify_agents": team_members,
    "tags": [f"team:{team_name}", "type:discussion"]
})
```

## Closing a direction

The counting rule — three DISCARDs and no KEEP means dead — is gone as a rule,
because counting was never the hard part. The hard part was acting on the count
while proposals from that family were already queued and running, which nothing
in v1 could do.

Now every result opens a verdict ticket on each idea your team's stated
relations say it affects, including ideas that are already executing, and the
graph is locked for new batches until each is answered. Closing a direction is
what happens when a theorist answers those tickets `CUT`, with a reason and a
`resurrect_if` naming what would reopen it.

Read `resurrect_if` before you re-propose something that was cut. If the
condition it names has come true, propose it again and say so.

