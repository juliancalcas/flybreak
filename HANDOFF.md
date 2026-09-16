# Handoff: FlyBreak split into its own repo

FlyBreak used to live inside `forja-de-libreas` (the DCS livery generator's
repo), alongside `liveries/`. Per explicit direction, it's now its own
repo, `juliancalcas/flybreak`, with `flybreak/`'s real commit history
extracted via `git subtree split` (not squashed -- `git log` here goes
back to the original scaffold commit, same authorship, same messages).

Two independent Claude Code sessions did this restructuring in parallel
(one local, one cloud) and landed slightly different but equally valid
layouts; the version on `main` now is the flattened one (no nested
`flybreak/` package folder -- the repo root itself is the package, so
imports are `from engine.network import ...` not
`from flybreak.engine.network import ...`, and the entry point is
`python __main__.py` / `python -m engine.server` etc., not
`python -m flybreak`). Confirmed working: `python -m pytest .` from the
repo root, 7 passed, 4 skipped (the 4 need the real connectome data, not
fetched on a fresh checkout until `fetch_connectome` runs).

**Immediate local task:** set up a working checkout at
`C:\Users\Julian\Documents\Claude\Main\FLY`. From a terminal there:

```
git clone https://github.com/juliancalcas/flybreak "C:\Users\Julian\Documents\Claude\Main\FLY"
cd "C:\Users\Julian\Documents\Claude\Main\FLY"
git config core.hooksPath githooks
pip install -r requirements.txt
python -m engine.fetch_connectome   # once, ~50 MB
python __main__.py                   # launches engine + frontend, opens the browser
```

If any of this hits the Windows "Deletion of directory ... failed (y/n)"
loop, see `.claude/skills/recover-stuck-git-pull/` -- already known and
documented, not a new problem.

**Ownership going forward:** the local session is scoped to `DCS`
(`forja-de-libreas`/`liveries/`) only from here on and will not touch this
repo again. This cloud session owns FlyBreak's continued work -- no more
parallel-session risk on this repo specifically.

**Carried over from `forja-de-libreas`:** two general-purpose skills
(`.claude/skills/recover-stuck-git-pull/`,
`.claude/skills/remote-session-has-no-local-access/`) and the
`githooks/`/`HANDOFF.md` two-machine convention, now pointed at this
repo's own remote instead of the shared one. Not carried over: the
`liveries/`-specific role and task skills -- none apply here.

**Still open, unchanged from before the split:**

1. `fly-connectome-template` (Mert Cobanov) was never vendored --
   `frontend/index.html` is a from-scratch self-contained page. Its
   source-available license needs checking and crediting if it's ever
   pulled in.
2. The real long-term goal: an embodied fly in a calm environment with
   food/water sources and other flies, never a fear stimulus. See this
   README's "Where this is headed" for the proposed smallest first step
   (wiring `OLF_ORN_FOOD`, a real sensory population already in the
   connectome data, to a single static food source -- not a world engine
   yet).

Delete this file once the local checkout is confirmed working and the two
open items above have either been picked up or explicitly deferred again.
