# Getting Started

## Prerequisites

- Python 3.13+
- git 2.5+ (required for `git worktree`)
- [uv](https://astral.sh/uv) (recommended) or pip
- [plumb](https://github.com/anant-gupta-utexas/plumb) installed as a
  sibling path dependency (see `pyproject.toml`)
- Agent plugins installed in your Claude Code environment:
  `DEV-ESSENTIALS`, `DEV-BE-PYTHON`

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/anant-gupta-utexas/atlas.git
cd atlas
```

### 2. Set up the Python environment

```bash
# Using uv (recommended)
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv sync
```

Or with pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Verify the install

```bash
atlas --help
```

You should see `run`, `resume`, `status`, `hook`, and the `loop` command group.

### 4. Optional — install the post-commit hook

```bash
# Run from the root of the repo you want atlas to track
atlas hook install
```

This writes `.git/hooks/post-commit`. It is idempotent and removable
via `atlas hook uninstall`.

## Running atlas

### Start a new run

```bash
atlas run "add response-cache middleware to this Flask repo"
```

Atlas creates `dev/active/<task-slug>/tasks.md`, opens Stage 0
(research), and pauses at the first gate. From there you work through
each gate interactively.

Two optional flags on `atlas run`:

```bash
atlas run "<task>" --backend codex    # dispatch through a different engine
atlas run "<task>" --telemetry        # record tokens + cost to plumb
```

`--telemetry` is off by default because it changes the dispatched argv;
without it an attended run is byte-identical to pre-loop-mode. It is
deliberately independent of the permission mode, so measuring a run never
widens what the agent is allowed to do. See
[CLI Backends](cli_backends.md) for both.

### Check pipeline state

```bash
atlas status
```

Prints the `## current` block from the active `tasks.md` — phase,
gate index, and next unchecked item. Exits non-zero if no active run.

### Resume after a session compaction

Atlas's state is entirely in `dev/active/<task>/tasks.md`. When a
Claude Code session ends (compaction, restart), the `CLAUDE.md`
instruction paragraph in this repo tells a fresh session to read
`tasks.md` and find the first unchecked box. No human re-briefing
needed.

## Configuration

Create `.atlas.toml` in your project root (or use `~/.atlas/config.toml`
for user-wide defaults):

Every key `Config.load()` actually reads, with its default:

```toml
# Where plumb's SQLite file lives.
plumb_db_path = "~/.plumb/plumb.db"

# The Claude model name. NOT portable to other engines — see [backend.models].
model = "haiku"

# Per-stage timeout overrides, keyed by stage name (seconds).
[timeout_overrides]
code_gen = 3600

# Override the slash command a plugin tool string resolves to.
[plugin_commands]
code-review = "DEV-ESSENTIALS:code-review"

[backend]
default = "claude"        # project-wide backend default

[backend.models]
codex = "gpt-5.1-codex"   # per-engine model names; unset engines use their CLI default

[loop]                    # only needed if you run `atlas loop`
repos = ["owner/repo"]
poll_interval_s = 60
max_runs_per_day = 20
max_dollars_per_day = 10.0
max_turns = 40
no_progress_limit = 3
identical_error_limit = 5
cooldown_min = 30
concurrency = 1           # any other value raises; frozen until Phase L4
# trusted_authors = ["you"]   # REQUIRED if a target repo is public or multi-author
```

Model swaps are config edits, not code changes. Two things to know:

- `model` is a **Claude** name. Handing it to another engine is a hard
  failure, not a degraded default (`codex exec --model haiku` is an HTTP
  400), which is why per-engine names live in `[backend.models]`.
- `max_dollars_per_day` only accumulates **claude**-reported spend. The
  Codex CLI reports no cost at all, so on that lane `max_runs_per_day` is
  the real bound.

## Running tests

```bash
pytest                          # all tests
pytest tests/unit               # unit tests only
pytest --cov=src --cov-report=term-missing  # with coverage
```

CI gates: `ruff check`, `ruff format`, `mypy src`. All three must pass
before merging to `main`.

```bash
ruff check .
ruff format .
mypy src/
```

## Troubleshooting

### `atlas` command not found

Ensure the virtual environment is active (`source .venv/bin/activate`)
and the package was installed with `uv sync` or `pip install -e .`.

### Virtual environment issues

```bash
deactivate
rm -rf .venv
uv venv
source .venv/bin/activate
uv sync
```

### `.atlas/current-run` mismatch error

Atlas detected that the `run_id` in `.atlas/current-run` does not match
the `run_id` in `tasks.md`. This happens when a file is manually edited
out of sync. The error message names both values — reconcile them by
editing one file to match the other, then re-run `atlas status` to
confirm.

### Post-commit hook not writing scores

Check that `atlas hook install` completed without error and that
`.git/hooks/post-commit` exists and is executable (`chmod +x`). The
hook reads `.atlas/current-run`; if no active run is present, it exits
silently.

## Next Steps

- Read [Core Concepts](core_concepts.md) for how the 7-stage pipeline
  and gate system work.
- Read [System Design](../2_architecture/system_design.md) for the full
  component breakdown and data flow.
- Read [Testing Guide](../4_testing/index.md) for test organization and
  the routing ground-truth fixture.
