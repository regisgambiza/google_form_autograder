AI Agent — Design & Integration Notes

Purpose

The AI Agent is an autonomous runtime component intended to continuously monitor
performance of the grading pipeline (models, latencies, failure rates) and apply
small, safe corrective actions to maximize throughput while keeping errors low.

Core responsibilities

- Monitor per-model metrics: latency, per-call errors, throughput (answers/sec).
- Maintain a model pool where each model has two fallback alternatives.
- Apply safe swaps: when a model shows sustained high error rate, swap to first
  alternate and re-add the previous model to alternates.
- Tune answers-per-call (batch) per model: increase when stable and latency low,
  reduce when errors rise.
- Emit a concise action log for auditing and debugging; support CSV export.

API (programmatic)

- `AIAgent(models: List[Dict])` — create instance. Each model dict: `name`,
  `alternates`, and optional `batch`.
- `start()` / `stop()` — start or stop the agent background thread.
- `register_state_callback(fn)` — register a UI callback to receive state
  snapshots for visualization.
- `ingest_metrics(answers:int, errors:int, latency_ms:float)` — feed runtime
  metrics collected from the grader.
- `get_state()` — snapshot for dashboards.
- `export_logs_csv(path)` — export recent action logs.

Integration guidance

- The agent intentionally has no external dependencies so it can be imported
  directly into the main app process or run in a sidecar process.
- For low-risk integration, create an `AIAgent` instance and register a simple
  callback which pushes state updates to the GUI (e.g. via Qt signal or HTTP).
- The real grader should call `agent.ingest_metrics(...)` after each call or
  in periodic intervals to provide live observations.

Safety & guarantees

- All corrections are conservative (batch changes and swaps) and logged.
- The agent never deletes data; it only adjusts runtime routing/parameters.
- For production use, add feature flags and a manual "agent inspector" UI to
  allow operators to override decisions.

Simulation

- A simulated dashboard `ai_agent_simulation.html` is included to preview the
  agent's behavior via a self-contained browser UI (no app changes required).

