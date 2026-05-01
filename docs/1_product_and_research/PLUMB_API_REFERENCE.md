# Plumb API Reference

Quick reference for integrating plumb into other projects.

## Main Entry Point

### `run()`

Creates a run factory usable as a **decorator or context manager** (sync + async).

```python
from plumb import run, SpanKind, ScorerKind

# Signature
def run(
    *,
    task_id: str,
    kind: str | RunKind = "online",  # "online" or "offline"
    parent_run_id: str | None = None,
    orchestrator_model: str | None = None,
    sub_agent_model: str | None = None,
    prompt_version: str | None = None,
    tool_schema_version: str | None = None,
    git_sha: str | None = None,
) -> _RunFactory
```

#### Usage Examples

**Context manager (sync):**
```python
with run(task_id="eval") as r:
    r.add_span(SpanKind.LLM, "generate")
    r.add_score("accuracy", ScorerKind.DETERMINISTIC, value_numeric=0.95)
```

**Context manager (async):**
```python
async with run(task_id="eval") as r:
    r.add_span(SpanKind.LLM, "generate")
```

**Decorator (sync):**
```python
@run(task_id="eval")
def my_function():
    pass
```

**Decorator (async):**
```python
@run(task_id="eval")
async def my_async_function():
    pass
```

**Nested runs (automatic parent tracking):**
```python
with run(task_id="orchestrator", kind="online") as parent:
    with run(task_id="sub_task", kind="offline") as child:
        # child automatically has parent.run_id as parent_run_id
        pass
```

---

## RunHandle Methods

The handle yielded by `with run(...) as r:` provides these methods:

### Properties (Read-Only)

```python
r.run_id          # str: unique 32-char hex ID
r.task_id         # str: the task_id from run()
r.parent_run_id   # str | None: parent run ID (auto-detected or explicit)
```

### `add_span()`

Buffer a span (unit of work).

```python
def add_span(
    kind: SpanKind | str,
    name: str,
    *,
    parent_span_id: str | None = None,
    input_hash: str | None = None,        # 64-char lowercase hex (SHA256)
    output_hash: str | None = None,       # 64-char lowercase hex (SHA256)
    tokens: tuple[int, int] | None = None,  # (tokens_in, tokens_out)
    latency_ms: float | None = None,
    status: SpanStatus | str | None = None,  # default None
    error_type: str | None = None,
) -> str  # returns span_id
```

**SpanKind values:** `"llm"`, `"tool"`, `"subagent"`, `"handoff"`, `"plan"`, `"verify"`

**SpanStatus values:** `"success"`, `"failure"`, `"aborted"`

### `add_score()`

Buffer a score (metric).

```python
def add_score(
    metric_name: str,
    scorer: ScorerKind | str,
    *,
    value_numeric: float | None = None,  # XOR: set one only
    value_label: str | None = None,      # XOR: set one only
    span_id: str | None = None,          # Optional: score a specific span
    scorer_version: str | None = None,   # default: "unversioned"
) -> str  # returns score_id
```

**ScorerKind values:** `"deterministic"`, `"judge"`, `"human"`, `"user_signal"`

**Validation:** Exactly one of `value_numeric` or `value_label` must be set.

### `set_models()`

Late-bind model information (last call wins).

```python
def set_models(
    *,
    orchestrator_model: str | None = None,
    sub_agent_model: str | None = None,
) -> None
```

### `abort()`

Mark the run as aborted. Future `add_*()` calls become no-ops, but buffered spans are preserved.

```python
def abort(reason: str) -> None
```

**Validation:** `reason` must be non-empty.

---

## Enums

### RunKind
```python
RunKind.OFFLINE   # "offline"
RunKind.ONLINE    # "online"
```

### RunStatus
```python
RunStatus.PENDING    # "pending"
RunStatus.SUCCESS    # "success"
RunStatus.FAILURE    # "failure"
RunStatus.ABORTED    # "aborted"
RunStatus.STALLED    # "stalled"
```

### SpanKind
```python
SpanKind.LLM       # "llm"
SpanKind.TOOL      # "tool"
SpanKind.SUBAGENT  # "subagent"
SpanKind.HANDOFF   # "handoff"
SpanKind.PLAN      # "plan"
SpanKind.VERIFY    # "verify"
```

