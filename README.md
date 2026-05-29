# Google Form Autograder

A Python-based automated grading system that evaluates student responses to Google Forms using AI-powered semantic analysis, concept coverage scoring, and multi-judge consensus.

## Overview

This application automatically grades open-ended question responses from Google Forms by:
1. Fetching form responses from Google Forms API
2. Generating grading rubrics using AI
3. Evaluating answers through multiple AI judges
4. Combining scores with weighted consensus
5. Producing detailed grading reports

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Main Application                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Google Forms API (OAuth2)                                   │   │
│  │  - Fetch form structure                                      │   │
│  │  - Get student responses                                     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                        │
│                              ▼                                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Evaluation Pipeline (evaluation_pipeline.py)                │   │
│  │  - Deterministic pre-checks (deterministic_checks.py)        │   │
│  │  - Embedding-based semantic scoring (semantic_scoring.py)    │   │
│  │  - Rubric generation (rubric_generator.py)                   │   │
│  │  - Multi-judge consensus (ai_judges.py)                      │   │
│  │  - Final score calculation (consensus_engine.py)             │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                        │
│                              ▼                                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Ollama LLM API (Local)                                      │   │
│  │  - Semantic Judge                                              │   │
│  │  - Concept Coverage Judge                                    │   │
│  │  - Factual Accuracy Judge                                    │   │
│  │  - Strict Judge                                                │   │
│  │  - Misconception Detector                                    │   │
│  │  - Language Filter                                           │   │
│  │  - Rubric Generator                                          │   │
│  │  - Reasoning Fallback                                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Evaluation Pipeline (`evaluation_pipeline.py`)

The main grading workflow with a multi-stage pipeline:

```
Answer Input
    │
    ├─► Deterministic Checks (fast, rule-based)
    │    ├─ Exact match (normalized)
    │    ├─ Numeric equivalence (50% = 0.5)
    │    ├─ Algebraic equivalence (Sympy)
    │    ├─ Equation equivalence
    │    └─ Interval matching
    │
    ├─► Embedding-based Scoring (semantic_scoring.py)
    │    ├─ Concept coverage analysis
    │    ├─ Semantic similarity to rubric
    │    └─ Embedding similarity score
    │
    ├─► Rubric Generation (rubric_generator.py)
    │    └─ AI-generated grading criteria
    │
    ├─► Multi-Judge Consensus (ai_judges.py)
    │    ├─ Semantic Judge - Same meaning?
    │    ├─ Concept Coverage - Which concepts covered?
    │    ├─ Factual Accuracy - Correct facts?
    │    ├─ Strict Judge - Classroom standards
    │    ├─ Misconception Detection - Conceptual errors?
    │    └─ Language Filter - Language vs content errors
    │
    ├─► Score Combination (consensus_engine.py)
    │    └─ Weighted average with misconception penalty
    │
    ├─► Decision Routing (confidence_router.py)
    │    ├─ Auto-accept (≥0.92)
    │    ├─ Auto-reject (<0.35)
    │    └─ Reasoning fallback (middle range)
    │
    └─► Final Result (YES/NO with confidence score)
```

### 2. AI Judges (`ai_judges.py`)

Six specialized judges, each using a different LLM model:

| Judge | Model | Purpose |
|-------|-------|---------|
| `semantic_judge` | llama3.1:8b | Checks if answers convey same meaning |
| `concept_judge` | llama3.1:8b | Measures concept coverage percentage |
| `factual_judge` | gemma3:12b | Validates scientific/mathematical accuracy |
| `strict_judge` | gemma3:12b | Classroom standards evaluation |
| `misconception_judge` | llama3.1:8b | Detects conceptual misunderstandings |
| `language_filter` | llama3.1:8b | Separates language issues from content |

**Key Feature:** Uses Ollama's structured output (JSON Schema) to guarantee valid JSON responses.

### 3. Deterministic Checks (`deterministic_checks.py`)

Fast rule-based pre-filters that handle common cases without LLM calls:

- Exact normalized string matching
- Numeric equivalence (with tolerance)
- Percentage handling (50% = 0.5)
- Algebraic expressions (using SymPy)
- Equation equivalence
- Interval notation

### 4. Rubric Generator (`rubric_generator.py`)

Dynamically generates grading rubrics using:
- Required concepts
- Optional bonus concepts
- Acceptable paraphrases
- Critical errors to watch for
- Common misconceptions

### 5. Consensus Engine (`consensus_engine.py`)

Combines judge scores with configurable weights:
```json
{
  "semantic_similarity": 0.35,
  "concept_coverage": 0.25,
  "factual_accuracy": 0.2,
  "strict_judge": 0.1,
  "language_noise": 0.05,
  "embedding": 0.05
}
```

Applies misconception penalty (40% default reduction) when misconceptions are detected.

### 6. Confidence Router (`confidence_router.py`)

Routes decisions based on final score:
- **≥0.92**: Auto-accept
- **<0.35**: Auto-reject
- **Middle range**: Fallback reasoning with reasoning model

## Configuration (`config.json`)

```json
{
    "jury_models": {
        "semantic_judge": "llama3.1:8b",
        "concept_judge": "llama3.1:8b",
        "factual_judge": "gemma3:12b",
        "strict_judge": "gemma3:12b",
        "misconception_judge": "llama3.1:8b",
        "language_filter": "llama3.1:8b"
    },
    "rubric_model": "gemma3:12b",
    "embedding_model": "mxbai-embed-large",
    "reasoning_model": "gemma3:12b",
    "confidence_thresholds": {
        "auto_accept": 0.92,
        "auto_reject": 0.35
    },
    "early_exit": {
        "enabled": true,
        "min_judges": 3,
        "agreement_confidence": 0.9
    },
    "misconception_penalty": 0.4,
    "max_latency_per_answer_seconds": 30
}
```

## Usage

### Running the Grader

```bash
# Command-line mode
python main.py

# GUI mode (if running from desktop)
python gui_main.py
```

### Grading Modes

- **Whole Form**: Grades all responses
- **Recent Only**: Grades only recent submissions (controlled via `GRADE_RECENT_ONLY` env variable)

### Forms Configuration (`forms_to_grade.json`)

```json
{
    "forms": [
        "https://docs.google.com/forms/d/FORM_ID/viewform",
        "https://docs.google.com/forms/d/e/FORM_ID/viewform"
    ]
}
```

## Output

### Grading Report (`Feedback/`)
- Detailed per-question breakdown
- Student response analysis
- Judge scores and reasoning
- Acceptance/rejection rationale

### Results Caching
Results can be cached for faster re-processing:
```json
{
    "persist_result_cache": true
}
```

## Performance Characteristics

- **Fast Path**: ~100ms for deterministic matches
- **Embedding Scoring**: ~1-3 seconds
- **Judges**: ~2-3 minutes (6 judges × 30s max per judge)
- **Reasoning Fallback**: ~1-2 minutes

Early exit option allows grading to complete after 3 unanimous judges.

## Requirements

- Python 3.11+
- Ollama (local LLM server)
- Google Cloud project with Forms API
- Ollama models:
  - `llama3.1:8b`
  - `gemma3:12b`
  - `mxbai-embed-large`

## Error Handling

- **3 retries** per judge with 30-second timeout
- **Abstain fallback** when judges fail
- **Detailed logging** for debugging
- **Latency monitoring** to prevent stuck evaluations

## Testing

```bash
# Test judges
python test_judges.py

# Run evaluation pipeline tests
python test_evaluator.py
```

## License

MIT License
