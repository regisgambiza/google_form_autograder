# Judge JSON Extraction Issues

## Problem Summary

The grading system uses AI judges (LLMs via Ollama) to evaluate student answers. The judges must return valid JSON responses, but we've encountered several issues:

## Issue 1: Empty Responses from Ollama

**Symptom:**
```
[WARNING] Judge factual_judge attempt 1/3 failed: 
[WARNING]   Content received: ''
```

**Root Cause:**
- Ollama API returning empty responses when prompts are large
- Models may be timing out or hitting internal limits
- The `format` parameter (JSON Schema) doesn't prevent this

**Current Fix:**
- Increased timeout from 120s to 180s
- Added check for empty content before JSON parsing
- Proper retry logic with continue on empty response

## Issue 2: Out-of-Range Numeric Values

**Symptom:**
Some models (e.g., llama3.1:8b) return JSON where numeric fields are outside the expected [0.0, 1.0] range:

```json
{
  "semantic_similarity": 1,
  "concept_coverage": 0.9,
  "confidence": 10,    // WRONG - should be 0.0-1.0
  "decision": "YES"
}
```

**Root Cause:**
- Ollama's `format` parameter provides SCHEMA VALIDATION at the API level but doesn't ALWAYS enforce constraints
- Models sometimes ignore the `minimum`/`maximum` constraints in the JSON Schema
- The model may understand the format but not the value range

**Current Fix:**
- `_fill_judge_defaults()` clamps all numeric fields to [0.0, 1.0]
- Config updated to use gemma3:12b for factual_judge (produces correct format)

## Issue 3: Qwen3 "Thinking" Mode Bleed

**Symptom:**
Some models (Qwen3 family) return responses with `<think>...</think>` blocks embedded in the JSON:

```json
{
  "semantic_similarity": 0.8,
  "reasoning": "<tool_call>
  ...thinking...
  </think>"
}
```

**Root Cause:**
- Qwen3 has a "thinking" mode that produces hidden chain-of-thought blocks
- These blocks can bleed into the JSON output when using structured output

**Current Fix:**
- `_extract_json_object()` strips `<think>...</think>` blocks
- 5-level fallback extraction system:
  1. JSON in code blocks (```json ... ```)
  2. Full string parse
  3. JSON at start of string
  4. JSON after newlines
  5. Balanced brace counting
  6. Regex matching

## Current Architecture

```
Ollama API (with format=JSON_SCHEMA)
    │
    ├─► Check for empty response → Retry if empty
    │
    ├─► Strip <think> blocks
    │
    ├─► Try parse as JSON
    │
    ├─► Fallback extraction strategies (5 levels)
    │
    ├─► Validate required fields
    │
    ├─► Normalize decision values
    │
    ├─► Clamp numeric values to [0.0, 1.0]
    │
    └─► Return valid judge result
```

## Configuration

**Models:**
- llama3.1:8b - semantic, concept, strict, misconception, language judges
- gemma3:12b - factual judge (preferred for correct format)
- deepseek-r1:8b - reasoning fallback
- mxbai-embed-large - embeddings

**Context Windows:**
- judge_num_ctx: 4096
- rubric_num_ctx: 1024
- fallback_num_ctx: 4096

## Next Steps for Improvement

1. Monitor which judges return empty responses
2. Consider reducing prompt size (shorter rubrics)
3. Test with smaller context windows if GPU memory is tight
4. Add metrics to track judge success rates
