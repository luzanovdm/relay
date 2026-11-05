#!/usr/bin/env python3
"""Self-check for hooks/relay-hooks.py: python3 tests/test_hooks.py"""
import json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HOOK = os.path.join(ROOT, "hooks", "relay-hooks.py")
RQ = os.path.join(ROOT, "skills", "relay", "bin", "rq")


def wr(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


class Case:
    """A fresh queue directory; `rq` and `hook` run exactly as the agent and Claude Code would."""

    def __init__(self, **extra):
        self.dir = tempfile.mkdtemp()
        self.agent = os.path.join(self.dir, "agent")
        self.env = {**os.environ, "RQ_DIR": self.agent, "RQ_BIN": RQ, "PYTHONWARNINGS": "error", **extra}

    def rq(self, *a, inp=None):
        r = subprocess.run([sys.executable, RQ, *a], cwd=self.dir, env=self.env, capture_output=True, text=True, input=inp)
        assert r.returncode == 0, f"rq {a}\n{r.stdout}{r.stderr}"
        return r.stdout

    def hook(self, event, hook=HOOK, **payload):
        p = {"hook_event_name": event, "cwd": self.dir, "session_id": "S1", **payload}
        r = subprocess.run([sys.executable, hook], input=json.dumps(p), env=self.env, capture_output=True, text=True)
        assert r.returncode == 0 and not r.stderr, f"{event} rc={r.returncode}\n{r.stdout}{r.stderr}"
        return r.stdout.strip()

    def transcript(self, text, tokens=0):
        p = os.path.join(self.dir, "t.jsonl")
        rows = [{"type": "user", "message": {"content": "go"}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": text}],
                                                  "usage": {"input_tokens": tokens}}}]
        wr(p, "".join(json.dumps(r) + "\n" for r in rows))
        return p

    def queue(self, session="S1"):
        self.rq("init", "t")
        wr(os.path.join(self.agent, "session"), session)


def reason(out):
    j = json.loads(out)
    assert j["decision"] == "block", out
    return j["reason"]


# no queue: nothing stored, nothing blocked
c = Case()
assert c.hook("UserPromptSubmit", prompt="fix the label") == ""
assert c.hook("Stop") == ""

# prompt capture: this session's queue stores the message, a slash command is not stored, another session's queue is stale
c = Case(); c.queue()
out = json.loads(c.hook("UserPromptSubmit", prompt="fix the label"))["hookSpecificOutput"]["additionalContext"]
assert "inbox #1" in out and "fix the label" in c.rq("inbox"), out
assert c.hook("UserPromptSubmit", prompt="/relay") == "" and "#2" not in c.rq("inbox")
out = json.loads(c.hook("UserPromptSubmit", prompt="hello", session_id="S2"))["hookSpecificOutput"]["additionalContext"]
assert "stale" in out and "hello" not in c.rq("inbox")

# the session that runs rq owns the queue
c = Case(); c.rq("init", "t")
for cmd in ("ls && rq next --start", f"{RQ} next --start"):  # on PATH and by absolute path
    c = Case(); c.rq("init", "t")
    c.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd})
    with open(os.path.join(c.agent, "session"), encoding="utf-8") as f:
        assert f.read() == "S1", cmd

# stop: an open queue of this session blocks, a real question releases, another session's queue is ignored
c = Case(); c.queue(); c.rq("inbox", "--add", inp="unconverted message")
r = reason(c.hook("Stop"))
assert "rq next --start" in r and "WAITING FOR ANSWER:" in r, r
assert c.hook("Stop", transcript_path=c.transcript("WAITING FOR ANSWER: which one?")) == ""
assert c.hook("Stop", session_id="S2") == ""

# stop: identical state three times blocks, the fourth attempt is released (stuck guard)
c = Case(); c.queue(); c.rq("inbox", "--add", inp="m")
assert all(c.hook("Stop") for _ in range(3)) and c.hook("Stop") == ""

# stop: past the context limit the first stop asks for the handoff file once per session
c = Case(RQ_CTX_TOKENS="1000"); c.queue(); c.rq("inbox", "--add", inp="m")
t = c.transcript("working", tokens=5000)
r = reason(c.hook("Stop", transcript_path=t))
assert "RESUME.md" in r and "start a fresh session" in r, r
assert "RESUME.md" not in reason(c.hook("Stop", transcript_path=t))

# a configured marker replaces the default one everywhere
c = Case(RELAY_ASK_MARKER="NEED INPUT,ASK USER"); c.queue(); c.rq("inbox", "--add", inp="m")
assert "NEED INPUT:" in reason(c.hook("Stop"))
assert c.hook("Stop", transcript_path=c.transcript("ASK USER: which one?")) == ""

# plan file: open steps keep the turn alive, a finished plan does not; SessionStart prints both state files
c = Case()
wr(os.path.join(c.agent, "PLAN.md"), "- [x] done\n- [ ] next step\n")
assert "next step" in reason(c.hook("Stop"))
wr(os.path.join(c.agent, "PLAN.md"), "- [x] done\n")
assert c.hook("Stop") == ""
wr(os.path.join(c.agent, "RESUME.md"), "goal: ship it")
out = c.hook("SessionStart")
assert "RESUME.md" in out and "goal: ship it" in out and "PLAN.md" in out, out
assert Case().hook("SessionStart") == ""

# commit gate: a failing `rq gate` denies `git commit` only, and only while a queue exists
c = Case()
stub = os.path.join(c.dir, "rq-stub")
wr(stub, "import json, sys\n"
         f"if sys.argv[1] == 'status': print(json.dumps({{'exists': True, 'base': {c.agent!r}, 'open': 1, 'action': 'WORK'}}))\n"
         "else: print('f.ts: belongs to unfinished Q-001'); sys.exit(1)\n")
c.env["RQ_BIN"] = stub
out = json.loads(c.hook("PreToolUse", tool_name="Bash", tool_input={"command": "git commit -m x"}))["hookSpecificOutput"]
assert out["permissionDecision"] == "deny" and "unfinished Q-001" in out["permissionDecisionReason"], out
assert c.hook("PreToolUse", tool_name="Bash", tool_input={"command": "git status"}) == ""
c.env["RQ_BIN"] = RQ
assert Case().hook("PreToolUse", tool_name="Bash", tool_input={"command": "git commit -m x"}) == ""

# rq not found anywhere: silent no-op
c = Case(RQ_BIN="/nonexistent", PATH="/usr/bin:/bin")
lone = os.path.join(c.dir, "hooks", "relay-hooks.py")
os.makedirs(os.path.dirname(lone))
shutil.copy(HOOK, lone)
assert c.hook("Stop", hook=lone) == ""
print("hooks ok")
