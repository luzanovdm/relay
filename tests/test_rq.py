#!/usr/bin/env python3
"""Self-check for rq: python3 tests/test_rq.py (fails loudly on any broken rule)."""
import json, os, subprocess, sys, tempfile

RQ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "relay", "bin", "rq")
tmp = tempfile.mkdtemp()
# PYTHONWARNINGS=error: a leaked file handle or other warning inside rq fails the run
env = {**os.environ, "RQ_DIR": os.path.join(tmp, "agent"), "PYTHONWARNINGS": "error"}


def rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def wr(p, text):
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def rq(*a, ok=True, inp=None):
    r = subprocess.run([sys.executable, RQ, *a], cwd=tmp, env=env, capture_output=True, text=True, input=inp)
    assert (r.returncode == 0) == ok, f"rq {a} rc={r.returncode}\n{r.stdout}{r.stderr}"
    return r.stdout + r.stderr


def fill(i, run="true"):
    p = os.path.join(tmp, "agent", "revision-queue", i + ".md")
    t = rd(p)
    t = t.replace("level: <local|shared|kit|contract|architecture>", "level: shared")
    t = t.replace("(root-level fix: owner -> state/contract -> consumer -> recovery; consumers that change; what is NOT done)",
                  "Fix the shared label component; consumers inherit; no local patches.")
    t = t.replace("- A1: <original failure no longer reproduces> | check: <how proven> | fast: <cmd> | run: <cmd> | evidence:",
                  f"- A1: cursor is default | check: spec | fast: true | cat | run: {run} | evidence:")
    wr(p, t)


rq("init", "t", "--owner", "b")
out = rq("add", "label cursor", "--original", "café ☕ the cursor on the label is wrong", "--prio", "P1")
i = out.split()[0]
assert i == "Q-001", out
assert "café ☕" in rd(os.path.join(tmp, "agent", "revision-queue", i + ".md"))  # Original is stored verbatim, non-ASCII included
rq("add", "second", "--original", "x", "--image", "nope.png", "--desc", "d", ok=False)
rq("add", "second", "--original", "x", "--image", "a", ok=False)  # image without desc
j = rq("add", "second", "--original", "x", "--prio", "P0").split()[0]
assert "ACTION: TRIAGE_NEEDED" in rq("next")  # triage items are worked, not waited for
pasted = rq("add", "pasted", "--original", "p", "--desc", "pasted image: red button").split()[0]  # description without a file
rq("add", "empty", "--original", "", ok=False)
rq("set", i, "pending", ok=False)  # untriaged cannot start
fill(i)
fill(j, run="false")
rq("set", i, "pending")
rq("set", j, "pending")
assert "ACTION: WORK Q-002" in rq("next", "--start")  # P0 first, started
rq("set", i, "in_progress", ok=False)  # one at a time
rq("set", j, "completed", ok=False)  # no evidence
rq("set", j, "implemented")
assert "PASS" in rq("check", j)  # a pipe inside fast: survives
out = rq("verify", j, ok=False)
assert "FAIL" in out and "REOPENED Q-002" in out, out  # failing verify reopens the item
rq("set", j, "completed", ok=False)  # completed only from implemented
rq("set", j, "needs_approval", ok=False)  # needs a decision
rq("set", j, "in_progress")
rq("set", j, "implemented")
rq("evidence", j, "A1", "screenshot x.png")
rq("set", j, "completed")
assert "Q-002" not in rq("list")
assert "ACTION: WORK Q-001" in rq("next", "--start")
rq("set", i, "implemented")
assert "ACTION: VERIFY" in rq("next")
assert "COMPLETED Q-001" in rq("verify")
rq("set", pasted, "superseded", "reason=test")
assert "ACTION: CLOSE" in rq("next")
st = json.loads(rq("status", "--json"))
assert st["open"] == 1 and st["action"] == "CLOSE", st
assert "Q-001" in rq("stats")
rq("close", "--carry", "Q-999", ok=False)
assert rq("lint").strip() == "ok"
assert "archived" in rq("close")
assert not os.path.exists(os.path.join(tmp, "agent", "revision-queue.md"))
assert rq("init", "t2") and "Q-004" == rq("add", "n", "--original", "see Q-099 here").split()[0]

# gc: dry run lists stale private files, --apply deletes them, the live queue is protected
old = os.path.join(tmp, "agent", "stale-notes.txt")
wr(old, "x")
os.utime(old, (1, 1))
assert "stale-notes.txt" in rq("gc") and os.path.exists(old)
assert "revision-queue.md" not in rq("gc", "--apply") and not os.path.exists(old)
assert os.path.isfile(os.path.join(tmp, "agent", "revision-queue.md"))

# ---- automations: inbox, refs, consumers, gate, batched playwright (own git repo) ----
repo = tempfile.mkdtemp()
env2 = {**env, "RQ_DIR": os.path.join(repo, ".git", "agent"), "RQ_PW": f"python3 {repo}/fakepw.py"}


def rq2(*a, ok=True, inp=None):
    r = subprocess.run([sys.executable, RQ, *a], cwd=repo, env=env2, capture_output=True, text=True, input=inp)
    assert (r.returncode == 0) == ok, f"rq2 {a} rc={r.returncode}\n{r.stdout}{r.stderr}"
    return r.stdout + r.stderr


