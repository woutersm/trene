<!-- styre:begin -->
<!-- Managed by styre. Edits inside this block are overwritten; text outside it is kept. -->

# Working in this repository

An orchestrator (styre) is running a phased design through you, unattended. Nobody can answer
questions, so resolve ambiguity yourself and record the decision. You are given one phase at a
time; each phase is a fresh process with no memory of the last one.

## What is checked after you finish

Your own account of your work is not what decides the phase. After you stop, the orchestrator
re-runs the phase's gate commands itself, and inspects the staged diff. A phase is accepted
only if every gate command exits 0, no test was skipped or deleted, and the phase actually
changed something. Then it is committed, one commit per phase. On failure you get the output
back and try again, a limited number of times, and the run stops if you do not converge.

So there is nothing to gain by reporting success early: the gate will find it, and you will
spend a fix round on it.

## Rules

- Implement only the phase you were given. Later phases are context, not work for now.
- Keep it simple. Write the plainest thing that meets the criteria and passes the gate. No
  configuration knob with one sensible value, no abstraction with one caller, no framework
  where a function will do, no option nobody asked for. A design that does not call for
  something is a design that does not want it.
- Search before creating: confirm a thing does not already exist, and edit it rather than
  adding a parallel version.
- No placeholders: no TODO, no stubs, no hard-coded results standing in for logic.
- When the phase lists tests, write them first, from that list, and watch them fail before
  you write the code they cover. A test that passes before its implementation exists is not
  testing what you think it is; fix the test. The list is the specification — never narrow it
  to what your implementation happens to do.
- Never skip, delete or weaken a test to make it pass; fix the code. Added skip/xfail markers
  and removed tests are rejected automatically.
- Keep a .gitignore for build artifacts.
- Do not run `git commit`, `git checkout`, `git reset` or any other command that moves the
  branch. The orchestrator owns the git history.
- Read what earlier phases wrote in the working tree rather than a description of it. The
  notes you are handed deliberately do not inventory the repository.
- **Read narrowly, and read files with `read`.** A file you read stays in your context for
  the rest of the phase, and reading is the largest thing in it: on a measured run, reading
  files was 62% of all command output, and a third of it was a file read again. Find the
  lines first with `grep -n`, then `read` the file with `offset` and `limit` around them. Do
  not `cat` or `sed` a file through `bash`: a command's output keeps only its first 60 and
  last 90 lines, so a long file comes back as its two ends and you end up reading it again
  in pieces. Pipe a command's own output through `head`, `tail` or `grep` rather than
  printing a whole test log. Every token you add makes each later turn slower to start.
- **Never read anything under `.styre/`.** It is the orchestrator's working directory, not
  yours: run logs, timelines, prompts and other phases' files. Nothing in it helps you build
  this phase, and reading it spends the context you need for the work. Write exactly the
  files named below there, and nothing else — no listing it, no looking for another phase's
  state file to copy. The formats you need are below, in full.
- **Tool output is capped.** A `read` returns at most 300 lines and a command's output its
  first 60 and last 90; a note says what was left out and how to get it.

## Your three styre tools

- **`styre_gate`** runs the phase's gate: exactly the commands the orchestrator runs after you
  stop, in the same shell with the same timeout. It returns whether it passed, each command's
  exit code, and the last lines of each failure. Use it rather than running the gate commands
  yourself; run a single test file with bash while you work on it.
- **`styre_progress`** (`done`, `now`, `next`, `learned`) overwrites `.styre/progress.md`.
  Call it after every step that finishes something — a file written, a group of tests
  passing, a gate command green. If your process is restarted, or a fix round starts after
  you stop, this file and the working tree are all the next process gets. At most 40 lines;
  `learned` is for facts the tree does not show — a command that works, a trap, a decision.
- **`styre_finish`** (`status`, `reason`, `unmet_criteria`) records the phase's result, last.

## What you write when you finish

| What | Lifetime | Who reads it |
| --- | --- | --- |
| `docs/decisions.md` | permanent, append-only, committed with the code | humans, later |
| `.styre/notes-next.md` | one phase; overwrite it, never append | the next phase only |
| `styre_finish`, called last | one phase | the orchestrator |

`docs/decisions.md` is for choices a reader could not infer from the code: ambiguities you
resolved, options you rejected and why, constraints you discovered. Not a list of the files
you created — the repository already shows that.

**Append to it without reading it.** It is append-only and nothing in it is addressed to
you, so open it only to add your section at the end — never to read what earlier phases
wrote. On a measured run, reading this file was the single largest tool result of the whole
run, ~14,000 tokens in one call, and it grows with every phase that has ever appended.

**No later phase reads it.** It is committed for a person going through this months from
now, so write the two or three things that would save them an hour, and nothing else. A
summary of what you built, a count of passing tests, or a report that the gate went green
is wasted here: the gate is what proves that, and it has already run. If the *next* phase
needs to know something, that belongs in `.styre/notes-next.md`, which it does read.

`styre_finish` takes `status` `DONE` or `BLOCKED`, a one-line `reason` (required when
BLOCKED) and `unmet_criteria`, the exact text of each exit criterion you could not fully meet.

`DONE` means every exit criterion is met and the whole gate passes: the tool refuses it
unless your last `styre_gate` run passed and `unmet_criteria` is empty. A
criterion you worked around, partly met, or could not verify is unmet; so is one you are
unsure about. Recording that honestly is how the design gets fixed, and costs you nothing.
Claiming a criterion you did not verify is the one outcome that wastes the whole run.

`styre_finish` counts your red `styre_gate` runs itself. They are not held against you;
they show which criteria are hard to hit.
<!-- styre:end -->
