# Google Form Autograder - Technical Architecture

## Overview

This document describes the internal architecture and flow of the Google Form Autograder system.

## System Flow

### 1. Initialization (`main.py`)

```
1. Load config.json
2. Load forms_to_grade.json
3. Initialize Google API service (OAuth2)
4. For each form:
   a. Extract form ID
   b. Fetch form structure
   c. Get responses for each question
   d. Evaluate answers
   e. Generate feedback report
   f. Update correct answers in form
```

### 2. Answer Evaluation (`evaluation_pipeline.py`)

```
evaluate_answer(answer, expected, question):
    1. Create cache key
    2. Check cache (if enabled)
    3. Run deterministic checks
    4. Generate rubric
    5. Score concepts
    6. Run judges (if needed)
    7. Combine scores
    8. Route decision
    9. Return EvaluationResult
```

### 3. Multi-Judge Consensus (`ai_judges.py`)

```
run_judges(answer, question, expected, rubric):
    1. For each judge type:
       a. Prepare prompt + rubric
       b. Submit ProviderRequest through ProviderManager.ask(...)
       c. Parse JSON response
       d. Validate and normalize output
    2. Apply early exit if unanimous + high confidence
    3. Return list of judge results
```

### 3.1 Provider Boundary

The active grading path must route judge model calls through `provider_manager.py`.
Judges build prompts and validate verdicts; they do not choose OpenRouter,
llama.cpp, or Ollama directly.

```
global_dispatcher.py
    -> evaluation_pipeline.py
        -> ai_judges.py
            -> ProviderManager.ask(ProviderRequest)
                -> providers/openrouter_provider.py
                -> providers/llamacpp_provider.py
                -> providers/ollama_provider.py
```

Direct Ollama callers still exist for non-grading subsystems:

- `embeddings.py`: embedding model calls.
- `vision_context.py`: optional image/context extraction.
- `feedback.py`: optional generated feedback reports.
- `rubric_generator.py` and legacy evaluators: compatibility/older paths.

Those modules should not be used for normal judge grading unless the selected
application mode explicitly calls them.

**Judge Output Schema:**
```json
{
    "semantic_similarity": 0.0-1.0,
    "concept_coverage": 0.0-1.0,
    "factual_accuracy": 0.0-1.0,
    "misconception_detected": boolean,
    "misconception_description": "string",
    "language_noise_ratio": 0.0-1.0,
    "confidence": 0.0-1.0,
    "decision": "YES"|"NO"|"ABSTAIN",
    "reason_short": "string"
}
```

### 4. Score Combination (`consensus_engine.py`)

```
combine_scores(judge_scores, embedding_score, misconception_detected):
    penalty = 0.6 if misconception_detected else 1.0
    
    final_score = (
        semantic_similarity * 0.35 +
        concept_coverage * 0.25 +
        factual_accuracy * 0.2 +
        strict_judge_score * 0.1 +
        language_noise_bonus * 0.05 +
        embedding_score * 0.05
    ) * penalty
    
    return clamp(final_score, 0.0, 1.0)
```

## Data Structures

### EvaluationResult (dataclass)

```python
@dataclass
class EvaluationResult:
    answer: str
    decision: str  # YES/NO
    final_score: float  # 0.0-1.0
    semantic_score: float
    concept_score: float
    factual_score: float
    misconception_detected: bool
    misconception_description: str
    missing_concepts: List[str]
    accepted_concepts: List[str]
    model_agreement: float  # Judge agreement %)
    confidence: float  # 0.0-1.0
    fast_path_used: bool
    latency_ms: float
    stage_reached: str  # deterministic|embedding|jury|reasoning
```

### DeterministicResult (dataclass)

```python
@dataclass
class DeterministicResult:
    accepted: bool
    confidence: float
    method: str  # exact_normalized|numeric_equivalence|algebraic_equivalence|...
```

## Caching Strategy

