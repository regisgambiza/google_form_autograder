import asyncio
import json
import re
from typing import Dict, List

try:
    import aiohttp
except Exception:
    aiohttp = None
import ollama

from evaluator_config import load_config
from logger import log

JUDGE_PROMPTS = {
    "semantic_judge": "You are a semantic meaning evaluator. Your ONLY job is to determine whether the student's answer conveys the same MEANING as the expected answer, regardless of wording, grammar, or spelling. Ignore surface form completely. Focus only on whether the core idea is the same.",
    "concept_judge": "You are a concept coverage checker. Given the required concepts for a correct answer, determine what percentage of them appear in the student's answer (even if expressed differently). Return a coverage score from 0.0 to 1.0.",
    "factual_judge": "You are a factual accuracy checker for science and mathematics. Determine whether the student's answer is scientifically or mathematically correct, ignoring grammar and spelling. Flag anything factually wrong even if it sounds similar to the correct answer.",
    "strict_judge": "You are a strict but fair human examiner. Grade as you would in a real classroom. Do not accept vague or incomplete answers. Require the student to have demonstrated genuine understanding, not just a lucky guess.",
    "misconception_judge": "You are a misconception analyst. Your job is to detect whether the student's answer reveals a fundamental conceptual misunderstanding, even if parts of the answer sound correct on the surface. A misconception should lower the score significantly.",
    "language_filter": "You are a language quality assessor for ESL and Thai learner answers. Your job is to separate language errors (grammar, spelling, word order) from content errors. Report how much of the answer's incorrectness is due to language issues vs actual wrong content.",
}
REQUIRED_FIELDS = ["semantic_similarity", "concept_coverage", "factual_accuracy", "misconception_detected", "misconception_description", "language_noise_ratio", "confidence", "decision", "reason_short"]


def _abstain(reason: str = "judge unavailable") -> Dict[str, object]:
    return {"semantic_similarity": 0.0, "concept_coverage": 0.0, "factual_accuracy": 0.0, "misconception_detected": False, "misconception_description": "", "language_noise_ratio": 0.0, "confidence": 0.0, "decision": "ABSTAIN", "reason_short": reason}


def _extract_json_object(raw: str) -> Dict[str, object]:
    clean = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.IGNORECASE | re.DOTALL).strip()
    clean = re.sub(r"^```(?:json)?\\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\\s*```$", "", clean)
    try:
        return json.loads(clean)
    except Exception:
        decoder = json.JSONDecoder()
        idx = clean.find("{")
        while idx >= 0:
            try:
                obj, _ = decoder.raw_decode(clean[idx:])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
            idx = clean.find("{", idx + 1)
        m = re.search(r"\\{.*?\\}", clean, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _normalize_decision(d: Dict[str, object]) -> Dict[str, object]:
    dec = str(d.get("decision", "ABSTAIN")).strip().upper()
    d["decision"] = "YES" if dec in {"YES", "TRUE", "CORRECT", "PASS"} else ("NO" if dec in {"NO", "FALSE", "INCORRECT", "FAIL", "WRONG"} else "ABSTAIN")
    return d


def _valid(d: Dict[str, object]) -> bool:
    return all(k in d for k in REQUIRED_FIELDS) and str(d.get("decision")) in {"YES", "NO", "ABSTAIN"}


async def call_judge_async(session, model: str, role: str, answer: str, question: str, expected: str, rubric: Dict[str, object], retries: int, num_ctx: int) -> Dict[str, object]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JUDGE_PROMPTS[role]},
            {"role": "user", "content": f"Question: {question}\\nExpected: {expected}\\nAnswer: {answer}\\nRubric: {json.dumps(rubric)}\\nReturn strict JSON only."}
        ],
        "stream": False,
        "options": {"num_ctx": num_ctx},
    }
    for i in range(retries):
        try:
            async with session.post("http://localhost:11434/api/chat", json=payload, timeout=120) as resp:
                data = await resp.json()
            obj = _normalize_decision(_extract_json_object(data.get("message", {}).get("content", "")))
            for k in REQUIRED_FIELDS:
                obj.setdefault(k, _abstain("partial")[k])
            if _valid(obj):
                return obj
        except Exception as ex:
            log("WARNING", f"Judge {role} attempt {i+1}/{retries} failed: {ex}")
    return _abstain("retries_exhausted")


async def run_all_judges_with_early_exit(answer: str, question: str, expected: str, rubric: Dict[str, object], retries: int = 3) -> List[Dict[str, object]]:
    cfg = load_config()
    model = cfg.get("jury_models", {}).get("semantic_judge", "qwen2.5:7b")
    num_ctx = int(cfg.get("ollama_options", {}).get("judge_num_ctx", 2048))
    ee = cfg.get("early_exit", {})
    min_judges = int(ee.get("min_judges", 3))
    agree_thresh = float(ee.get("agreement_confidence", 0.90))
    enabled = bool(ee.get("enabled", True))

    if aiohttp is None:
        log("WARNING", "aiohttp not installed; falling back to synchronous judge calls")
        out: List[Dict[str, object]] = []
        for role in JUDGE_PROMPTS:
            obj = _abstain("retries_exhausted")
            for i in range(retries):
                try:
                    raw = ollama.chat(
                        model=model,
                        options={"num_ctx": num_ctx},
                        messages=[
                            {"role": "system", "content": JUDGE_PROMPTS[role]},
                            {"role": "user", "content": f"Question: {question}\\nExpected: {expected}\\nAnswer: {answer}\\nRubric: {json.dumps(rubric)}\\nReturn strict JSON only."},
                        ],
                    )["message"]["content"]
                    candidate = _normalize_decision(_extract_json_object(raw))
                    for k in REQUIRED_FIELDS:
                        candidate.setdefault(k, _abstain("partial")[k])
                    if _valid(candidate):
                        obj = candidate
                        break
                except Exception as ex:
                    log("WARNING", f"Judge {role} sync attempt {i+1}/{retries} failed: {ex}")
            out.append(obj)
        return out

    tasks = {}
    async with aiohttp.ClientSession() as session:
        for role in JUDGE_PROMPTS:
            tasks[asyncio.create_task(call_judge_async(session, model, role, answer, question, expected, rubric, retries, num_ctx))] = role
        results: List[Dict[str, object]] = []
        for done in asyncio.as_completed(tasks):
            r = await done
            results.append(r)
            if enabled and len(results) >= min_judges:
                decisions = [x.get("decision") for x in results]
                confs = [float(x.get("confidence", 0.0)) for x in results]
                avg_conf = (sum(confs) / len(confs)) if confs else 0.0
                if len(set(decisions)) == 1 and avg_conf >= agree_thresh:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    log("DEBUG", f"Early exit after {len(results)} judges - unanimous {decisions[0]} @ {avg_conf:.2f}")
                    break
        return results


def run_judges(answer: str, question: str, expected: str, rubric: Dict[str, object], retries: int = 3) -> List[Dict[str, object]]:
    return asyncio.run(run_all_judges_with_early_exit(answer, question, expected, rubric, retries))
