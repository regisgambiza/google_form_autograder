# settings_dialog.py - extracted Settings dialog for the Classic Desktop Utility GUI
import os
import json
import shutil
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QWidget, QFrame, QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QScrollArea, QFileDialog, QMessageBox,
)
import ollama
from evaluator_config import (
    DEFAULT_CONFIG,
    effective_ai_worker_count,
    is_llamacpp_only,
)
from cache_manager import clear_grading_cache
from app_theme import apply_widget_theme


EXECUTION_MODE_PRESETS = {
    "Maximum accuracy: independent unanimous jury + review": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 4,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 4,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
        "active_judge_roles": ["semantic_judge", "factual_judge", "concept_judge", "strict_judge"],
        "adaptive_math_jury": {
            "enabled": True,
            "primary_roles": ["semantic_judge", "factual_judge", "concept_judge"],
            "adjudicator_role": "strict_judge",
            "minimum_primary_confidence": 0.90,
            "ambiguity_markers": ["ambiguous", "uncertain", "unclear", "insufficient", "depends"],
        },
        "early_exit": {"enabled": False, "min_judges": 3, "agreement_confidence": 0.90},
        "accuracy_policy": {
            "enabled": True,
            "minimum_judge_confidence": 0.90,
            "required_accept_roles": ["semantic_judge", "factual_judge", "concept_judge"],
            "require_distinct_models": True,
            "embeddings_can_accept": False,
            "ambiguous_outcome": "REVIEW",
        },
        "answer_key_auto_add_proven_equivalents": True,
        "patient_ai_mode": True,
        "enable_jury_circuit_breaker": False,
        "judge_timeout_seconds": 7200,
        "judge_http_timeout_seconds": 7200,
        "judge_total_hard_timeout_seconds": 21600,
        "answer_hard_timeout_seconds": 21600,
        "jury_semaphore_acquire_timeout_seconds": 21600,
        "max_latency_per_answer_seconds": 21600,
        "embedding_timeout_seconds": 1800,
        "rubric_timeout_seconds": 3600,
        "dispatcher_stall_timeout_seconds": 7200,
        "ai_stall_timeout_seconds": 900,
        "jury_circuit_break_seconds": 0,
    },
    "Math: deterministic checks + semantic judge only (recommended)": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 1,
        "worker_queue_size": 1200,
        "producer_det_queue_low_watermark": 450,
        "producer_det_queue_high_watermark": 900,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "max_concurrent_embedding_http": 1,
        "judge_timeout_seconds": 45,
        "judge_http_timeout_seconds": 65,
        "judge_total_hard_timeout_seconds": 55,
        "jury_circuit_break_seconds": 900,
        "max_latency_per_answer_seconds": 45,
        "dispatcher_stall_timeout_seconds": 420,
        "ai_stall_timeout_seconds": 300,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
        "active_judge_roles": ["semantic_judge"],
        "judge_prewarm_enabled": True,
        "judge_prewarm_timeout_seconds": 20,
        "confidence_thresholds": {
            "auto_accept": 0.90,
            "auto_reject": 0.35,
        },
        "embedding_thresholds": {
            "auto_accept": 0.90,
            "auto_reject": 0.42,
            "send_to_jury": [0.42, 0.90]
        },
        "consensus_weights": {
            "semantic_similarity": 0.45,
            "concept_coverage": 0.25,
            "factual_accuracy": 0.15,
            "strict_judge": 0.05,
            "language_noise": 0.0,
            "embedding": 0.10,
        },
    },
    "Bulk speed: all forms, high concurrency, less review": {
        "deterministic_worker_count": 7,
        "ai_worker_count": 4,
        "worker_queue_size": 3000,
        "producer_det_queue_low_watermark": 1200,
        "producer_det_queue_high_watermark": 2500,
        "max_concurrent_judge_http": 5,
        "max_concurrent_jury_answers": 4,
        "max_concurrent_embedding_http": 4,
        "judge_timeout_seconds": 25,
        "judge_http_timeout_seconds": 35,
        "max_latency_per_answer_seconds": 25,
        "dispatcher_stall_timeout_seconds": 120,
        "ai_stall_timeout_seconds": 120,
        "enable_async_judges": False,
        "sync_judge_parallelism": 6,
    },
    "Daily balanced: semantic/factual review with moderate concurrency": {
        "deterministic_worker_count": 5,
        "ai_worker_count": 3,
        "worker_queue_size": 2000,
        "producer_det_queue_low_watermark": 900,
        "producer_det_queue_high_watermark": 1700,
        "max_concurrent_judge_http": 4,
        "max_concurrent_jury_answers": 3,
        "max_concurrent_embedding_http": 3,
        "judge_timeout_seconds": 30,
        "judge_http_timeout_seconds": 45,
        "max_latency_per_answer_seconds": 30,
        "dispatcher_stall_timeout_seconds": 150,
        "ai_stall_timeout_seconds": 120,
        "enable_async_judges": False,
        "sync_judge_parallelism": 6,
    },
    "Slow-model safe: lower concurrency, longer timeouts": {
        "deterministic_worker_count": 5,
        "ai_worker_count": 2,
        "worker_queue_size": 1800,
        "producer_det_queue_low_watermark": 700,
        "producer_det_queue_high_watermark": 1400,
        "max_concurrent_judge_http": 2,
        "max_concurrent_jury_answers": 2,
        "max_concurrent_embedding_http": 2,
        "judge_timeout_seconds": 45,
        "judge_http_timeout_seconds": 65,
        "max_latency_per_answer_seconds": 45,
        "dispatcher_stall_timeout_seconds": 240,
        "ai_stall_timeout_seconds": 180,
        "enable_async_judges": False,
        "sync_judge_parallelism": 3,
    },
    "General accuracy: semantic + factual 2-judge review": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 1,
        "worker_queue_size": 1200,
        "producer_det_queue_low_watermark": 450,
        "producer_det_queue_high_watermark": 900,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "max_concurrent_embedding_http": 1,
        "judge_timeout_seconds": 80,
        "judge_http_timeout_seconds": 110,
        "judge_total_hard_timeout_seconds": 95,
        "jury_circuit_break_seconds": 1200,
        "max_latency_per_answer_seconds": 90,
        "dispatcher_stall_timeout_seconds": 420,
        "ai_stall_timeout_seconds": 300,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
        "active_judge_roles": ["semantic_judge", "factual_judge"],
        "judge_prewarm_enabled": True,
        "judge_prewarm_timeout_seconds": 20,
        "embedding_thresholds": {
            "auto_accept": 0.88,
            "auto_reject": 0.52,
            "send_to_jury": [0.52, 0.88]
        },
    },
    "Strict review: semantic + factual + strict judge": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 1,
        "worker_queue_size": 1200,
        "producer_det_queue_low_watermark": 450,
        "producer_det_queue_high_watermark": 900,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "max_concurrent_embedding_http": 1,
        "judge_timeout_seconds": 55,
        "judge_http_timeout_seconds": 75,
        "judge_total_hard_timeout_seconds": 50,
        "jury_circuit_break_seconds": 900,
        "max_latency_per_answer_seconds": 55,
        "dispatcher_stall_timeout_seconds": 420,
        "ai_stall_timeout_seconds": 300,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
        "active_judge_roles": ["semantic_judge", "factual_judge", "strict_judge"],
        "judge_prewarm_enabled": True,
        "judge_prewarm_timeout_seconds": 20,
        "embedding_thresholds": {
            "auto_accept": 0.90,
            "auto_reject": 0.45,
            "send_to_jury": [0.45, 0.90]
        },
    },
    "Recovery: lowest load, longest timeouts": {
        "deterministic_worker_count": 3,
        "ai_worker_count": 1,
        "worker_queue_size": 1000,
        "producer_det_queue_low_watermark": 350,
        "producer_det_queue_high_watermark": 700,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "max_concurrent_embedding_http": 1,
        "judge_timeout_seconds": 90,
        "judge_http_timeout_seconds": 120,
        "max_latency_per_answer_seconds": 90,
        "dispatcher_stall_timeout_seconds": 600,
        "ai_stall_timeout_seconds": 420,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
    },
}

