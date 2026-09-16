---
name: remote-session-has-no-local-access
description: Use immediately when a cloud/remote Claude Code session is asked to create, move, rename, or otherwise touch a folder or file on the user's own computer -- e.g. "move this folder", "put it at C:\...", "create a folder for me" -- before attempting anything or explaining the constraint more than once.
---

# A remote session cannot touch the user's local filesystem

## Overview

A Claude Code session running in a managed cloud/remote environment (the
kind described in this session's own system prompt as "an isolated,
ephemeral container") has **zero access** to the user's actual computer.
Its `Bash`/file tools only reach its own container's disk. There is no
bridge, no remote-execution tool, no "give me access" that fixes this --
it is a property of how the session was started, not a permission that
can be granted mid-conversation.

A **local** Claude Code session (one the user starts themselves by
opening a terminal on their own machine and running `claude`) is a
completely different thing: a real process running on their computer,
with real access to their files. If the user mentions "my other window"
already creating folders or moving files for them, that other window
almost certainly *is* a local session -- point them there, don't try to
become it.

## Recognize it immediately

Signs this situation has come up:
- The user asks to create/move/rename a folder, open a file, or run
  anything at a path like `C:\Users\...` or `/Users/...` that is clearly
  their machine, not this session's own working directory
- The user says some variant of "just do it", "you did this yourself
  before", or references another window/session that already has file
  access
- The user asks how to "give you access" or "change the session" to get
  local access

## What to do

1. **State the constraint once, plainly, with the concrete reason**
   ("this session's container has no path to your PC" -- not "I'm not
   allowed to"), and give the actual fix in the same message: either use
   the local session they already have, or start one (`cd` to the target
   folder on their machine, run `claude`).
2. **Do not repeat the full explanation on every follow-up.** If they
   push back or ask again, a one-line reference back ("same limit as
   before, it doesn't change with rephrasing") is enough -- repeating the
   whole rationale reads as stonewalling, not as help.
3. **Make the manual path as low-friction as possible instead of
   dwelling on the "no".** One paste-once, semicolon-chained command
   block beats several short ones the user has to run in sequence.
4. **If they have a local session, actively redirect to it** rather than
   continuing to relay commands through this one -- it is strictly faster
   for anything touching their filesystem.

## Common mistakes

- Re-explaining the same limitation at length every time it comes up,
  instead of a short reference back to what was already said.
- Offering to "find another way" that quietly still requires local
  filesystem access (a GUI tool, a different terminal invocation, "let me
  try something") -- if it needs to touch their disk, it needs a local
  session, full stop.
- Suggesting the user expose SSH/RDP or similar remote access to their
  own machine just to work around this -- disproportionate security
  exposure for what a local Claude Code session already solves for free.