### SpanStatus
```python
SpanStatus.SUCCESS   # "success"
SpanStatus.FAILURE   # "failure"
SpanStatus.ABORTED   # "aborted"
```

### ScorerKind
```python
ScorerKind.DETERMINISTIC  # "deterministic"
ScorerKind.JUDGE          # "judge"
ScorerKind.HUMAN          # "human"
ScorerKind.USER_SIGNAL    # "user_signal"
```

---

## Data Entities

### Run
```python
@dataclass(frozen=True)
class Run:
    run_id: str
    task_id: str
    kind: RunKind
    status: RunStatus
    start_ts: datetime  # UTC, timezone-aware
    end_ts: datetime | None = None
    parent_run_id: str | None = None
    orchestrator_model: str | None = None
    sub_agent_model: str | None = None
    prompt_version: str | None = None
    tool_schema_version: str | None = None
    git_sha: str | None = None
    error_type: str | None = None
```

### Span
```python
@dataclass(frozen=True)
class Span:
    span_id: str
    run_id: str
    kind: SpanKind
    name: str
    parent_span_id: str | None = None
    status: SpanStatus | None = None
    input_hash: str | None = None        # 64-char hex
    output_hash: str | None = None       # 64-char hex
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error_type: str | None = None
```

### Score
```python
@dataclass(frozen=True)
class Score:
    score_id: str
    run_id: str
    metric_name: str
    scorer: ScorerKind
    scorer_version: str
    scored_at: datetime  # UTC, timezone-aware
    span_id: str | None = None
    value_numeric: float | None = None
    value_label: str | None = None
    rationale: str | None = None
```

---

## Error Handling

Plumb **never raises storage errors into user code**. If the database fails to write:

- The error is logged with context (`plumb_internal_error: true`, `run_id`, error class name).
- The run context manager does **not** suppress user exceptions (FR-EDGE-1).
- The context manager completes normally.

**User code is responsible for:** validating inputs (non-empty strings, valid enums, ID formats).

---

## Common Patterns

### Capturing LLM Calls

```python
with run(task_id="inference", orchestrator_model="claude-3-5-sonnet-20241022") as r:
    span_id = r.add_span(
        SpanKind.LLM,
        "chat_completion",
        tokens=(100, 250),
        latency_ms=1234.5,
        status=SpanStatus.SUCCESS,
    )
    r.add_score(
        "latency",
        ScorerKind.DETERMINISTIC,
        value_numeric=1234.5,
        span_id=span_id,
    )
```

### Evaluation with Nested Agents

```python
@run(task_id="orchestrator", kind="online")
async def orchestrate():
    @run(task_id="sub_task", kind="offline")
    async def sub_agent_call():
        # This run automatically has orchestrate's run_id as parent
        pass
    
    await sub_agent_call()
```

### Recording Examples

Examples are written directly to the storage adapter (not via RunHandle):

```python
from plumb.core.entities import Example, ExampleSource
from datetime import datetime, UTC
import hashlib

example = Example(
    example_id="<32-char-hex>",
    task_id="eval",
    inputs_hash=hashlib.sha256(b"input").hexdigest(),
    expected_output_hash=hashlib.sha256(b"output").hexdigest(),
    source=ExampleSource.PRODUCTION_PROMOTION,
    created_at=datetime.now(tz=UTC),
)
```

---

## ID Formats

- **run_id, span_id, score_id, example_id:** 32-char lowercase hex (128-bit)
- **input_hash, output_hash, inputs_hash, expected_output_hash:** 64-char lowercase hex (SHA256)

IDs are auto-generated; you rarely need to construct them.

---

## Configuration

Plumb uses the `plumb.config.Settings` (pydantic-settings):

```python
from plumb.config import get_settings

settings = get_settings()
# Environment variables:
# - PLUMB_DATA_DIR: SQLite DB and blob store location (default: ~/.plumb)
# - PLUMB_JUDGE_ADAPTER: judge adapter to use (default: "anthropic")
```

---

## Imports

```python
from plumb import (
    run,                    # Main entry point
    RunKind,               # Enum
    RunStatus,             # Enum
    SpanKind,              # Enum
    SpanStatus,            # Enum
    ScorerKind,            # Enum
)
from plumb.core.entities import (
    Run, Span, Score, Example, ExampleSource,
)
```