EXECUTION_MODE_ALIASES = {
    "Max Speed": "Bulk speed: all forms, high concurrency, less review",
    "Balanced": "Daily balanced: semantic/factual review with moderate concurrency",
    "Stable": "Slow-model safe: lower concurrency, longer timeouts",
    "High Accuracy": "General accuracy: semantic + factual 2-judge review",
    "High Accuracy (Practical)": "Strict review: semantic + factual + strict judge",
    "Safe Mode": "Recovery: lowest load, longest timeouts",
    "Fastest: Bulk Grading": "Bulk speed: all forms, high concurrency, less review",
    "Standard: Daily Grading": "Daily balanced: semantic/factual review with moderate concurrency",
    "Reliable: Slow Model Safety": "Slow-model safe: lower concurrency, longer timeouts",
    "Conservative: 2-Judge Review": "General accuracy: semantic + factual 2-judge review",
    "Strict: 3-Judge Review": "Strict review: semantic + factual + strict judge",
    "Recovery: Low Load": "Recovery: lowest load, longest timeouts",
}

DEFAULT_EXECUTION_MODE = "Maximum accuracy: independent unanimous jury + review"


def normalize_execution_mode(mode_name):
    return EXECUTION_MODE_ALIASES.get(mode_name, mode_name)


class SettingsModelDiscoveryThread(QThread):
    finished = Signal(object, object, str)

    def __init__(self, llamacpp_model_dir):
        super().__init__()
        self.llamacpp_model_dir = str(llamacpp_model_dir or "")

    def run(self):
        errors = []
        ollama_models = []
        llamacpp_models = []
        try:
            ollama_models = [
                self._read_ollama_model_name(model_info)
                for model_info in ollama.list().get("models", [])
            ]
            ollama_models = [m for m in ollama_models if m]
        except Exception as exc:
            errors.append(f"Ollama models unavailable: {exc}")
        try:
            llamacpp_models = self._find_llamacpp_models(self.llamacpp_model_dir)
        except Exception as exc:
            errors.append(f"llama.cpp model scan failed: {exc}")
        self.finished.emit(ollama_models, llamacpp_models, "; ".join(errors))

    @staticmethod
    def _read_ollama_model_name(model_info):
        if isinstance(model_info, dict):
            return model_info.get("name") or model_info.get("model")
        return getattr(model_info, "name", None) or getattr(model_info, "model", None)

    @staticmethod
    def _find_llamacpp_models(model_dir):
        root = os.path.expandvars(os.path.expanduser(str(model_dir or "")))
        found = []
        if not root or not os.path.isdir(root):
            return found
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                lower_name = filename.lower()
                if not lower_name.endswith(".gguf"):
                    continue
                if lower_name.startswith("mmproj-") or "mmproj" in lower_name:
                    continue
                path = os.path.join(dirpath, filename)
                found.append(os.path.relpath(path, root).replace("\\", "/"))
        return sorted(found, key=str.casefold)



