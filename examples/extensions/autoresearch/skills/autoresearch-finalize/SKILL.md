---
name: autoresearch-finalize
description: Split a noisy autoresearch branch into clean, independent branches — one per logical change — ready for individual review
---

An autoresearch branch is a working log, not a deliverable: kept, discarded,
and crashed commits sit interleaved, with dead-end attempts still in the
history. This skill turns the *kept* commits into a small number of clean
branches — one per logical, non-overlapping change — each starting from the
same merge-base as the original branch, so they can be reviewed and merged
independently.

Read `.auto/log.jsonl` and the current git history yourself rather than
trusting a summary held in context — this is a git-surgery task, and stale
assumptions about which commit is which cause silent data loss.

## 0. Preconditions

- Must be on the autoresearch branch (not `main`/`master`) with a clean
  working tree (`git status --porcelain` empty). Stop and say so if not.
- `.auto/log.jsonl` must exist and contain at least one `"status": "keep"`
  result whose commit actually changed files (a bare "baseline" run usually
  didn't). If there's nothing to split out, say so and stop rather than
  manufacturing a branch.

## 1. Collect the kept commits

Read `.auto/log.jsonl` (skip `"type": "hook"` lines — they're not
experiments). For the segment you're finalizing (normally the current one —
ask if the log has multiple segments and it isn't obvious which to use),
build the list of `"status": "keep"` results with their `commit`, `metric`,
`description`, and `metrics`.

For each kept commit, resolve the files it touched:

```
git show --name-only --pretty=format: <commit>
```

Drop any commit with an empty file list (no-op runs, e.g. the baseline).

## 2. Group by shared files

**Constraint: groups must not share files**, so each resulting branch can be
reviewed and merged without touching what another branch touches.

Compute connected components over "commits that touch a common file are in
the same group" (union-find, or just iterate: keep a list of `{files, commits}`
groups, and merge two groups whenever a new commit's file set intersects
either of them). Do this by hand for a handful of commits; for a long branch,
write a short throwaway script rather than tracking it in your head — errors
here silently corrupt someone's git history.

The result is 1 branch per group. A branch covering only one commit is fine
and common.

## 3. Propose the grouping — get approval before touching git

Show, per group: the files touched, the commits in chronological order, and
the net metric movement (baseline → best *within that group*, using the
session's `metric_name`/`metric_unit`/`direction` from the log's config
header). Use `ask_user` if available; otherwise state the plan plainly and
wait for a go-ahead. Do not create branches speculatively — this step exists
because grouping heuristics are not infallible, and the user knows the code
better than the file-overlap graph does.

## 4. Find the merge-base

```
git merge-base <autoresearch-branch> <upstream>   # e.g. main, master, or the branch's tracked upstream
```

If there's no obvious upstream, use the branch's first commit (typically the
"baseline" experiment, which has no diff) as the merge-base instead.

## 5. Build each branch

For every approved group, in file order doesn't matter but commit order
within the group must stay chronological:

```
git checkout -b <branch-name> <merge-base>
git cherry-pick <commit-1> <commit-2> ... <commit-n>   # oldest first
```

Name branches for what they do, not for autoresearch bookkeeping — e.g.
`autoresearch/cache-parsed-ast`, not `autoresearch/group-2`.

Then squash the group into a single reviewable commit:

```
git reset --soft <merge-base>
git commit -m "<subject>

<one line per included experiment: what changed, and its effect>

<metric_name>: <baseline> -> <best> (<+/-N%>)"
```

Write the subject as what the change *does*, not "autoresearch group N". The
metric line is the evidence a reviewer needs to trust the change without
re-running the benchmark themselves.

If a cherry-pick conflicts, stop and resolve it manually rather than
force-resolving blind — a conflict usually means the grouping missed a
shared dependency between files (e.g. one file imports a symbol another
group renamed). Fix the grouping and start that branch over rather than
patching around it.

## 6. Wrap up

- `git checkout <autoresearch-branch>` to return — the original branch is
  untouched; finalizing only ever reads from it and creates new branches.
- List the branches created, each with its one-line summary and metric
  delta, so the user can open PRs (or `git push -u origin <branch>`) for
  whichever ones they want.
- Leave `.auto/` alone. It's the source of truth for this session and other
  segments may still be in flight.

Do not delete or rewrite the autoresearch branch itself — it stays as the
full experimental record even after finalizing.
