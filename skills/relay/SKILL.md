---
name: relay
description: Run a batch of user revisions as a strict work loop with the rq tool: triage, one item at a time, proven acceptance criteria, new feedback never interrupts. Use when the user asks for a revision queue or a batch of fixes.
---

# Relay: revision work loop

Shared by Claude and Codex. The queue is a work loop driven by the `rq` tool, not
a notebook: the turn does not end while an open item exists. One writer owns a
checkout; this skill creates no other task, team, branch or publication gate.

An item is a **triaged request**, not the user's wording: original message, root
cause, root-level solution, acceptance criteria and priority, worked out before
implementation and without slowing the current work.

## rq: the only way to touch the queue

`rq` is `bin/rq` next to this file (Python 3 standard library, macOS/Linux/WSL). It owns the file
format, IDs, statuses and guards. If `rq` is not on PATH, call it by that path. Never edit
`revision-queue.md` by hand. Files live in `$(git rev-parse --git-path agent)` (outside Git `./.agent/`;
`RQ_DIR` overrides): `revision-queue.md`, `revision-queue/<ID>.md` (detail), `revision-history/`,
`revision-assets/<ID>/`, plus the state files `RESUME.md` (handoff) and `PLAN.md` (checklist plan).
Private, never committed.

| Command | Use |
|---|---|
| `rq init <batch>` | start a batch |
| `rq add "<title>" --original - [--image P --desc "visible details"] [--prio P1] [--family Q-3]` | intake; new item is `triage`, detail file created, original stored verbatim from stdin |
| `rq next [--start]` | **the loop driver**: prints `ACTION:` and the item, its criteria and next step; `--start` takes it |
| `rq set <ID> <status\|-> [key=value ...]` | change status/fields in place, guarded; `rq set <ID> - next="..."` records where to resume |
| `rq check <ID>` | run the item's `fast:` commands (per-edit, cheap) |
| `rq verify [ID ...]` | run `run:` commands, record evidence, auto-complete proven items |
| `rq evidence <ID> A<n> "<text or path>"` | manual evidence |
| `rq show/list/lint/related <path>/stats/status --json` | read, validate, reuse, measure |
| `rq inbox` / `rq add "<title>" --inbox N` / `rq inbox --skip 3,4` | the user's messages stored verbatim by a hook; convert or skip each |
| `rq refs <words> [--pin ID]` | newest source-of-truth files by mtime; `--pin` writes the winner into Refs |
| `rq consumers <path> [--write ID]` | consumers of a file from `git grep` (selector, class, file name) with their specs |
| `rq gate [--worktree]` | commit gate (run by a git-commit hook): staged (or all changed) files vs files touched per item |
| `rq close [--carry IDS]` | archive the batch |
| `rq gc [--days N] [--apply]` | dry-run list of stale private files; `--apply` deletes |

Guards `rq` enforces: an item cannot be `pending`/`in_progress`/`implemented`
until its detail file is triaged (cause level, solution, every criterion with a
`check:`); one `in_progress` at a time; `implemented` only from `in_progress`;
`completed` only from `implemented` with evidence on every criterion (a failing
`rq verify` reopens the item to `pending`; entering `in_progress` clears old
evidence); `needs_approval` needs `decision=` and is left only with `answer=`;
`blocked` and `superseded` need `reason=`. Statuses: triage, pending, in_progress,
blocked, implemented, needs_approval, completed, superseded. `[ ]` keeps the loop
alive, `[?]` (needs_approval) waits for the user, `[x]` is archived at once.

## Intake

A hook stores every user message verbatim in the inbox while a queue is active and
tells you its number. `rq next` shows `INTAKE` when no item is in progress, and the
Stop hook counts unconverted messages as open work, so a message cannot be lost.
Each actionable request becomes its own item via `rq add "<title>" --inbox N`
(several requests = several items; images still go with `--image/--desc`); a
message that is not a request is `rq inbox --skip N`. Intake is bookkeeping: it never starts work
on the new item and never changes, pauses or widens the current one; return to the
current item exactly where it was. An answer to an approval is resolved with
`rq set <ID> pending answer="..."`, not added as a new item.

