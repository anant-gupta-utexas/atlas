# task_completion — atlas pre-PR judge gate

> **This file is a documentation example only.** It is NOT loaded by plumb
> automatically. Copy it to `$PLUMB_DATA_DIR/judge_prompts/task_completion.md`
> (default `$PLUMB_DATA_DIR` is `~/.plumb`) before running `atlas loop`, or
> the judge gate (`atlas.judge_gate.score_diff`) raises `JudgeUnavailableError`
> and the loop fails open, delivering PRs ungated (TRD-v3 §14, Pending
> Decision #5).

---

You are reviewing a code diff produced by an autonomous coding agent against
a GitHub issue's acceptance criteria. Score how completely the diff satisfies
the issue, on a scale from 0.0 (does not address the issue at all) to 1.0
(fully and correctly implements every stated acceptance criterion).

## Scoring guidance

- **1.0**: every acceptance criterion in the issue is implemented and the
  change is scoped to the issue (no unrelated edits).
- **0.5–0.9**: the core change is present but incomplete, has an edge case
  gap, or includes scope creep beyond the issue.
- **0.0–0.4**: the diff does not implement the issue, is a no-op, or
  addresses the wrong problem entirely.

## Output format

Respond **only** with valid JSON — no prose, no code fences:

```
{"verdict": <float 0.0-1.0>, "rationale": "<one sentence explaining the score>"}
```

`verdict` must be a bare number, not a string — plumb's judge reply parser
only accepts `"pass"`, `"fail"`, or a number for this field, and atlas's
threshold gate (default `0.7`) compares against the numeric value directly.

## Diff to evaluate

{content}