def g(*a):
    subprocess.run(("git",) + a, cwd=repo, check=True, capture_output=True)


def w(name, text):
    p = os.path.join(repo, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    wr(p, text)


def detail(i):
    return rd(os.path.join(repo, ".git", "agent", "revision-queue", i + ".md"))


g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
w("ui/card.component.ts", "@Component({selector: 'ui-card'}) export class CardComponent {}")
w("ui/page.component.ts", "// uses ui-card\n")
w("ui/page.component.spec.ts", "// spec\n")
w("app/billing.py", "def compute_total(items):\n    return sum(items)\n")
w("app/main.py", "from billing import compute_total\n")
w("tests/test_main.py", "# covers main\n")
w("docs/mockup-old.html", "x"); w("design/mockup-new.html", "y")
os.utime(os.path.join(repo, "docs/mockup-old.html"), (1, 1))
g("add", "-A"); g("commit", "-qm", "init")
w(".gitignore", "dist/\n"); w("dist/mockup-zzz.html", "z")
w("fakepw.py", "import json,os,sys\nf=[a for a in sys.argv if a.endswith('.ts')]\n"
  "st=lambda x:'skipped' if 'skip' in x else 'unexpected' if 'bad' in x else 'expected'\n"
  "json.dump({'suites':[{'file':x,'specs':[{'ok':st(x)!='unexpected','tests':[{'status':st(x)}]}]} for x in f]},open(os.environ['PLAYWRIGHT_JSON_OUTPUT_NAME'],'w'))\n")

rq2("init", "auto")
assert rq2("inbox", "--add", inp="item one ✓").strip() == "1"
assert "INTAKE" in rq2("next") and json.loads(rq2("status", "--json"))["open"] == 1
rq2("close", ok=False)  # unconverted inbox blocks close
a1 = rq2("add", "from inbox", "--inbox", "1").split()[0]
assert "item one ✓" in detail(a1)
rq2("inbox", "--add", inp="not a request")
rq2("inbox", "--skip", "2")
assert "#" not in rq2("inbox")

out = rq2("refs", "mockup", "--root", repo)
assert out.splitlines()[0].endswith("design/mockup-new.html") and "CHECK" in out and "dist/" not in out
rq2("refs", "mockup", "--root", repo, "--pin", a1)
assert "mockup-new.html" in detail(a1)

out = rq2("consumers", "ui/card.component.ts", "--write", a1)
assert "ui/page.component.ts  spec: ui/page.component.spec.ts" in out, out
assert "consumers: ui/page.component.ts @" in detail(a1)
out = rq2("consumers", "app/billing.py")  # symbols and test files are not Angular-specific
assert "app/main.py  spec: tests/test_main.py" in out and "compute_total" in out, out


def fill2(i, run):
    p = os.path.join(repo, ".git", "agent", "revision-queue", i + ".md")
    t = rd(p)
    t = t.replace("level: <local|shared|kit|contract|architecture>", "level: local")
    t = t.replace("(root-level fix: owner -> state/contract -> consumer -> recovery; consumers that change; what is NOT done)",
                  "Fix the owner component once; consumers inherit the change.")
    t = t.replace("- A1: <original failure no longer reproduces> | check: <how proven> | fast: <cmd> | run: <cmd> | evidence:",
                  f"- A1: renders | check: spec | run: {run} | evidence:")
    wr(p, t)


a2 = rq2("add", "second", "--original", "b").split()[0]
fill2(a1, "pw:e2e/good.spec.ts"); fill2(a2, "pw:e2e/bad.spec.ts")
for i in (a1, a2):
    rq2("set", i, "pending")
assert f"WORK {a1}" in rq2("next", "--start")
w("ui/card.component.ts", "// changed by first item\n")
assert "unfinished" in rq2("gate", "--worktree", ok=False)  # unstaged, as in `git add && git commit`
g("add", "ui/card.component.ts")
assert "unfinished" in rq2("gate", ok=False)  # staged file of an in_progress item
rq2("set", a1, "implemented")
assert rq2("gate").strip() == "ok"
rq2("set", a2, "in_progress")
w("ui/card.component.ts", "// changed again by second item\n")
g("add", "ui/card.component.ts")
assert "unfinished" in rq2("gate", ok=False)
g("reset", "-q")
rq2("set", a2, "implemented")
a3 = rq2("add", "third", "--original", "c").split()[0]
fill2(a3, "pw:e2e/skip.spec.ts")
rq2("set", a3, "in_progress"); rq2("set", a3, "implemented")
out = rq2("verify", a1, a2, a3, ok=False)
assert f"PASS {a1}" in out and f"FAIL {a2}" in out and f"FAIL {a3}" in out and out.count("playwright") <= 2, out  # skipped is not a pass
assert f"REOPENED {a2}" in out and f"COMPLETED {a1}" in out

# approval needs an answer; blocked needs a reason
a4 = rq2("add", "fourth", "--original", "d").split()[0]
fill2(a4, "true")
rq2("set", a4, "needs_approval", "decision=choose A or B")
rq2("set", a4, "pending", ok=False)
rq2("set", a4, "pending", "answer=A")
rq2("set", a4, "blocked", ok=False)
rq2("set", a4, "blocked", "reason=waiting for the mockup")
print("rq ok")