Screenshots: inspect each image once and pass the visible details with `--desc`
(stored in the detail file); afterwards work from the text and reload the image
only when a detail is missing. Never invent a copied file or an observation.

## Triage (parallel, never blocks current work)

Fill the detail file (`rq show <ID>` shows the template): **Original** (verbatim),
**Cause** (`level: local|shared|kit|contract|architecture`: one file, a component several files
use, a shared kit/library/design system, an API or data contract, the system structure; `file:line`
evidence, all consumers of the affected unit), **Solution** (fix at the owning level: owner
-> state/contract -> consumer -> recovery; consumers that change; what is NOT done,
no local patches hiding a shared defect), **Acceptance criteria**, **Refs**,
**Priority** (P0 blocks user/data loss, P1 broken core flow, P2 visible defect, P3
polish; size S/M/L; deps — the user's explicit priority wins), **Confidence**.

- **Depth by size.** Quick look first. `rq consumers <path> --write <ID>` builds the
  consumer list from `git grep` (not from memory) and stores it with HEAD and date;
  `rq related <path>` shows earlier items that touched the same file. One local file,
  one consumer, obvious cause: do it inline. Shared component, kit, contract,
  several consumers or unknown cause: a read-only subagent writes the content,
  the main session stores it. `consumers` and `related` work on any language (top-level
  definitions plus component selectors; test files by the usual `.spec`/`.test`/`_test`/`test_`/`Tests` names).
- **Family.** Items with one suspected cause go to one triage (`family:`), so the
  cause is found once. At most 3 triages in flight.
- Result: `rq set <ID> pending prio=P1`, or `rq set <ID> needs_approval decision="..."`
  when any holds: low confidence; more than one reasonable solution; change to the
  kit, architecture or a contract; user-visible behaviour beyond what was asked;
  removing a behaviour; criteria narrower than the request.

### Acceptance criteria (written during triage, before implementation)

`- A1: <observable outcome> | check: <how proven> | fast: <cmd> | run: <cmd> | evidence:`
Cover: the original failure no longer reproduces (steps), each affected consumer
still works, edge/empty/error states. Prefer executable proof: `run:` is a command
or test/Playwright scenario (stored as `agent/revision-queue/<ID>.spec.*`);
`fast:` is a cheap per-edit check (typecheck of the unit, one spec). Manual
evidence only where nothing can be automated. The implementer never weakens a
criterion; narrowing needs a new triage and approval.

### Approval

For a `[?]` item tell the user **immediately**, in the message: the item, the
decision in one or two sentences with the alternative, the recommendation and the
detail path. Do not stop or wait: other items keep moving. Unanswered items are
repeated in every report with the exact question.

### Sources of truth (mockups, specs, tasks)

Every item that mentions a design, mockup, page or doc gets exact `Refs`, resolved
before triage ends, never from memory: `rq refs <words> --pin <ID>` lists matches
under the repo and the private agent dir (extra roots: `--root` or `RQ_ROOTS`) newest
first, prints `CHECK` when the newest and the runner-up live in different folders
(confirm the newest is the intended one), and pins path plus date. Directory names are not refs. A newer file that contradicts
an older one wins; note it. A missing source keeps the item `blocked`.

## The loop

Repeat `rq next --start`, act on `ACTION`, until it says `CLOSE`/`ASK`/`BLOCKED`:

- **WORK** — one item only. Re-read its detail file and compare the solution with
  the current code (re-triage if the premise broke). Implement exactly the triaged
  solution; touch only what it needs; never edit for several items in one pass.
  Items of one family may follow each other in the same context (files read once),
  each with its own criteria and evidence. After each edit run `rq check <ID>`.
  Do not reset/stash/clean or include another item's changes. When done:
  `rq set <ID> implemented`. Delegated implementation stays `in_progress` until
  integrated and rechecked. Only an explicit urgent instruction in the user's own words
  ("urgent", "first", "stop, do ... now") preempts: `rq set <current> pending next="<exact
  resume point>"`, `rq add ... --prio P0`, triage it, `rq next --start`; the paused
  item resumes before fresh ones of its priority.
- **INTAKE** — convert or skip the inbox messages, then return to the loop.
- **TRIAGE_NEEDED** — triage the item now (inline, or a read-only subagent); the
  `note:` of WORK lists untriaged ids to start in parallel. Never wait idle.
- **VERIFY** — `rq verify` (failures reopen the item as `pending`); for `MANUAL`
  criteria collect evidence and run `rq evidence`. Manual criteria of P0/P1 and
  kit/architecture items are checked by a subagent that did not implement them.
- **CLOSE** — `rq close`, then report the archive path and per-item results.
- **ASK** — report the `[?]` decisions with exact questions.
- **BLOCKED** — report the missing thing for each blocked item.

Never between iterations: a summary, "what next", offering queued items, a
report. Decisions already settled by a plan, triage or refs are not asked again.
A real blocker that only the user can resolve ends the message with one concrete question
as its last block, starting with the ask marker (`WAITING FOR ANSWER:`; `RELAY_ASK_MARKER`
changes it). The Stop hook releases the turn only for that marker, for `ASK`/`BLOCKED`, or
when nothing is open.

Failures: adding an item and stopping; reporting while items are open; abandoning
the current item because new feedback arrived; several items at once; implementing
an untriaged item; completing without per-criterion evidence; a local patch over a
shared cause; a mockup resolved from memory or a directory name.

## Checks

`rq` records the duration of every `check`/`verify`; keep `fast:` to seconds and the
full run in `VERIFY`. "Tests only at the end" moves checks into `VERIFY`; it never
lets an item complete without evidence. UI criteria
use `run: pw:<spec path>`: `rq verify` runs all of them in one Playwright invocation
(`RQ_PW` overrides the command) and records evidence per criterion.

## Commit gate

`rq set <ID> in_progress` snapshots the dirty files; `implemented` records the files
the item touched. A git-commit hook runs `rq gate` and denies a commit that stages a
file of an unfinished item or one touched by several open items; commit them apart or
finish the item first. Files edited by someone else while an item is in progress are
attributed to it: keep one writer per checkout.

## Session size

The Stop hook measures the current context. Past ~200k tokens (`RQ_CTX_TOKENS`) it asks once
for a handoff written over `RESUME.md` (goal, done, open, branch/HEAD, exact next step, verify
commands) and a fresh session; the SessionStart hook prints `RESUME.md` and `PLAN.md` there, and
the queue carries the state. A `PLAN.md` checklist with open `- [ ]` steps keeps the turn alive
the same way a queue does (ignored when untouched for 12 hours). A queue belongs to
the session that last ran `rq` (hook-written `session` file). In any other session
the hooks neither capture messages nor block stopping and say "stale queue"; ignore
it unless the user asks to continue: then `rq next` adopts it.

## Measure and clean

`rq stats` shows work time, reopens, failed checks and check time per item. A reopen
after VERIFY means triage missed something: say what. `rq gc` is a dry run; apply
it only on the user's yes.

## Hooks (Claude Code)

`hooks/relay-hooks.py` (one script, installed by the plugin or the README's settings snippet)
does four things: store each user message in the inbox, record which session owns the queue,
deny `git commit` when `rq gate` fails, keep the turn alive while work is open and print the
handoff at session start. Without it everything above still works, but the guarantees that
nothing is lost, stopped early or committed half-done become your job (see Codex).

## Codex

No hooks, so do by hand what they do in Claude: pipe every user message to
`rq inbox --add` (or `rq add "<title>" --original -`) before anything else, run
`rq status --json` before the final message (stop only on `CLOSE`/`ASK`/`BLOCKED`
with nothing open) and `rq gate --worktree` before every commit.

## Commits and delivery

One commit per item only when the user requests that policy. Otherwise save
coherent authorized batches with explicit paths and normal hooks. No push, PR,
merge or deploy permission follows from the queue.

## After context loss

`rq next` shows the current item, its `next:` and detail path. Re-run lost
triages, reconcile the actual branch/source and the result, resume the loop. Do
not read archives or equate an existing commit with acceptance.
