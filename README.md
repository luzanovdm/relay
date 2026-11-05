# Relay

A strict work loop for coding agents (Claude Code, Codex): turn a batch of user feedback into a queue, work it **one item at a time**, and close each item only with **proof** that its acceptance criteria hold.

Three parts:

- **`rq`**: a single-file Python CLI (standard library only) that owns the queue. Statuses, IDs, guards and the file format live in code, so an agent cannot "just edit the markdown" into a wrong state.
- **The `relay` skill** (`skills/relay/SKILL.md`): the rules the agent follows: intake, triage, the loop, checks, commit gate.
- **Hooks for Claude Code** (`hooks/relay-hooks.py`): make the loop hard to fall out of: no message lost, no early stop, no half-finished commit.

## Why

Agents handed a list of fixes tend to patch symptoms, drop messages that arrive mid-task, declare victory without evidence, and leave debt behind. Relay encodes the opposite habits:

- **Triage before work.** Every request becomes an item with the original wording, root cause (`local | shared | kit | contract | architecture`), a root-level solution, and acceptance criteria. `rq` refuses to start an item until that is filled in.
- **No local patches over shared defects.** The solution is written at the owning level; consumers are listed from `git grep`, not from memory.
- **One item at a time, new feedback never interrupts.** Messages land verbatim in an inbox and become their own items; the current item is not paused or widened.
- **Proof, not claims.** Each criterion has a `check:` and ideally an executable `run:`. An item completes only from `implemented` with evidence on every criterion. A failing `rq verify` reopens it.
- **No debt in touched code.** Criteria and the loop leave no room for "fix later": it is fixed in the change, or named as a blocker with a reason.
- **The turn does not end while work is open.** The Stop hook blocks stopping until the queue is empty or the agent asks one real question.

## Install

Requires Python 3.9+ and git on macOS, Linux or WSL (queue writes are locked with `fcntl`; native Windows is not supported).

### Claude Code plugin

```text
/plugin marketplace add luzanovdm/relay
/plugin install relay@relay
```

This installs the skill (`/relay:relay`) and registers the four hooks. The agent calls `rq` by the path the skill gives it; to use it yourself, link it onto your `PATH` (below).

### Manual (Claude Code)

```bash
git clone https://github.com/luzanovdm/relay ~/relay
mkdir -p ~/.claude/skills ~/.local/bin
ln -s ~/relay/skills/relay ~/.claude/skills/relay
ln -s ~/relay/skills/relay/bin/rq ~/.local/bin/rq
```

Then add the hooks to `~/.claude/settings.json` (adjust the path):

```json
{
  "hooks": {
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python3 ~/relay/hooks/relay-hooks.py" }] }],
    "PreToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3 ~/relay/hooks/relay-hooks.py" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "python3 ~/relay/hooks/relay-hooks.py" }] }],
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "python3 ~/relay/hooks/relay-hooks.py" }] }]
  }
}
```

### Codex

Codex has no hooks. Put the skill where Codex reads skills (for example `~/.agents/skills/relay`, a symlink to `skills/relay`) and link `rq` onto `PATH`. The skill's *Codex* section lists the steps the hooks would otherwise do (store messages with `rq inbox --add`, check `rq status --json` before the final message, run `rq gate --worktree` before commits).

## Quickstart

In a repo, tell the agent: `/relay` and paste your list of fixes. It will:

```bash
rq init "feedback round 1"
rq add "label cursor is wrong" --original - <<< "the cursor on the label looks like a text caret"
rq next --start      # prints ACTION: TRIAGE_NEEDED / WORK / VERIFY / CLOSE ...
rq check Q-001       # per-edit, cheap
rq set Q-001 implemented
rq verify            # runs run: commands, records evidence, completes proven items
rq close             # archives the batch
```

A triaged item carries criteria such as:

```text
- A1: cursor is the default arrow on the label | check: spec | fast: npx tsc --noEmit | run: npx vitest run label.spec.ts | evidence:
```

Run `python3 skills/relay/bin/rq --help` for every command; `skills/relay/SKILL.md` is the full contract.

## Hooks

`hooks/relay-hooks.py` is one script; the event comes from the hook payload.

| Event | What it does |
|---|---|
| `UserPromptSubmit` | Stores the message verbatim in the inbox while *this session's* queue is active, and tells the agent its number. |
| `PreToolUse` (Bash) | Records which session runs `rq` (it owns the queue); denies `git commit` when `rq gate` fails (a file of an unfinished item, or one touched by several open items). |
| `Stop` | Blocks stopping while the queue (or an open `PLAN.md` checklist) has work, unless the message ends with the ask marker. Releases after 3 identical blocked attempts (stuck guard). Past the context limit it asks once for a `RESUME.md` handoff. |
| `SessionStart` | Prints `RESUME.md` and `PLAN.md` so a fresh session continues from them. |

A queue belongs to the session that last ran `rq`; in any other session hooks neither capture nor block and say "stale queue".

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `RQ_DIR` | `$(git rev-parse --git-path agent)`, outside Git `./.agent` | Where queue, inbox, history, state files live. |
| `RQ_BIN` | `rq` next to the hook, then `PATH` | Which `rq` the hooks run. |
| `RELAY_ASK_MARKER` | `WAITING FOR ANSWER` | Comma-separated markers that let a message end the turn with a real question (first one is what the agent is told to write; set your language's phrase here). |
| `RQ_CTX_TOKENS` | `200000` | Context size that triggers the one-time handoff request. |
| `RQ_PW` | `npx playwright test --reporter=json ...` | Command for batched `pw:<spec>` criteria. |
| `RQ_TIMEOUT` | `600` | Seconds per `check`/`verify` command. |
| `RQ_ROOTS` | none | Extra roots for `rq refs`, colon-separated. |
| `RQ_GC_EXTRA` | none | Extra private directories next to `RQ_DIR` for `rq gc`, colon-separated names. |

Set them in your shell or in the `env` block of Claude Code's `settings.json`.

## Files and privacy

State is private and never committed: it lives inside `.git/agent/` (per worktree) or `./.agent/` outside Git: `revision-queue.md`, `revision-queue/<ID>.md`, `revision-history/`, `revision-assets/`, plus `RESUME.md` and `PLAN.md`. Nothing is sent anywhere; `rq` and the hooks make no network calls. `rq gc` is a dry run unless you pass `--apply`.

## Security model

`fast:` and `run:` in a triage file are executed as shell commands in the repo root (`rq check`, `rq verify`). The queue is therefore trusted input written by you and your agent, in a private directory. Do not point `RQ_DIR` at a directory others can write, and do not run `rq verify` on a queue you did not create. `rq` itself makes no network calls and writes only inside `RQ_DIR`; what your `run:` commands do is up to them.

## Limitations

- POSIX only (`fcntl`), Python 3.9+.
- One writer per checkout: files edited by someone else while an item is in progress are attributed to it.
- The hooks are Claude Code only; with Codex the guarantees are the agent's discipline (see the skill's *Codex* section).
- `rq consumers` is a heuristic over `git grep` (selectors and top-level definitions in TS/JS, Python, Swift, Go, Kotlin, Java, Rust), not a language server.

## Tests

```bash
python3 tests/test_rq.py
python3 tests/test_hooks.py
```

CI runs both on Linux and macOS.

## License

MIT. See [LICENSE](LICENSE).
