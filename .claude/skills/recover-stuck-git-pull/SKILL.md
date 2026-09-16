---
name: recover-stuck-git-pull
description: Use when `git pull`/`git fetch`/`git merge` gets stuck repeating "Deletion of directory '...' failed. Should I try again? (y/n)" on Windows, or typed commands and Ctrl+C don't seem to stop it -- a file lock (antivirus, an open File Explorer window, an editor watching the folder), not a real merge conflict.
---

# Recovering a stuck git pull on Windows

## Overview

On Windows, `git pull`/`fetch`/`merge` can get stuck repeating "Deletion of
directory 'X' failed. Should I try again? (y/n)" -- sometimes for several
different directories in sequence. This is git's own cleanup step (removing
an old pack directory, or a directory being replaced during checkout)
hitting a file-system lock: something else has a handle open on a file
inside that directory. It is not a merge conflict and it has nothing to do
with commit history -- the actual fetched data is already safely written to
disk before this prompt ever appears.

## Symptoms

- `Deletion of directory '.git/objects/XX' failed. Should I try again? (y/n)`
  repeating for different two-hex-digit subdirectories
- The same prompt on a real working-tree path (e.g. `.github/workflows`, or
  any directory involved in a rename) during `git merge`/`git pull`
- Typing a new command at the prompt (e.g. `git status`) does nothing but
  print `Sorry, I did not understand your answer. Please type 'y' or 'n'`
- Typing the letters "Ctrl+C" doesn't stop it -- because that's not what it
  is: Ctrl+C is a keyboard shortcut (hold Control, tap C), not text to type

## Fix

1. **Stop the loop.** Press and hold Ctrl, tap C. If that doesn't work
   within a few seconds, just close the terminal window outright -- the
   fetch/transfer data is already on disk, closing the window loses
   nothing that wasn't already saved.
2. **Remove the actual lock**, if you want to fix it at the source before
   retrying: close any File Explorer window showing that repo folder or a
   subfolder of it (the single most common cause on Windows), close any
   editor/IDE that has the folder open as a workspace, close any git GUI
   client (GitHub Desktop etc.) pointed at the same repo.
3. **If it keeps recurring on retry** -- a large restructuring pull with
   many renamed directories is the worst case, since almost every renamed
   folder can hit the same lock -- stop fighting individual prompts and
   clone fresh into a new sibling folder instead:
   ```
   cd ..
   git clone <same-remote-url> <new-folder-name>
   cd <new-folder-name>
   ```
   A fresh clone only ever creates files; it never deletes or renames
   anything in an existing tree, so this class of lock cannot happen
   during it.
4. **Recover any uncommitted work** from the old folder before abandoning
   it, rather than losing it: commit it there first
   (`git add -A && git commit`) so it is safe in that folder's own `.git`
   history, then copy the specific changed/new files by hand into their
   equivalent path in the fresh clone (paths may have moved if the pull
   included a restructuring -- check with `git status` in the old folder
   to see exactly which files changed), and `git add`/`commit`/`push` from
   the fresh clone. Leave the old folder and its commit alone as a safety
   net until the new clone is confirmed working end to end.

## Common mistakes

- **Typing commands at the y/n prompt.** It only parses a single `y` or `n`
  character; anything else just reprints "Sorry, I did not understand your
  answer" and re-asks the identical question -- it is not reading what you
  typed as a new command.
- **Typing "Ctrl+C" as literal text** instead of pressing the actual key
  combination.
- **Assuming it is a merge conflict.** It isn't -- once you break out,
  `git status` typically shows a clean divergence (`ahead/behind N
  commits`), not conflict markers. The prompt is purely a cleanup-step
  failure, unrelated to the merge itself.
- **Retrying indefinitely on the same directory.** If two or three `y`
  retries in a row don't clear it, the lock isn't releasing on its own --
  stop retrying and go straight to the fresh-clone path instead of
  clicking through dozens of prompts.
