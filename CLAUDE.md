# CLAUDE.md

## Coding Standards & Conventions

Read @.maister/docs/INDEX.md before starting any task. It indexes the project's coding standards and conventions:
- Coding standards organized by domain (frontend, backend, testing, etc.)
- Project vision, tech stack, and architecture decisions

Follow standards in `.maister/docs/standards/` when writing code — they represent team decisions. If standards conflict with the task, ask the user.

### Standards Evolution

When you notice recurring patterns, fixes, or conventions during implementation that aren't yet captured in standards — suggest adding them. Examples:
- A bug fix reveals a pattern that should be standardized (e.g., "always validate X before Y")
- PR review feedback identifies a convention the team wants enforced
- The same type of fix is needed across multiple files
- A new library/pattern is adopted that should be documented

When this happens, briefly suggest the standard to the user. If approved, invoke `/maister:standards-update` with the identified pattern.

## Maister Workflows

This project uses the maister plugin for structured development workflows. When any `/maister:*` command is invoked, execute it via the Skill tool immediately — do not skip workflows for "straightforward" tasks. The user chose the workflow intentionally; complexity assessment is the workflow's job.

## Verifying Changes

Every change must be verified before it is committed:

1. Run the quality gates: `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`.
2. Capture screenshots with `python tools/capture_screenshots.py --output screenshots` (add `--page /path` for new or changed pages that are not captured by default). Open the PNGs of the pages you touched and confirm the result looks right on desktop and mobile. Screenshots use fictitious seed data only and are never committed (`screenshots/` is ignored).

## GitHub Actions (`.github/workflows/claude.yml`)

When running from an issue labelled `claude`:

- Implement the issue on the branch prepared by the action, following the standards above, and run the verification steps before every commit.
- When the work is done, open a pull request with `gh pr create` against `main`. The body must link the issue (`Closes #<number>`), summarise the change, list the verification results (pytest summary line, ruff), and name the pages checked on screenshots.
- The `CI` workflow re-runs the checks on the PR and posts the screenshots as a PR comment; do not commit screenshots to the branch.
- Never put real medical or personal data in code, tests, screenshots, issues or PR text.