```
Cache Key Format:
  Hash = SHA256(normalized_answer + ":" + SHA256(question + ":" + expected))
  Cache Path: cache/results/{hash}.json

Rubric Cache:
  Cache Key = SHA256(question + "||" + expected)
  Cache Path: cache/rubrics/{hash}.json
```

## Early Exit Logic

```
Early exit is triggered when:
  - At least min_judges (default: 3) have completed
  - All completed judges agree (same decision)
  - Average confidence ≥ agreement_confidence (default: 0.9)

This allows grading to complete in ~6-10 seconds instead of 2+ minutes.
```

## Error Handling

### Judge Failure Pattern

```
For each judge attempt (max 3):
  1. Try structured output format
  2. If empty/invalid, try fallback JSON extraction
  3. If still failing, log detailed error
  4. Return ABSTAIN after 3 failures
```

### Timeout Handling

```
Each judge has 30-second max latency.
If exceeded:
  - Current evaluation continues
  - Warning logged
  - May affect final confidence score
```

## Decision Routing

```
final_score >= 0.92  → YES (auto_accept)
final_score < 0.35   → NO (auto_reject)
otherwise            → Reasoning fallback
```

### Reasoning Fallback Flow

```
1. Send judge scores + answer + question to reasoning model
2. Model provides decision + confidence + reason
3. Override auto-accept/auto-reject with model decision
```

## Performance Optimization

1. **Caching**: Skip LLM calls for seen answers
2. **Deterministic Pre-filter**: Fast rule-based matches
3. **Early Exit**: Stop after 3 unanimous judges
4. **Embedding Thresholds**: Auto-accept/reject before judges
5. **Parallel Processing**: Judge calls run concurrently (async)

## Provider Strategies

`provider_strategy` selects routing. All strategies are failover chains except
`dual_lane`, which is the only parallel mode:

- `openrouter_only`, `llamacpp_only`, `ollama_only`: single provider
- `openrouter_llamacpp`, `llamacpp_openrouter`, `all_providers`, ...:
  sequential failover in listed order
- `dual_lane`: per-provider AI worker pools (sizes from
  `openrouter_ai_worker_count` / `llamacpp_ai_worker_count`) pull question
  batches from one shared queue, so both providers grade concurrently at their
  own speed with no duplicated calls. Each call pins its lane via
  `metadata["provider_priority"]`; the other provider remains the in-request
  fallback. `effective_jury_concurrency()` auto-bumps
  `max_concurrent_jury_answers` to at least the total worker count so lanes
  never serialize on the jury semaphore.

## Failed-Answer Requeue

With `requeue_failed_answers` enabled, an answer whose grading ends in ERROR
(after judge retries) is re-scheduled by a dedicated retry scheduler thread
with exponential backoff (`requeue_base_delay_seconds * 2^attempt`) up to
`requeue_max_attempts` extra passes, instead of being silently dropped.
While requeued, the result is withheld from progress accounting and from the
per-question apply trigger, so Google Form writes only ever see final
verdicts. Exhausted attempts fall back to the legacy behavior: the answer is
logged as failed and excluded from Forms and teacher review. In non-batched
mode the deterministic workers hold their shutdown sentinel until the requeue
scheduler is empty, guaranteeing retried tasks always find a live worker.

## Logging Structure

```
[2026-05-30 00:00:00] [LEVEL] message
```

Log levels:
- `DEBUG`: Internal processing details
- `INFO`: Main workflow milestones
- `WARNING`: Retries, timeouts, partial failures
- `ERROR`: JSON extraction failures, API errors

## Ollama Structured Output

The system uses Ollama's `format` parameter to enforce JSON schema:

```python
ollama.chat(
    model="gemma3:12b",
    format={
        "type": "object",
        "properties": {
            "semantic_similarity": {"type": "number", "minimum": 0, "maximum": 1}
        },
        "required": ["semantic_similarity"]
    },
    messages=...
)
```

This guarantees the response is always valid JSON in the expected format.