def show_settings_dialog(owner):
    dialog = QDialog(owner)
    dialog.setWindowTitle("Settings")
    dialog.setModal(True)
    dialog.resize(1120, 860)
    dialog.setMinimumSize(920, 700)
    dialog.setSizeGripEnabled(True)

    main_layout = QVBoxLayout(dialog)
    main_layout.setContentsMargins(12, 12, 12, 12)
    main_layout.setSpacing(10)

    scroll_area = QScrollArea(dialog)
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QFrame.NoFrame)
    scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    scroll_widget = QWidget()
    scroll_widget.setMinimumWidth(0)
    settings_layout = QVBoxLayout(scroll_widget)
    settings_layout.setContentsMargins(0, 0, 0, 0)
    settings_layout.setSpacing(12)

    def make_settings_section(title, description=""):
        section = QFrame(scroll_widget)
        section.setObjectName("SettingsSection")
        section.setFrameShape(QFrame.StyledPanel)
        section.setStyleSheet(
            "QFrame#SettingsSection {"
            "background: #ffffff;"
            "border: 1px solid #d6dde5;"
            "border-radius: 8px;"
            "}"
            "QLabel#SettingsSectionTitle {"
            "font-weight: 700;"
            "font-size: 14px;"
            "color: #111827;"
            "}"
            "QLabel#SettingsSectionDescription {"
            "color: #5f6b7a;"
            "}"
        )
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(14, 12, 14, 14)
        section_layout.setSpacing(8)
        title_label = QLabel(title, section)
        title_label.setObjectName("SettingsSectionTitle")
        section_layout.addWidget(title_label)
        if description:
            description_label = QLabel(description, section)
            description_label.setObjectName("SettingsSectionDescription")
            description_label.setWordWrap(True)
            section_layout.addWidget(description_label)
        section_form = QFormLayout()
        section_form.setContentsMargins(0, 4, 0, 0)
        section_form.setSpacing(8)
        section_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        section_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        section_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        section_layout.addLayout(section_form)
        settings_layout.addWidget(section)
        return section_form

    global_form = make_settings_section(
        "Global Settings",
        "General grading behavior, cache policy, application workers, and run safety.",
    )
    openrouter_form = make_settings_section(
        "OpenRouter",
        "Cloud provider routing, concurrency, cost controls, and answer batching.",
    )
    llamacpp_form = make_settings_section(
        "llama.cpp",
        "Local GGUF provider settings, server location, cleanup behavior, and judge models.",
    )
    ollama_form = make_settings_section(
        "Ollama",
        "Local Ollama model choices, monitoring model, provider capacity, and generation limits.",
    )
    settings_layout.addStretch()
    scroll_widget.setLayout(settings_layout)
    scroll_area.setWidget(scroll_widget)
    main_layout.addWidget(scroll_area)

    evaluator_combo = QComboBox(dialog)
    evaluator_combo.addItems([
        "ai_evaluator (Basic)",
        "ai_evaluator_2 (Advanced)",
        "ai_evaluator_semantic (Semantic Pipeline)",
    ])

    strictness_combo = QComboBox(dialog)
    strictness_combo.addItems(["strict", "balanced", "lenient", "review-heavy", "practice"])
    strictness_combo.setToolTip(
        "Controls how the final AI votes become Accepted, Needs review, or Rejected. "
        "Strict requires stronger independent agreement; lenient/practice accept more high-confidence equivalent answers."
    )
    provider_strategy_combo = QComboBox(dialog)
    provider_strategy_combo.addItems([
        "free_first_ollama_fallback",
        "openrouter_llamacpp_ollama",
        "openrouter_llamacpp",
        "llamacpp_openrouter",
        "local_all",
        "custom_priority",
        "free_first_paid_fallback",
        "cheap_paid_only",
        "openrouter_only",
        "llamacpp_only",
        "ollama_only",
    ])
    provider_strategy_combo.setToolTip(
        "Controls provider routing. Paid strategies use the cheap paid fallback model list and respect the spend cap."
    )
    provider_priority_edit = QLineEdit(dialog)
    provider_priority_edit.setText("openrouter,llamacpp,ollama")
    provider_priority_edit.setToolTip(
        "Custom provider order used by custom_priority and legacy/default routing. Example: openrouter,llamacpp,ollama"
    )
    max_openrouter_spend_spin = QDoubleSpinBox(dialog)
    max_openrouter_spend_spin.setRange(0.0, 100.0)
    max_openrouter_spend_spin.setSingleStep(0.10)
    max_openrouter_spend_spin.setDecimals(2)
    max_openrouter_spend_spin.setToolTip("0 means no OpenRouter spend cap for the current app run.")

    model_combo = QComboBox(dialog)
    embedding_model_combo = QComboBox(dialog)
    reasoning_model_combo = QComboBox(dialog)
    minimum_judge_confidence_spin = QDoubleSpinBox(dialog)
    minimum_judge_confidence_spin.setRange(0.50, 1.00)
    minimum_judge_confidence_spin.setSingleStep(0.01)
    minimum_judge_confidence_spin.setDecimals(2)
    distinct_models_checkbox = QCheckBox("Require different models for acceptance", dialog)
    key_auto_add_checkbox = QCheckBox("Append validated answers now; audit them in Answer Keys", dialog)
    patient_ai_checkbox = QCheckBox("Patient AI: wait for complete model responses", dialog)
    dedup_checkbox = QCheckBox("Deduplicated mode: group equivalent responses before evaluation", dialog)
    dedup_checkbox.setToolTip(
        "On: normalize/group equivalent responses and evaluate one representative.\n"
        "Off: raw mode; take every response exactly as read from the form, with no pre-deduplication."
    )
    audit_path_edit = QLineEdit(dialog)
    benchmark_path_edit = QLineEdit(dialog)

    cfg = {}
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    provider_priority_edit.setText(",".join(str(x) for x in cfg.get("provider_priority", ["openrouter", "llamacpp", "ollama"])))

    def normalize_model_key(model_name):
        text = str(model_name or "").strip()
        return text[:-7] if text.endswith(":latest") else text

    def add_model_choice(model_names, seen_keys, model_name):
        text = str(model_name or "").strip()
        key = normalize_model_key(text)
        if text and key and key not in seen_keys:
            model_names.append(text)
            seen_keys.add(key)

    ollama_models = []
    llamacpp_model_dir = cfg.get("llamacpp_model_dir", r"C:\Users\regis\.lmstudio\models")
    llamacpp_models = []

    ollama_keys = {
        normalize_model_key(model_name)
        for model_name in ollama_models
        if normalize_model_key(model_name)
    }

    available_models = []
    seen_model_keys = set()

    # Prefer configured spelling, then append locally installed Ollama models.
    models = cfg.get("models", {}).get("judge", [])
    embedding_model = cfg.get("embedding_model")
    reasoning_model = cfg.get("reasoning_model")
    if models:
        add_model_choice(available_models, seen_model_keys, models[0])
    add_model_choice(available_models, seen_model_keys, embedding_model)
    add_model_choice(available_models, seen_model_keys, reasoning_model)

    cfg_jury = cfg.get("jury_models", {}) if cfg else {}
    for configured_model in cfg_jury.values():
        add_model_choice(available_models, seen_model_keys, configured_model)
    supervisor_model = cfg.get(
        "openrouter_supervisor_ollama_model",
        DEFAULT_CONFIG.get("openrouter_supervisor_ollama_model", "gpt-oss:latest"),
    )
    add_model_choice(available_models, seen_model_keys, supervisor_model)
    for model_name in ollama_models:
        add_model_choice(available_models, seen_model_keys, model_name)

    extra_configured_models = sorted(
        key for key in seen_model_keys if key not in ollama_keys
    )
    installed_model_count = len(ollama_keys)
    model_status_label = QLabel(
        f"{len(available_models)} selectable models "
        f"({installed_model_count} installed"
        f"{', ' + str(len(extra_configured_models)) + ' configured only' if extra_configured_models else ''}; "
        "llama.cpp scan pending).",
        dialog,
    )
    model_status_label.setWordWrap(True)
    if extra_configured_models:
        model_status_label.setToolTip(
            "Configured but not reported by Ollama: " + ", ".join(extra_configured_models)
        )

    if available_models:
        model_combo.addItems(available_models)
        embedding_model_combo.addItems(available_models)
        reasoning_model_combo.addItems(available_models)

    # Jury model selectors (one combobox per jury role)
    jury_combos = {}
    jury_role_labels = {}
    jury_defaults = DEFAULT_CONFIG.get("jury_models", {})
    visible_settings_jury_roles = ("semantic_judge", "factual_judge", "concept_judge", "strict_judge")
    for role, default_model in jury_defaults.items():
        combo = QComboBox(dialog)
        # Ensure the configured/default model is present in the list
        role_model = cfg_jury.get(role, default_model)
        role_models = list(available_models)
        if normalize_model_key(role_model) not in {normalize_model_key(m) for m in role_models}:
            role_models.insert(0, role_model)
        if role_models:
            combo.addItems(role_models)
            combo.setCurrentText(role_model)
        jury_combos[role] = combo
        jury_role_labels[role] = QLabel(role.replace('_', ' ').title() + ":", dialog)
        if role not in visible_settings_jury_roles:
            combo.hide()
            jury_role_labels[role].hide()

    llamacpp_role_combos = {}
    llamacpp_role_labels = {}
    cfg_llamacpp = cfg.get("llamacpp_models", {}) if cfg else {}
    for role in jury_defaults:
        combo = QComboBox(dialog)
        configured = cfg_llamacpp.get(role, [])
        if isinstance(configured, str):
            configured = [configured]
        role_models = list(llamacpp_models)
        for configured_model in reversed([str(m).strip() for m in configured if str(m).strip()]):
            if configured_model not in role_models:
                role_models.insert(0, configured_model)
        if role_models:
            combo.addItems(role_models)
        else:
            combo.addItem("No llama.cpp GGUF models found")
            combo.setEnabled(False)
        if configured:
            combo.setCurrentText(str(configured[0]))
        combo.setToolTip(
            "Select a GGUF model found under the llama.cpp model folder. "
            "Projector/mmproj files are hidden because they are not grading models."
        )
        llamacpp_role_combos[role] = combo
        llamacpp_role_labels[role] = QLabel("llama.cpp " + role.replace('_', ' ').title() + ":", dialog)
        if role not in visible_settings_jury_roles:
            combo.hide()
            llamacpp_role_labels[role].hide()

    def combo_contains(combo, text):
        target = normalize_model_key(text)
        return any(normalize_model_key(combo.itemText(i)) == target for i in range(combo.count()))

    def add_combo_choice(combo, text):
        text = str(text or "").strip()
        if text and not combo_contains(combo, text):
            combo.addItem(text)

    def apply_discovered_models(discovered_ollama, discovered_llamacpp, error_text):
        discovered_ollama = [str(m).strip() for m in discovered_ollama or [] if str(m).strip()]
        discovered_llamacpp = [str(m).strip() for m in discovered_llamacpp or [] if str(m).strip()]
        for model_name in discovered_ollama:
            for combo in [model_combo, embedding_model_combo, reasoning_model_combo, supervisor_model_combo, *jury_combos.values()]:
                add_combo_choice(combo, model_name)
        for role, combo in llamacpp_role_combos.items():
            current = combo.currentText().strip()
            placeholder = current == "No llama.cpp GGUF models found"
            if placeholder:
                combo.clear()
            for model_name in discovered_llamacpp:
                add_combo_choice(combo, model_name)
            if placeholder and combo.count() == 0:
                combo.addItem("No llama.cpp GGUF models found")
                combo.setEnabled(False)
            else:
                combo.setEnabled(True)
                configured = cfg_llamacpp.get(role, [])
                if isinstance(configured, str):
                    configured = [configured]
                preferred = str((configured or [""])[0]).strip()
                if preferred and combo_contains(combo, preferred):
                    combo.setCurrentText(preferred)
                elif current and current != "No llama.cpp GGUF models found" and combo_contains(combo, current):
                    combo.setCurrentText(current)
        ollama_keys_now = {
            normalize_model_key(model_name)
            for model_name in discovered_ollama
            if normalize_model_key(model_name)
        }
        extra_now = sorted(key for key in seen_model_keys if key not in ollama_keys_now)
        model_status_label.setText(
            f"{model_combo.count()} selectable models "
            f"({len(ollama_keys_now)} installed"
            f"{', ' + str(len(extra_now)) + ' configured only' if extra_now else ''}; "
            f"{len(discovered_llamacpp)} llama.cpp GGUF models found)."
        )
        if error_text:
            model_status_label.setToolTip(str(error_text))
        refresh_jury_status()

    model_discovery_thread = SettingsModelDiscoveryThread(llamacpp_model_dir)
    dialog._model_discovery_thread = model_discovery_thread
    model_discovery_thread.finished.connect(apply_discovered_models)
    model_discovery_thread.start()

    report_checkbox = QCheckBox("Generate Report", dialog)
    dedup_checkbox.setChecked(cfg.get("enable_deduplication", True))
    legacy_judge_answer_batch_size = max(1, int(cfg.get("judge_answer_batch_size", 3)))
    ollama_judge_answer_batch_size_spin = QSpinBox(dialog)
    ollama_judge_answer_batch_size_spin.setRange(1, 20)
    ollama_judge_answer_batch_size_spin.setValue(
        max(1, int(cfg.get("ollama_judge_answer_batch_size", legacy_judge_answer_batch_size)))
    )
    ollama_judge_answer_batch_size_spin.setToolTip(
        "How many student answers are sent to each local Ollama judge call. "
        "Use 1 for best reliability on local models and limited hardware."
    )
    openrouter_judge_answer_batch_size_spin = QSpinBox(dialog)
    openrouter_judge_answer_batch_size_spin.setRange(1, 50)
    openrouter_judge_answer_batch_size_spin.setValue(
        max(1, int(cfg.get("openrouter_judge_answer_batch_size", legacy_judge_answer_batch_size)))
    )
    openrouter_judge_answer_batch_size_spin.setToolTip(
        "How many student answers are sent to each OpenRouter judge call. "
        "Higher values can improve throughput but may increase malformed JSON risk."
    )
    llamacpp_judge_answer_batch_size_spin = QSpinBox(dialog)
    llamacpp_judge_answer_batch_size_spin.setRange(1, 1)
    llamacpp_judge_answer_batch_size_spin.setValue(1)
    llamacpp_judge_answer_batch_size_spin.setToolTip(
        "llama.cpp is capped at 1 answer per judge call to avoid malformed local batch JSON."
    )
    legacy_ai_worker_count = max(1, int(cfg.get("ai_worker_count", 4) or 4))
    openrouter_ai_worker_count_spin = QSpinBox(dialog)
    openrouter_ai_worker_count_spin.setRange(1, 12)
    openrouter_ai_worker_count_spin.setValue(
        max(1, int(cfg.get("openrouter_ai_worker_count", legacy_ai_worker_count) or legacy_ai_worker_count))
    )
    openrouter_ai_worker_count_spin.setToolTip(
        "Application AI worker threads when OpenRouter is active. Higher values process more questions in parallel. "
        "Changes apply to the next grading run."
    )
    ollama_ai_worker_count_spin = QSpinBox(dialog)
    ollama_ai_worker_count_spin.setRange(1, 4)
    ollama_ai_worker_count_spin.setValue(max(1, int(cfg.get("ollama_ai_worker_count", 1) or 1)))
    ollama_ai_worker_count_spin.setToolTip(
        "Application AI worker threads when Ollama is active. Keep low unless your local hardware can handle parallel model work. "
        "Changes apply to the next grading run."
    )
    llamacpp_ai_worker_count_spin = QSpinBox(dialog)
    llamacpp_ai_worker_count_spin.setRange(1, 1)
    llamacpp_ai_worker_count_spin.setValue(1)
    llamacpp_ai_worker_count_spin.setToolTip(
        "llama.cpp is capped at 1 application AI worker so local GGUF grading stays serial and reliable."
    )
    openrouter_worker_count_spin = QSpinBox(dialog)
    openrouter_worker_count_spin.setRange(1, 12)
    openrouter_worker_count_spin.setValue(max(1, int(cfg.get("openrouter_worker_count", 4) or 4)))
    openrouter_worker_count_spin.setToolTip(
        "OpenRouter provider worker threads. Higher values allow more concurrent OpenRouter API calls. "
        "Changes apply to the next grading run."
    )
    ollama_worker_count_spin = QSpinBox(dialog)
    ollama_worker_count_spin.setRange(1, 4)
    ollama_worker_count_spin.setValue(max(1, int(cfg.get("ollama_worker_count", 1) or 1)))
    ollama_worker_count_spin.setToolTip(
        "Ollama provider worker threads. Keep this at 1 unless your local hardware can run multiple model requests efficiently. "
        "Changes apply to the next grading run."
    )
    llamacpp_worker_count_spin = QSpinBox(dialog)
    llamacpp_worker_count_spin.setRange(1, 1)
    llamacpp_worker_count_spin.setValue(1)
    llamacpp_worker_count_spin.setToolTip(
        "llama.cpp is capped at 1 provider worker because local GGUF models share one server/hardware lane."
    )
    llamacpp_enabled_checkbox = QCheckBox("Enable llama.cpp provider", dialog)
    llamacpp_enabled_checkbox.setChecked(bool(cfg.get("llamacpp_enabled", True)))
    llamacpp_require_server_checkbox = QCheckBox("Require running llama.cpp server", dialog)
    llamacpp_require_server_checkbox.setChecked(bool(cfg.get("llamacpp_require_server", True)))
    llamacpp_auto_start_checkbox = QCheckBox("Start llama.cpp server automatically when needed", dialog)
    llamacpp_auto_start_checkbox.setChecked(bool(cfg.get("llamacpp_auto_start_server", True)))
    llamacpp_auto_start_checkbox.setToolTip(
        "When llama.cpp-only grading is selected and no server is responding, start llama-server.exe using the selected local model."
    )
    llamacpp_stop_after_grading_checkbox = QCheckBox("Stop llama.cpp server after grading", dialog)
    llamacpp_stop_after_grading_checkbox.setChecked(bool(cfg.get("llamacpp_stop_server_after_grading", False)))
    llamacpp_stop_after_grading_checkbox.setToolTip(
        "When grading finishes, stop llama-server.exe to release RAM used by local GGUF models. "
        "Leave off if you use the same llama.cpp server in another app."
    )
    llamacpp_stop_on_close_checkbox = QCheckBox("Stop llama.cpp server when app closes", dialog)
    llamacpp_stop_on_close_checkbox.setChecked(bool(cfg.get("llamacpp_stop_server_on_app_close", False)))
    llamacpp_stop_on_close_checkbox.setToolTip(
        "When this app closes, stop llama-server.exe to release RAM used by local GGUF models. "
        "This does not close LM Studio itself."
    )
    llamacpp_base_url_edit = QLineEdit(dialog)
    llamacpp_base_url_edit.setText(str(cfg.get("llamacpp_api_base_url", "http://127.0.0.1:8080")))
    llamacpp_context_size_spin = QSpinBox(dialog)
    llamacpp_context_size_spin.setRange(512, 1048576)
    llamacpp_context_size_spin.setValue(max(512, int(cfg.get("llamacpp_server_context_size", 32768) or 32768)))
    llamacpp_gpu_layers_combo = QComboBox(dialog)
    llamacpp_gpu_layers_combo.setEditable(True)
    llamacpp_gpu_layers_combo.addItems(["auto", "all", "0"])
    llamacpp_gpu_layers_combo.setCurrentText(str(cfg.get("llamacpp_server_gpu_layers", "auto")))
    llamacpp_threads_spin = QSpinBox(dialog)
    llamacpp_threads_spin.setRange(1, 256)
    llamacpp_threads_spin.setValue(max(1, int(cfg.get("llamacpp_server_threads", 8) or 8)))
    llamacpp_threads_batch_spin = QSpinBox(dialog)
    llamacpp_threads_batch_spin.setRange(1, 256)
    llamacpp_threads_batch_spin.setValue(max(1, int(cfg.get("llamacpp_server_threads_batch", 8) or 8)))
    llamacpp_server_batch_size_spin = QSpinBox(dialog)
    llamacpp_server_batch_size_spin.setRange(1, 8192)
    llamacpp_server_batch_size_spin.setValue(max(1, int(cfg.get("llamacpp_server_batch_size", 1024) or 1024)))
    llamacpp_server_ubatch_size_spin = QSpinBox(dialog)
    llamacpp_server_ubatch_size_spin.setRange(1, 8192)
    llamacpp_server_ubatch_size_spin.setValue(max(1, int(cfg.get("llamacpp_server_ubatch_size", 512) or 512)))
    llamacpp_flash_attn_combo = QComboBox(dialog)
    llamacpp_flash_attn_combo.addItems(["auto", "on", "off"])
    llamacpp_flash_attn_combo.setCurrentText(str(cfg.get("llamacpp_server_flash_attn", "auto")).lower())
    llama_cache_types = ["q8_0", "f16", "bf16", "q4_0", "q4_1", "q5_0", "q5_1", "f32", "iq4_nl"]
    llamacpp_cache_type_k_combo = QComboBox(dialog)
    llamacpp_cache_type_k_combo.addItems(llama_cache_types)
    llamacpp_cache_type_k_combo.setCurrentText(str(cfg.get("llamacpp_server_cache_type_k", "q8_0")).lower())
    llamacpp_cache_type_v_combo = QComboBox(dialog)
    llamacpp_cache_type_v_combo.addItems(llama_cache_types)
    llamacpp_cache_type_v_combo.setCurrentText(str(cfg.get("llamacpp_server_cache_type_v", "q8_0")).lower())
    llamacpp_parallel_spin = QSpinBox(dialog)
    llamacpp_parallel_spin.setRange(1, 32)
    llamacpp_parallel_spin.setValue(max(1, int(cfg.get("llamacpp_server_parallel", 1) or 1)))
    llamacpp_mmap_checkbox = QCheckBox("Enable model memory mapping (--mmap)", dialog)
    llamacpp_mmap_checkbox.setChecked(bool(cfg.get("llamacpp_server_mmap", True)))
    llamacpp_jinja_checkbox = QCheckBox("Enable Jinja chat templates (--jinja)", dialog)
    llamacpp_jinja_checkbox.setChecked(bool(cfg.get("llamacpp_server_jinja", True)))
    llamacpp_server_exe_edit = QLineEdit(dialog)
    llamacpp_server_exe_edit.setText(str(cfg.get("llamacpp_server_executable", r"C:\Tools\llama.cpp\llama-server.exe")))
    llamacpp_server_exe_picker = QWidget(dialog)
    llamacpp_server_exe_picker_layout = QHBoxLayout(llamacpp_server_exe_picker)
    llamacpp_server_exe_picker_layout.setContentsMargins(0, 0, 0, 0)
    llamacpp_server_exe_picker_layout.setSpacing(6)
    llamacpp_server_exe_browse_btn = QPushButton("Browse...", dialog)
    llamacpp_server_exe_browse_btn.setToolTip("Choose llama-server.exe.")
    llamacpp_server_exe_picker_layout.addWidget(llamacpp_server_exe_edit, 1)
    llamacpp_server_exe_picker_layout.addWidget(llamacpp_server_exe_browse_btn)

    def browse_llamacpp_server_exe():
        current_exe = os.path.expandvars(os.path.expanduser(llamacpp_server_exe_edit.text().strip()))
        current_dir = os.path.dirname(current_exe) if current_exe else ""
        if not current_dir or not os.path.isdir(current_dir):
            current_dir = os.path.expanduser("~")
        selected_exe, _filter = QFileDialog.getOpenFileName(
            dialog,
            "Select llama-server.exe",
            current_dir,
            "Executable Files (*.exe);;All Files (*)",
        )
        if selected_exe:
            llamacpp_server_exe_edit.setText(selected_exe)

    llamacpp_server_exe_browse_btn.clicked.connect(browse_llamacpp_server_exe)
    llamacpp_model_dir_edit = QLineEdit(dialog)
    llamacpp_model_dir_edit.setText(str(llamacpp_model_dir))
    llamacpp_model_dir_picker = QWidget(dialog)
    llamacpp_model_dir_picker_layout = QHBoxLayout(llamacpp_model_dir_picker)
    llamacpp_model_dir_picker_layout.setContentsMargins(0, 0, 0, 0)
    llamacpp_model_dir_picker_layout.setSpacing(6)
    llamacpp_model_dir_browse_btn = QPushButton("Browse...", dialog)
    llamacpp_model_dir_browse_btn.setToolTip("Choose the root folder that contains llama.cpp GGUF models.")
    llamacpp_model_dir_picker_layout.addWidget(llamacpp_model_dir_edit, 1)
    llamacpp_model_dir_picker_layout.addWidget(llamacpp_model_dir_browse_btn)

    def browse_llamacpp_model_dir():
        current_dir = os.path.expandvars(os.path.expanduser(llamacpp_model_dir_edit.text().strip()))
        if not current_dir or not os.path.isdir(current_dir):
            current_dir = os.path.expanduser("~")
        selected_dir = QFileDialog.getExistingDirectory(
            dialog,
            "Select llama.cpp Model Folder",
            current_dir,
        )
        if selected_dir:
            llamacpp_model_dir_edit.setText(selected_dir)

    llamacpp_model_dir_browse_btn.clicked.connect(browse_llamacpp_model_dir)
    supervisor_model_combo = QComboBox(dialog)
    supervisor_model_combo.setToolTip(
        "Local Ollama model used to audit OpenRouter judge quality. "
        "This does not grade student answers directly unless OpenRouter falls back to Ollama."
    )
    if available_models:
        supervisor_model_combo.addItems(available_models)
    if supervisor_model and normalize_model_key(supervisor_model) not in {
        normalize_model_key(supervisor_model_combo.itemText(i))
        for i in range(supervisor_model_combo.count())
    }:
        supervisor_model_combo.insertItem(0, supervisor_model)
    supervisor_model_combo.setCurrentText(str(supervisor_model or "gpt-oss:latest"))
    batch_size_spin = QSpinBox(dialog)
    batch_size_spin.setRange(1, 200)
    batch_auto_checkbox = QCheckBox("Auto", dialog)
    grading_mode_combo = QComboBox(dialog)
    grading_mode_combo.addItems(["Whole Form", "Recent Only"])
    execution_mode_combo = QComboBox(dialog)
    execution_mode_combo.addItems(list(EXECUTION_MODE_PRESETS.keys()))
    jury_status_label = QLabel(dialog)
    jury_status_label.setWordWrap(True)

    def active_roles_for_mode(mode_name):
        mode_name = normalize_execution_mode(mode_name)
        preset = EXECUTION_MODE_PRESETS.get(mode_name, {})
        roles = preset.get("active_judge_roles", cfg.get("active_judge_roles", []))
        if not isinstance(roles, list) or not roles:
            roles = list(jury_defaults.keys())
        return {role for role in roles if role in jury_defaults}

    def refresh_jury_status(mode_name=None):
        mode = mode_name or execution_mode_combo.currentText()
        active_roles = active_roles_for_mode(mode)
        preset = EXECUTION_MODE_PRESETS.get(normalize_execution_mode(mode), {})
        adaptive = preset.get("adaptive_math_jury", cfg.get("adaptive_math_jury", {}))
        primary_roles = list(adaptive.get("primary_roles", [])) if adaptive.get("enabled", False) else []
        adjudicator_role = str(adaptive.get("adjudicator_role", ""))
        visible_jury_roles = set(visible_settings_jury_roles)
        status_text = (
            f"{len(active_roles & visible_jury_roles)} active jury roles."
        )
        if len(primary_roles) >= 3 and adjudicator_role:
            status_text += (
                f" Flow: {jury_combos[primary_roles[0]].currentText()} evaluates; "
                f"{jury_combos[primary_roles[1]].currentText()} verifies; "
                f"{jury_combos[primary_roles[2]].currentText()} challenges completeness; "
                f"{jury_combos[adjudicator_role].currentText()} adjudicates when needed."
            )
        jury_status_label.setText(status_text)
        for role, label in jury_role_labels.items():
            active = role in active_roles
            assignment = ""
            if role in primary_roles:
                position = primary_roles.index(role)
                assignment = (
                    "meaning evaluator" if position == 0 else
                    "independent verifier" if position == 1 else
                    "completeness challenge"
                )
            elif role == adjudicator_role:
                assignment = "conditional adjudicator"
            label.setText(
                f"{role.replace('_', ' ').title()} "
                f"({assignment or ('active' if active else 'inactive')}):"
            )
            label.setStyleSheet("" if active else "color: #777;")

    for combo in jury_combos.values():
        combo.currentTextChanged.connect(lambda _text: refresh_jury_status())

    # Heartbeat monitor settings
    heartbeat_timeout_spin = QSpinBox(dialog)
    heartbeat_timeout_spin.setRange(30, 21600)
    heartbeat_timeout_spin.setValue(cfg.get("heartbeat_timeout", 90))
    heartbeat_interval_spin = QSpinBox(dialog)
    heartbeat_interval_spin.setRange(5, 60)
    heartbeat_interval_spin.setValue(cfg.get("heartbeat_interval", 10))
    heartbeat_max_restarts_spin = QSpinBox(dialog)
    heartbeat_max_restarts_spin.setRange(1, 10)
    heartbeat_max_restarts_spin.setValue(cfg.get("heartbeat_max_restarts", 5))

    # Ollama options
    judge_num_ctx_spin = QSpinBox(dialog)
    judge_num_ctx_spin.setRange(512, 8192)
    judge_num_ctx_spin.setValue(cfg.get("ollama_options", {}).get("judge_num_ctx", 2048))
    judge_num_predict_spin = QSpinBox(dialog)
    judge_num_predict_spin.setRange(64, 4096)
    judge_num_predict_spin.setValue(cfg.get("ollama_options", {}).get("judge_num_predict", 256))

    ev = cfg.get("evaluator", "ai_evaluator")
    evaluator_combo.setCurrentIndex(0 if ev == "ai_evaluator" else (2 if ev == "ai_evaluator_semantic" else 1))
    strictness_combo.setCurrentText(cfg.get("grading_strictness", cfg.get("leniency", "balanced")))
    provider_strategy_combo.setCurrentText(cfg.get("provider_strategy", "free_first_ollama_fallback"))
    max_openrouter_spend_spin.setValue(float(cfg.get("max_openrouter_spend_usd_per_run", 0.0) or 0.0))
    if models:
        model_combo.setCurrentText(models[0])
    embedding_model_combo.setCurrentText(cfg.get("embedding_model", DEFAULT_CONFIG.get("embedding_model", "")))
    reasoning_model_combo.setCurrentText(cfg.get("reasoning_model", DEFAULT_CONFIG.get("reasoning_model", "")))
    accuracy_cfg = cfg.get("accuracy_policy", {})
    minimum_judge_confidence_spin.setValue(float(accuracy_cfg.get("minimum_judge_confidence", 0.90)))
    distinct_models_checkbox.setChecked(bool(accuracy_cfg.get("require_distinct_models", True)))
    key_auto_add_checkbox.setChecked(bool(cfg.get("answer_key_auto_add_proven_equivalents", True)))
    patient_ai_checkbox.setChecked(bool(cfg.get("patient_ai_mode", True)))
    audit_path_edit.setText(str(cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")))
    benchmark_path_edit.setText(str(cfg.get("teacher_benchmark_path", "teacher_benchmark.jsonl")))
    report_checkbox.setChecked(bool(cfg.get("generate_report", True)))
    batch_size = cfg.get("batch_size", 32)
    if isinstance(batch_size, str) and batch_size.lower() == "auto":
        batch_auto_checkbox.setChecked(True)
        batch_size_spin.setEnabled(False)
        batch_size_spin.setValue(32)
    else:
        batch_size_spin.setValue(int(batch_size) if isinstance(batch_size, int) and batch_size > 0 else 32)
    batch_auto_checkbox.stateChanged.connect(lambda s: batch_size_spin.setEnabled(s != Qt.Checked))
    for retired_widget in (
        evaluator_combo,
        model_combo,
        embedding_model_combo,
        reasoning_model_combo,
        audit_path_edit,
        benchmark_path_edit,
        batch_size_spin,
        batch_auto_checkbox,
    ):
        retired_widget.hide()

    # Set Grade Mode from config
    grading_mode_combo.setCurrentText(cfg.get("grading_mode", "Whole Form"))
    execution_mode_combo.setCurrentText(
        normalize_execution_mode(cfg.get("execution_mode", DEFAULT_EXECUTION_MODE))
    )

    execution_mode_combo.currentTextChanged.connect(refresh_jury_status)
    refresh_jury_status()

    ignore_cache_checkbox = QCheckBox("Always grade from fresh data (ignore previous-run cache)", dialog)
    ignore_cache_checkbox.setChecked(bool(cfg.get("ignore_grading_cache", True)))
    ignore_cache_checkbox.setToolTip(
        "Before every grading run, remove cached results, rubrics, embeddings, context, "
        "validation data, Recent Only history, and pending Answer Keys reviews. "
        "Caching is still allowed within that run."
    )
    truncate_checkbox = QCheckBox(
        "Truncate answer variants before grading (keep only teacher's first answer)", dialog
    )
    truncate_checkbox.setChecked(bool(cfg.get("truncate_answers_before_grading", False)))
    truncate_checkbox.setToolTip(
        "DESTRUCTIVE: When enabled, before grading each targeted form the system will remove all answer-key variants\n"
        "leaving only the first teacher-provided answer. Backups are created automatically before changes."
    )

    force_ai_checkbox = QCheckBox("Send every answer through the full AI jury", dialog)
    force_ai_checkbox.setChecked(bool(cfg.get("force_ai_jury_for_all_answers", True)))
    force_ai_checkbox.setToolTip(
        "Mistral NeMo evaluates meaning, Gemma verifies facts/mathematics, and Phi-4 "
        "challenges completeness. GPT-OSS adjudicates disagreements, ambiguity, invalid output, or low confidence."
    )

    global_form.addRow("Grade Mode:", grading_mode_combo)
    global_form.addRow("Execution Mode:", execution_mode_combo)
    global_form.addRow("Grading Strictness:", strictness_combo)
    global_form.addRow("Minimum Judge Confidence:", minimum_judge_confidence_spin)
    global_form.addRow("Acceptance Diversity:", distinct_models_checkbox)
    global_form.addRow("Answer-Key Automation:", key_auto_add_checkbox)
    global_form.addRow("Slow Model Handling:", patient_ai_checkbox)
    global_form.addRow("AI Evaluation:", force_ai_checkbox)
    global_form.addRow("Answer Processing:", dedup_checkbox)
    global_form.addRow("Cache Reuse:", ignore_cache_checkbox)
    global_form.addRow("Truncate Answers:", truncate_checkbox)
    global_form.addRow("Reports:", report_checkbox)
    global_form.addRow("Heartbeat Timeout:", heartbeat_timeout_spin)
    global_form.addRow("Heartbeat Interval:", heartbeat_interval_spin)
    global_form.addRow("Heartbeat Restarts:", heartbeat_max_restarts_spin)

    openrouter_form.addRow("Provider Strategy:", provider_strategy_combo)
    openrouter_form.addRow("Provider Priority:", provider_priority_edit)
    openrouter_form.addRow("AI Worker Threads:", openrouter_ai_worker_count_spin)
    openrouter_form.addRow("Provider Workers:", openrouter_worker_count_spin)
    openrouter_form.addRow("Answers per Judge Call:", openrouter_judge_answer_batch_size_spin)
    openrouter_form.addRow("Spend Cap ($):", max_openrouter_spend_spin)

    llamacpp_form.addRow("Provider Enabled:", llamacpp_enabled_checkbox)
    llamacpp_form.addRow("AI Worker Threads:", llamacpp_ai_worker_count_spin)
    llamacpp_form.addRow("Provider Workers:", llamacpp_worker_count_spin)
    llamacpp_form.addRow("Answers per Judge Call:", llamacpp_judge_answer_batch_size_spin)
    llamacpp_form.addRow("Server URL:", llamacpp_base_url_edit)
    llamacpp_form.addRow("Auto-start Server:", llamacpp_auto_start_checkbox)
    llamacpp_form.addRow("Server Executable:", llamacpp_server_exe_picker)
    llamacpp_form.addRow("Model Folder:", llamacpp_model_dir_picker)
    llamacpp_form.addRow("Context Size:", llamacpp_context_size_spin)
    llamacpp_form.addRow("GPU Layers:", llamacpp_gpu_layers_combo)
    llamacpp_form.addRow("Generation Threads:", llamacpp_threads_spin)
    llamacpp_form.addRow("Batch Threads:", llamacpp_threads_batch_spin)
    llamacpp_form.addRow("Server Batch Size:", llamacpp_server_batch_size_spin)
    llamacpp_form.addRow("Server Micro-batch:", llamacpp_server_ubatch_size_spin)
    llamacpp_form.addRow("Flash Attention:", llamacpp_flash_attn_combo)
    llamacpp_form.addRow("K Cache Type:", llamacpp_cache_type_k_combo)
    llamacpp_form.addRow("V Cache Type:", llamacpp_cache_type_v_combo)
    llamacpp_form.addRow("Parallel Slots:", llamacpp_parallel_spin)
    llamacpp_form.addRow("Memory Mapping:", llamacpp_mmap_checkbox)
    llamacpp_form.addRow("Chat Templates:", llamacpp_jinja_checkbox)
    llamacpp_form.addRow("Server Check:", llamacpp_require_server_checkbox)
    llamacpp_form.addRow("After Grading:", llamacpp_stop_after_grading_checkbox)
    llamacpp_form.addRow("On App Close:", llamacpp_stop_on_close_checkbox)
    for role in visible_settings_jury_roles:
        llamacpp_form.addRow(llamacpp_role_labels[role], llamacpp_role_combos[role])

    ollama_form.addRow("Model Choices:", model_status_label)
    for role in visible_settings_jury_roles:
        ollama_form.addRow(jury_role_labels[role], jury_combos[role])
    ollama_form.addRow("Jury Roles:", jury_status_label)
    ollama_form.addRow("AI Worker Threads:", ollama_ai_worker_count_spin)
    ollama_form.addRow("Provider Workers:", ollama_worker_count_spin)
    ollama_form.addRow("Answers per Judge Call:", ollama_judge_answer_batch_size_spin)
    ollama_form.addRow("OpenRouter Monitor Model:", supervisor_model_combo)
    ollama_form.addRow("Judge Context:", judge_num_ctx_spin)
    ollama_form.addRow("Judge Output Tokens:", judge_num_predict_spin)

    buttons = QWidget(dialog)
    b = QHBoxLayout(buttons)
    b.setContentsMargins(0, 0, 0, 0)
    save_btn = QPushButton("Save", dialog)
    cancel_btn = QPushButton("Cancel", dialog)
    clear_cache_btn = QPushButton("Clear Cache & Grading History", dialog)
    clear_cache_btn.setObjectName("Danger")

    def clear_cache_now():
        answer = QMessageBox.question(
            dialog,
            "Clear grading cache?",
            "This clears regenerated model/context caches, Recent Only grading history, and all "
            "pending Answer Keys review candidates. The next run will fetch and grade everything again. "
            "Credentials, teacher benchmarks, backups, configuration, and form lists are preserved.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            owner.clear_all_forms(confirm=False)
            result = clear_grading_cache(reset_history=True)
            megabytes = result["removed_bytes"] / (1024 * 1024)
            QMessageBox.information(
                dialog,
                "Cache cleared",
                f"Removed {result['removed_files']} cached files ({megabytes:.1f} MB). "
                f"Removed {result['review_records_removed']} pending review records. "
                "The next grading run will start completely fresh.",
            )
        except Exception as exc:
            QMessageBox.critical(dialog, "Could not clear cache", str(exc))

    clear_cache_btn.clicked.connect(clear_cache_now)
    save_btn.clicked.connect(dialog.accept)
    cancel_btn.clicked.connect(dialog.reject)
    b.addWidget(clear_cache_btn)
    b.addStretch()
    b.addWidget(save_btn)
    b.addWidget(cancel_btn)
    main_layout.addWidget(buttons)

    if dialog.exec() == QDialog.Accepted:
        # Read existing config first to preserve other fields
        config_data = {}
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}")

        # Update fields
        eval_text = evaluator_combo.currentText()
        if "Semantic Pipeline" in eval_text:
            config_data["evaluator"] = "ai_evaluator_semantic"
        elif "Basic" in eval_text:
            config_data["evaluator"] = "ai_evaluator"
        else:
            config_data["evaluator"] = "ai_evaluator_2"

        config_data["grading_strictness"] = strictness_combo.currentText()
        config_data["leniency"] = strictness_combo.currentText()
        config_data["provider_strategy"] = provider_strategy_combo.currentText()
        priority = [
            part.strip().lower()
            for part in provider_priority_edit.text().split(",")
            if part.strip().lower() in {"openrouter", "llamacpp", "ollama"}
        ]
        if priority:
            config_data["provider_priority"] = list(dict.fromkeys(priority))
        config_data["max_openrouter_spend_usd_per_run"] = float(max_openrouter_spend_spin.value())

        if model_combo.currentText():
            config_data["models"] = {"judge": [model_combo.currentText()]}
        if embedding_model_combo.currentText():
            config_data["embedding_model"] = embedding_model_combo.currentText()
        if reasoning_model_combo.currentText():
            config_data["reasoning_model"] = reasoning_model_combo.currentText()
        for obsolete_key in (
            "rubric_model", "validate_expected_answers", "expected_answer_validation_optional",
            "expected_answer_validator_model", "expected_answer_validator_fallback_model",
            "expected_answer_validator_timeout_seconds", "expected_answer_validator_fallback_timeout_seconds",
            "expected_answer_validator_connect_timeout_seconds", "expected_answer_validator_min_confidence",
            "use_validated_expected_for_grading", "auto_replace_invalid_expected",
            "invalid_expected_blocks_updates", "rubric_timeout_seconds",
        ):
            config_data.pop(obsolete_key, None)
        accuracy_policy = dict(config_data.get("accuracy_policy", {}))
        accuracy_policy.update({
            "enabled": True,
            "minimum_judge_confidence": float(minimum_judge_confidence_spin.value()),
            "required_accept_roles": ["semantic_judge", "factual_judge", "concept_judge"],
            "require_distinct_models": distinct_models_checkbox.isChecked(),
            "embeddings_can_accept": False,
            "ambiguous_outcome": "REVIEW",
        })
        config_data["accuracy_policy"] = accuracy_policy
        config_data["answer_key_auto_add_proven_equivalents"] = key_auto_add_checkbox.isChecked()
        config_data["ignore_grading_cache"] = ignore_cache_checkbox.isChecked()
        config_data["force_ai_jury_for_all_answers"] = force_ai_checkbox.isChecked()
        config_data["patient_ai_mode"] = patient_ai_checkbox.isChecked()
        config_data["enable_jury_circuit_breaker"] = not patient_ai_checkbox.isChecked()
        config_data["truncate_answers_before_grading"] = truncate_checkbox.isChecked()
        config_data["decision_audit_path"] = audit_path_edit.text().strip() or "logs/grading_decisions.jsonl"
        config_data["teacher_benchmark_path"] = benchmark_path_edit.text().strip() or "teacher_benchmark.jsonl"

        # Save jury model selections
        selected_jury = {}
        for role, combo in jury_combos.items():
            try:
                sel = combo.currentText()
            except Exception:
                sel = jury_defaults.get(role)
            if sel:
                selected_jury[role] = sel
        if selected_jury:
            config_data["jury_models"] = selected_jury

        config_data["generate_report"] = report_checkbox.isChecked()

        if batch_auto_checkbox.isChecked():
            config_data["batch_size"] = "auto"
        else:
            config_data["batch_size"] = int(batch_size_spin.value())

        config_data["grading_mode"] = grading_mode_combo.currentText()
        selected_mode = execution_mode_combo.currentText()
        config_data["execution_mode"] = selected_mode

        # Apply execution preset knobs that control concurrency/timeout behavior.
        preset = EXECUTION_MODE_PRESETS.get(selected_mode, EXECUTION_MODE_PRESETS[DEFAULT_EXECUTION_MODE])
        for key, value in preset.items():
            config_data[key] = value
        config_data["openrouter_ai_worker_count"] = int(openrouter_ai_worker_count_spin.value())
        config_data["llamacpp_ai_worker_count"] = 1
        config_data["ollama_ai_worker_count"] = int(ollama_ai_worker_count_spin.value())
        config_data["openrouter_worker_count"] = int(openrouter_worker_count_spin.value())
        config_data["llamacpp_worker_count"] = 1
        config_data["ollama_worker_count"] = int(ollama_worker_count_spin.value())
        config_data["llamacpp_enabled"] = llamacpp_enabled_checkbox.isChecked()
        config_data["llamacpp_require_server"] = llamacpp_require_server_checkbox.isChecked()
        config_data["llamacpp_auto_start_server"] = llamacpp_auto_start_checkbox.isChecked()
        config_data["llamacpp_server_executable"] = llamacpp_server_exe_edit.text().strip() or "llama-server.exe"
        config_data["llamacpp_stop_server_after_grading"] = llamacpp_stop_after_grading_checkbox.isChecked()
        config_data["llamacpp_stop_server_on_app_close"] = llamacpp_stop_on_close_checkbox.isChecked()
        config_data["llamacpp_api_base_url"] = llamacpp_base_url_edit.text().strip() or "http://127.0.0.1:8080"
        config_data["llamacpp_model_dir"] = llamacpp_model_dir_edit.text().strip() or r"C:\Users\regis\.lmstudio\models"
        gpu_layers_text = llamacpp_gpu_layers_combo.currentText().strip().lower() or "auto"
        if gpu_layers_text not in {"auto", "all"}:
            try:
                gpu_layers_text = str(max(0, int(gpu_layers_text)))
            except ValueError:
                gpu_layers_text = "auto"
        config_data["llamacpp_server_context_size"] = int(llamacpp_context_size_spin.value())
        config_data["llamacpp_server_gpu_layers"] = gpu_layers_text
        config_data["llamacpp_server_threads"] = int(llamacpp_threads_spin.value())
        config_data["llamacpp_server_threads_batch"] = int(llamacpp_threads_batch_spin.value())
        config_data["llamacpp_server_batch_size"] = int(llamacpp_server_batch_size_spin.value())
        config_data["llamacpp_server_ubatch_size"] = int(llamacpp_server_ubatch_size_spin.value())
        config_data["llamacpp_server_flash_attn"] = llamacpp_flash_attn_combo.currentText()
        config_data["llamacpp_server_cache_type_k"] = llamacpp_cache_type_k_combo.currentText()
        config_data["llamacpp_server_cache_type_v"] = llamacpp_cache_type_v_combo.currentText()
        config_data["llamacpp_server_parallel"] = int(llamacpp_parallel_spin.value())
        config_data["llamacpp_server_mmap"] = llamacpp_mmap_checkbox.isChecked()
        config_data["llamacpp_server_jinja"] = llamacpp_jinja_checkbox.isChecked()
        selected_llamacpp = {}
        for role, combo in llamacpp_role_combos.items():
            try:
                sel = combo.currentText().strip()
            except Exception:
                sel = ""
            if sel == "No llama.cpp GGUF models found":
                sel = ""
            selected_llamacpp[role] = [sel] if sel else []
        config_data["llamacpp_models"] = selected_llamacpp
        if supervisor_model_combo.currentText():
            config_data["openrouter_supervisor_ollama_model"] = supervisor_model_combo.currentText()
        # Keep provider-level capacity in ProviderManager; application workers may
        # process multiple questions while Ollama remains capped by ollama_worker_count.
        config_data["max_concurrent_judge_http"] = 1
        config_data["max_concurrent_jury_answers"] = max(
            1,
            effective_ai_worker_count(config_data),
        )
        config_data["enable_async_judges"] = False
        config_data["sync_judge_parallelism"] = 1
        # User-facing accuracy controls override the preset defaults.
        config_data["accuracy_policy"]["minimum_judge_confidence"] = float(minimum_judge_confidence_spin.value())
        config_data["accuracy_policy"]["require_distinct_models"] = distinct_models_checkbox.isChecked()
        if isinstance(config_data.get("adaptive_math_jury"), dict):
            config_data["adaptive_math_jury"]["minimum_primary_confidence"] = float(minimum_judge_confidence_spin.value())
        config_data["answer_key_auto_add_proven_equivalents"] = key_auto_add_checkbox.isChecked()
        config_data["ignore_grading_cache"] = ignore_cache_checkbox.isChecked()
        config_data["force_ai_jury_for_all_answers"] = force_ai_checkbox.isChecked()
        config_data["patient_ai_mode"] = patient_ai_checkbox.isChecked()
        config_data["enable_jury_circuit_breaker"] = not patient_ai_checkbox.isChecked()
        # Prevent stale mode-only knobs from previous selection.
        if "active_judge_roles" not in preset:
            config_data["active_judge_roles"] = [
                "semantic_judge",
                "factual_judge",
                "concept_judge",
                "strict_judge",
                "misconception_judge",
                "language_filter",
            ]
        if "judge_prewarm_enabled" not in preset:
            config_data["judge_prewarm_enabled"] = False

        config_data["enable_deduplication"] = dedup_checkbox.isChecked()
        config_data["ollama_judge_answer_batch_size"] = int(ollama_judge_answer_batch_size_spin.value())
        config_data["openrouter_judge_answer_batch_size"] = int(openrouter_judge_answer_batch_size_spin.value())
        config_data["llamacpp_judge_answer_batch_size"] = 1
        config_data["judge_answer_batch_size"] = int(openrouter_judge_answer_batch_size_spin.value())
        config_data["ai_worker_count"] = effective_ai_worker_count(config_data)
        if is_llamacpp_only(config_data):
            config_data["ai_worker_count"] = 1
            config_data["llamacpp_ai_worker_count"] = 1
            config_data["max_concurrent_jury_answers"] = 1

        # Save Heartbeat monitor settings
        config_data["heartbeat_timeout"] = heartbeat_timeout_spin.value()
        config_data["heartbeat_interval"] = heartbeat_interval_spin.value()
        config_data["heartbeat_max_restarts"] = heartbeat_max_restarts_spin.value()

        # Save Ollama options
        ollama_options = config_data.get("ollama_options", {})
        ollama_options["judge_num_ctx"] = judge_num_ctx_spin.value()
        ollama_options["judge_num_predict"] = judge_num_predict_spin.value()
        ollama_options.pop("rubric_num_ctx", None)
        ollama_options.pop("rubric_num_predict", None)
        config_data["ollama_options"] = ollama_options

        # Save the updated grading mode to self
        owner.grading_mode = config_data["grading_mode"]

        # Write config.json in a single atomic write operation
        try:
            with open("config.json", "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4)
            owner._sync_worker_cards_to_config()
        except Exception as e:
            QMessageBox.critical(owner, "Error Saving Settings", f"Failed to save settings: {str(e)}")

