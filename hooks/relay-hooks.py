#!/usr/bin/env python3
"""relay hooks for Claude Code: one entry point, the event comes from the hook JSON on stdin.

UserPromptSubmit  store the user's message verbatim in the rq inbox while this session's queue is active
PreToolUse(Bash)  record the session that runs rq; run `rq gate` before `git commit`
Stop              keep the turn alive while the queue (or the plan file) has open work
SessionStart      print the handoff and plan files so a fresh session resumes from them

Environment:
  RQ_BIN             path to rq (default: ../skills/relay/bin/rq next to this file, then `rq` on PATH)
  RELAY_ASK_MARKER   comma-separated markers that let a message end the turn with a real question
                     (default "WAITING FOR ANSWER"; the first one is what the agent is told to write)
  RQ_CTX_TOKENS      context size that triggers the one-time handoff request (default 200000)

Every failure to find or run rq is silent: a hook must never break the session.
"""
import json, os, re, shutil, subprocess, sys, time

CTX_LIMIT = int(os.environ.get("RQ_CTX_TOKENS", "200000"))
MARKERS = [m.strip() for m in os.environ.get("RELAY_ASK_MARKER", "WAITING FOR ANSWER").split(",") if m.strip()]
STUCK = 3  # identical open state on this many consecutive stop attempts releases the turn
PLAN_MAX_AGE = 12 * 3600  # a plan file untouched for this long is ignored


def find_rq():
    here = os.path.dirname(os.path.realpath(__file__))
    for c in (os.environ.get("RQ_BIN"), os.path.join(here, "..", "skills", "relay", "bin", "rq"), shutil.which("rq")):
        if c and os.path.isfile(c):
            return os.path.abspath(c)


RQ_PATH = find_rq()
if not RQ_PATH:
    sys.exit(0)
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
cwd = d.get("cwd") or os.getcwd()
ev, sid = d.get("hook_event_name"), d.get("session_id") or ""


def rq(*a, inp=None):
    return subprocess.run([sys.executable, RQ_PATH, *a], cwd=cwd, input=inp, capture_output=True, text=True, timeout=60)


def status():
    try:
        return json.loads(rq("status", "--json").stdout)
    except Exception:
        return {}


def out(**extra):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": ev, **extra}}))


def on_prompt():
    text = (d.get("prompt") or "").strip()
    if not text or text.startswith("/"):
        return
    st = status()
    if not st.get("exists"):
        return
    if st.get("session") == sid:
        r = rq("inbox", "--add", inp=text)
        if r.returncode == 0:
            n = r.stdout.strip()
            out(additionalContext=f"relay: the message is stored verbatim as inbox #{n}. It does not interrupt the current item. "
                                  f"When it is a request: `rq add TITLE --inbox {n}`; when not: `rq inbox --skip {n}`.")
    else:
        out(additionalContext=f"relay: a queue from another session exists here ({st.get('open')} open, next {st.get('action')}). "
                              "It is stale: ignore it unless the user asks to continue it (then run `rq next`).")


def on_bash():
    cmd = (d.get("tool_input") or {}).get("command", "")
    if re.search(r"(^|[;&|\s/])rq\s+(init|add|next|set|evidence|verify|check|close|inbox)\b", cmd) and sid:
        base = status().get("base")  # the session that runs rq owns the queue
        if base:
            os.makedirs(base, exist_ok=True)
            with open(os.path.join(base, "session"), "w") as f:
                f.write(sid)
    if re.search(r"\bgit\b[^;&|\n]*?\bcommit(?![-\w])", cmd) and status().get("exists"):
        wt = re.search(r"\bgit\b[^;&|\n]*\badd\b|\bcommit\b[^;&|\n]*(\s-\w*a\w*|\s--all)\b", cmd)
        r = rq("gate", *(["--worktree"] if wt else []))
        if r.returncode:
            out(permissionDecision="deny", permissionDecisionReason="relay commit gate:\n" + r.stdout.strip())


def tail_info(path):
    """(assistant text of the current turn, current context tokens) from the end of the transcript."""
    text, ctx = [], 0
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 3_000_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except Exception:
                continue
            m = e.get("message", {})
            if e.get("type") == "user":
                c = m.get("content")
                if isinstance(c, str) or any(isinstance(b, dict) and b.get("type") != "tool_result" for b in c or []):
                    break  # the user's message that started this turn
            if e.get("type") != "assistant":
                continue
            if not ctx and m.get("usage"):
                u = m["usage"]
                ctx = sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            text.append("".join(b.get("text", "") for b in m.get("content", []) if isinstance(b, dict) and b.get("type") == "text"))
    except Exception:
        pass
    return "\n".join(text), ctx


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def on_stop():
    st = status()
    base = st.get("base")
    qs = st if st.get("exists") else None
    mine = bool(qs and qs.get("session") == sid)  # a queue of another session is stale, never forced
    plan, left = None, []
    if not mine and base:
        p = os.path.join(base, "PLAN.md")
        if os.path.isfile(p) and time.time() - os.path.getmtime(p) <= PLAN_MAX_AGE:
            with open(p, encoding="utf-8") as f:
                left = [l.strip() for l in f if l.lstrip().startswith("- [ ]")]
            plan = p
    queue_open = bool(mine and qs["open"] > 0 and qs["action"] not in ("ASK", "BLOCKED"))
    if not queue_open and not left:
        return
    text, ctx = tail_info(d.get("transcript_path", ""))
    if any(m in text for m in MARKERS):
        return  # the agent ended with a real question
    marker = MARKERS[0]
    state_path = os.path.join(base, "hook-state.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    sig = sid + "|" + ((qs["signature"] + "|" + qs["action"]) if queue_open else "\n".join(left))
    state["n"] = state.get("n", 0) + 1 if state.get("sig") == sig else 1
    state["sig"] = sig
    stuck = state["n"] > STUCK
    ctx_due = ctx > CTX_LIMIT and state.get("ctx_session") != sid
    if ctx_due:
        state["ctx_session"] = sid
    if stuck:
        state["n"] = 0
    try:
        os.makedirs(base, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass
    if stuck:
        return
    if ctx_due:
        block(f"The context is ~{ctx // 1000}k tokens. The queue/plan carries the state: overwrite {os.path.join(base, 'RESUME.md')} "
              "with the handoff (goal, done, open items, branch/HEAD, exact next step, verify commands) and end with "
              f"`{marker}: start a fresh session from this handoff?`")
    if queue_open:
        nxt = f"{qs['action']} {qs['item'] or ''} {qs['title'] or ''}".strip()
        block(f"The queue is not finished ({qs['open']} open). Next: {nxt}. Do not write a report or ask: run `rq next --start` "
              f"and follow its ACTION. A question is allowed only as the last block, starting with `{marker}:`, for a real blocker.")
    block(f"The plan {plan} is not finished ({len(left)} steps). Continue with the next step without asking. Ask only on a real "
          f"blocker, as the last block starting with `{marker}:`. Open steps:\n" + "\n".join(left[:5]))


def on_session_start():
    base = status().get("base")
    for name in ("RESUME.md", "PLAN.md"):
        p = os.path.join(base or "", name)
        if base and os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                print(f"=== {name} ({p}) ===\n{f.read()}")


if ev == "UserPromptSubmit":
    on_prompt()
elif ev == "PreToolUse" and d.get("tool_name") == "Bash":
    on_bash()
elif ev == "Stop":
    on_stop()
elif ev == "SessionStart":
    on_session_start()
