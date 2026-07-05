"""AI Agent runtime module

This module implements a background AIAgent that monitors model health, performs
automatic swaps between alternatives, and tunes answers-per-call (batch size)
for throughput vs reliability.

Design constraints:
- No external dependencies required (uses stdlib only).
- Exposes a simple programmatic API: start(), stop(), get_state(), export_logs_csv(path).
- Allows registering a callback to receive state updates for integration with GUIs.

This is a best-effort orchestrator: it observes metrics (errors, latency, throughput)
and applies simple heuristics to swap models or adjust batch sizes for speed and
reliability.

Example usage:

    from ai_agent import AIAgent

    agent = AIAgent(models=[
        {'name': 'mistral-nemo:12b', 'alternates': ['mistral-nemo:7b','phi4:14b'], 'batch': 4},
        {'name': 'gemma3:12b', 'alternates': ['gemma2:8b','gemma3:6b'], 'batch': 4},
    ])
    agent.start()
    # periodically call agent.get_state() for UI
    agent.stop()

"""
from __future__ import annotations

import csv
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable, Dict, List, Optional

Lock = threading.Lock


class ModelEntry:
    def __init__(self, name: str, alternates: List[str], batch: int = 4):
        self.name = name
        self.alternates = list(alternates)
        self.batch = int(batch)
        self.errors = 0
        self.latency_ms = 120

    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'alternates': list(self.alternates),
            'batch': self.batch,
            'errors': self.errors,
            'latency_ms': self.latency_ms,
        }


