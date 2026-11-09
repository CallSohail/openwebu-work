---
name: coding-agent-handoff
description: Decide when and how to hand a task to a coding agent installed on the user's computer (Claude Code, Codex, Cursor, Gemini CLI, OpenCode, Cline, Grok, Pi), which Open Terminal exposes in the Open WebUI model picker through Tether. Covers writing the brief, scoping the folder, reviewing the diff, and cleaning up after the agent.
---

# Coding agent handoff

Open Terminal can drive coding-agent CLIs that are installed and signed in on the user's machine. Through Tether they appear as models in the Open WebUI picker; the agent's own login stays on the computer.

## When to hand off

Hand off multi-file code changes, refactors, test writing, and repository work where the agent's own tools (diffs, test runners, repo search) beat shell commands. Keep it in the main chat for one-off scripts, document and data tasks, and anything that is mostly reading.

Check the agent exists first: `claude --version`, `codex --version`, `gemini --version`, `opencode --version`. If the agent is installed but not found from Tether, it may live outside the PATH Tether sees; tell the user the install folder rather than editing their PATH.

## Write the brief

Give the agent a self-contained brief, because it does not see this chat:
- Repository path and the branch to work on (create a new branch, never work on main).
- Goal in one sentence, acceptance criteria as a checklist, and the test command.
- Constraints: files or folders not to touch, no new dependencies without asking, no network calls in tests, no push.
- Cleanup: "Put throwaway scripts in .scratch/, delete them before you finish, and list every file you created or changed."

## Review

After the agent finishes, back in the main chat:
1. `git status` and `git diff --stat` on the branch.
2. Read the diff for scope creep, secrets, disabled tests and deleted files.
3. Run the tests yourself.
4. Check for leftovers: untracked files (`git status --porcelain | grep '^??'`), debug prints, `*.bak`, stray notebooks, `.scratch`. Remove leftovers the agent created; leave untracked files that existed before.
5. Summarise for the user and let them decide on commit and push.

## Limits

- Agent sessions disconnected for over an hour are closed by Open Terminal's cleanup. Long jobs should save progress notes in the repo.
- Never pass the user's API keys or tokens in the brief. If the agent needs credentials it should already have them from its own login.
