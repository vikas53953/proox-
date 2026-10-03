# Automation: Claude Code GitHub Action

Owner-approved builder automation (2026-10-01). It is **not** part of the product and
does not touch gates G01–G06 (a paid API key for builder automation is separate from the
G01 decision on model access for the hosted bot).

## How it is used

- The owner, or the owner's assistant posting **as the owner's GitHub account**, opens an
  issue or PR review and writes `@claude`.
- The workflow `.github/workflows/claude.yml` runs Claude in GitHub Actions; Claude replies
  in the same thread and may push work to its **own branches** and open PRs.

## Approval rule

**Every merge needs the owner's final approval.** Claude never merges, never pushes to
`main` or `feat/*`, and never approves its own PRs. Enforce it in GitHub:
Settings → Branches → protect `main` and `feat/v1.3-scaffold`: require a pull request,
require 1 approval, block force pushes.

## Guardrails in the workflow

| Guardrail | Where |
|---|---|
| Only the repository owner's account can trigger a run | `if: github.actor == github.repository_owner` |
| The action itself refuses users without write access and all bot accounts | action default |
| Actions pinned to full commit SHAs (RC12) | `claude.yml`, BOM.md |
| Model pinned (`--model claude-opus-5-5`), runs capped (`--max-turns 30`, 30 min) | `claude.yml` |
| One run at a time per issue/PR | `concurrency` |
| API key only in GitHub Secrets (`ANTHROPIC_API_KEY`), never in code, chat or issues | repo settings |
| Workflow inactive until merged into the default branch (`main`) | GitHub behaviour |

Issue and PR text is untrusted input (RC09): the owner-only trigger is the main defence
against someone else steering a run. The repo's own rules (RC01–RC12, CLAUDE-facing docs
in this repo) still apply to anything Claude changes.

## Permissions note

The workflow's `permissions:` block limits the workflow token. The official Claude GitHub
App acts with its own installation permissions (Contents, Issues, Pull requests:
read + write). Branch protection is therefore the real merge guardrail. A custom GitHub
App with narrower permissions is possible if wanted (see the official docs).