class AIAgent:
    """Autonomous performance agent.

    Args:
        models: list of dicts with keys: name, alternates, batch
        tick_interval: seconds between observation decisions
        max_log: maximum number of recent actions to keep
    """

    def __init__(self, models: Optional[List[Dict]] = None, tick_interval: float = 1.0, max_log: int = 500):
        self._lock = Lock()
        self.models: List[ModelEntry] = []
        models = models or []
        for m in models:
            self.models.append(ModelEntry(m['name'], m.get('alternates', []), m.get('batch', 4)))

        self.active_index = 0 if self.models else -1
        self.enabled = False
        self._tick_interval = float(tick_interval)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._logs = deque(maxlen=max_log)
        self._state_callbacks: List[Callable[[Dict], None]] = []
        # synthetic counters (would be fed by actual runtime in real integration)
        self._sim_total_errors = 0
        self._sim_total_answers = 0
        self._sim_total_latency_ms = 0.0

    # -- Agent lifecycle --
    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self.enabled = True
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self._log('agent', 'started')

    def stop(self):
        with self._lock:
            self.enabled = False
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=2.0)
            self._log('agent', 'stopped')

    def register_state_callback(self, fn: Callable[[Dict], None]):
        """Register a callback that receives the agent state dict on every tick."""
        with self._lock:
            self._state_callbacks.append(fn)

    # -- Observability / simulation helpers --
    def ingest_metrics(self, answers: int = 0, errors: int = 0, latency_ms: float = 0.0):
        """Record metrics from the grading pipeline.

        This method is safe to call from other threads (e.g. the grader).
        """
        with self._lock:
            self._sim_total_answers += int(answers)
            self._sim_total_errors += int(errors)
            self._sim_total_latency_ms += float(latency_ms) * max(1, answers)

    # -- Actions --
    def export_logs_csv(self, path: str):
        """Export cached logs to a CSV file."""
        with open(path, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['timestamp', 'actor', 'action', 'details'])
            for row in list(self._logs):
                w.writerow([row['ts'], row['actor'], row['action'], row.get('details', '')])
        self._log('agent', f'exported_logs:{path}')

    def get_state(self) -> Dict:
        """Return a snapshot of the agent state suitable for UI display."""
        with self._lock:
            return {
                'enabled': self.enabled,
                'active_index': self.active_index,
                'active_model': self.models[self.active_index].to_dict() if 0 <= self.active_index < len(self.models) else None,
                'models': [m.to_dict() for m in self.models],
                'total_answers': self._sim_total_answers,
                'total_errors': self._sim_total_errors,
                'avg_latency_ms': (self._sim_total_latency_ms / max(1, self._sim_total_answers)) if self._sim_total_answers else 0.0,
                'recent_logs': list(self._logs),
            }

    # -- Internal helpers --
    def _log(self, actor: str, action: str, details: str = ''):
        entry = {'ts': datetime.utcnow().isoformat(), 'actor': actor, 'action': action, 'details': details}
        self._logs.appendleft(entry)

    def _notify_state(self):
        state = self.get_state()
        for cb in list(self._state_callbacks):
            try:
                cb(state)
            except Exception:
                pass

    def _run_loop(self):
        while not self._stop.is_set():
            if not self.enabled:
                time.sleep(self._tick_interval)
                continue
            try:
                self._observe_and_act()
            except Exception as ex:
                self._log('agent', 'error', str(ex))
            self._notify_state()
            time.sleep(self._tick_interval)

    def _observe_and_act(self):
        """Core heuristic:
        - Look at recent errors for active model; if high -> swap to first alternate
        - If model has low errors and average latency small -> increase batch up to 32
        - If errors moderate -> reduce batch
        """
        with self._lock:
            if not self.models:
                return
            m = self.models[self.active_index]
            # Heuristics use internal counters (in real app these should be derived from live metrics)
            errs = m.errors
            avg_lat = m.latency_ms
            # Swap logic
            if errs >= 10 and m.alternates:
                # pick an alternate and swap
                alt = m.alternates.pop(0)
                m.alternates.append(m.name)  # current becomes an alternate
                oldname = m.name
                m.name = alt
                m.errors = max(0, errs // 3)
                m.latency_ms = max(40, m.latency_ms - 20)
                self._log('agent', 'swap', f'{oldname} -> {alt}')
            else:
                # Tuning batch
                if errs == 0 and avg_lat < 250 and m.batch < 32:
                    oldb = m.batch
                    m.batch = min(32, m.batch + 1)
                    if m.batch != oldb:
                        self._log('agent', 'tune_batch_increase', f'{m.name} {oldb} -> {m.batch}')
                elif errs > 4 and m.batch > 1:
                    oldb = m.batch
                    m.batch = max(1, m.batch // 2)
                    if m.batch != oldb:
                        self._log('agent', 'tune_batch_reduce', f'{m.name} {oldb} -> {m.batch}')

    # -- Utilities for testing / simulation --
    def simulate_incoming(self, answers_per_sec: int, duration_sec: int = 1):
        """Simulate ingesting a burst of answers; this updates internal totals and per-model errors.

        This is a helper so the agent can be exercised without the full app.
        """
        with self._lock:
            if not self.models:
                return
            m = self.models[self.active_index]
            batch = m.batch or 1
            calls = max(1, (answers_per_sec + batch - 1) // batch)
            fails = 0
            succ = 0
            latency_acc = 0
            # simplistic failure model: proportional to recorded errors
            fail_prob = min(0.4, m.errors / 50)
            for _ in range(calls):
                n = min(batch, answers_per_sec - succ - fails)
                if n <= 0:
                    break
                if (time.time() % 1.0) < fail_prob:
                    fails += n
                    m.errors += 1
                else:
                    succ += n
                latency_acc += (m.latency_ms + n*6)

            self._sim_total_answers += (succ + fails)
            self._sim_total_errors += fails
            self._sim_total_latency_ms += latency_acc
            # small self-adaptive latency drift
            m.latency_ms = max(40, m.latency_ms + (fails - succ) * 0.2)

            # record an activity log
            self._log('sim', 'burst', f'answers={succ+fails} succ={succ} failed={fails} model={m.name} batch={m.batch}')


if __name__ == '__main__':
    # quick smoke test when run standalone
    agent = AIAgent(models=[{'name':'mistral-nemo:12b','alternates':['phi4:14b'],'batch':4}])
    agent.start()
    for i in range(6):
        agent.simulate_incoming(answers_per_sec=40, duration_sec=1)
        time.sleep(1.0)
    agent.export_logs_csv('agent_logs.csv')
    agent.stop()
    print('Done, logs -> agent_logs.csv')

# --- Global agent registry for process-wide access ---
_GLOBAL_AGENT: Optional[AIAgent] = None


def register_global_agent(agent: AIAgent):
    global _GLOBAL_AGENT
    try:
        _GLOBAL_AGENT = agent
    except Exception:
        pass


def get_global_agent() -> Optional[AIAgent]:
    return _GLOBAL_AGENT
