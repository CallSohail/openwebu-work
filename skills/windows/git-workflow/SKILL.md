---
name: git-workflow
description: Safe Git and GitHub work on the user's PC, including cloning, branches, commits, diffs, reviewing changes, resolving conflicts, and pushing or opening pull requests with git and gh. Use whenever the user mentions git, a repository, commit, branch, push, pull, merge, clone, pull request or GitHub.
---

# Git Workflow

Follow `windows-safe-terminal` as well. Clone and create repositories inside `C:\Users\Pret\Desktop\Tether` unless the user names another folder.

## Look before you change anything

Start every git task with:
- `git status` to see the branch and the changes
- `git log --oneline -5` to see recent commits
- `git remote -v` to see where it pushes

Tell the user the branch and whether there are uncommitted changes before doing anything else.

## Allowed without asking

- `clone`, `status`, `log`, `diff`, `show`, `branch`, `fetch`
- Creating a new branch, staging files, and committing **on a new branch** with a clear message
- Running the project's tests and linters

## Ask first

- `git push`. Show the branch and the commits that will be pushed.
- Committing directly on `main` or `master`.
- `merge`, `rebase`, `cherry-pick`, `stash drop`.
- `gh pr create`, `gh issue create` and comments. Show the title and body first.
- Anything that deletes: `git clean`, `git branch -D`, `git checkout -- <file>`, `git restore`, the Git panel's Discard. Explain that discarding removes changes and new files permanently.

## Never

- `git push --force` to `main` or `master` or to someone else's branch, `git reset --hard` on shared branches, or rewriting published history, unless the user explicitly asks and confirms after a warning.
- Committing secrets. Check `git diff --cached` for keys, passwords, tokens and `.env` files before every commit, and add them to `.gitignore` instead.
- Changing global git config (`git config --global`) or credentials without asking.

## Commit messages

- First line: what changed, in the imperative, under 72 characters ("Add login page", "Fix date parsing").
- Blank line, then why, if it isn't obvious.

## Conflicts

1. Show which files conflict.
2. Show both sides of each conflict and propose a resolution.
3. Apply it only after the user agrees, then run the tests, then commit.

## GitHub CLI

- Check `gh auth status` first. If not signed in, tell the user to run `gh auth login` themselves. Never ask for their token in the chat.
- Before creating a pull request, push the branch (after confirmation) and show the diff summary.

## Reporting

After git work, show the branch, the last commit (`git log --oneline -1`) and whether the branch is ahead of or behind its remote. The user can also review changes in Open WebUI's Git panel.
