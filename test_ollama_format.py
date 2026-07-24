"""Optional live Ollama format smoke test.

This file used to call local Ollama models during pytest collection, which made
the normal unit suite hang on machines without those exact models available.
Set RUN_LIVE_OLLAMA_TESTS=1 to run the integration smoke test explicitly.
"""

import asyncio
import json
import os

import aiohttp
import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_OLLAMA_TESTS") != "1",
    reason="live Ollama integration test; set RUN_LIVE_OLLAMA_TESTS=1 to run",
)


JUDGE_PROMPTS = {
    "factual_judge": (
        "You are a factual accuracy checker for science and mathematics. "
        "Return ONLY valid JSON."
    ),
}


def _make_judge_prompt(question, expected, answer, rubric):
    return (
        f"Question: {question}\nExpected: {expected}\nAnswer: {answer}\n\n"
        f"Rubric for reference (do not return this):\n{json.dumps(rubric)}\n\n"
        "Provide your evaluation as a JSON object with these fields."
    )


def _get_judge_format():
    return {
        "type": "object",
        "properties": {
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "decision": {"type": "string", "enum": ["YES", "NO", "ABSTAIN"]},
            "reason_short": {"type": "string"},
        },
        "required": ["confidence", "decision", "reason_short"],
        "additionalProperties": True,
    }


@pytest.mark.asyncio
async def test_ollama_format_live():
    question = "What is the derivative of x^2?"
    expected = "2x"
    answer = "The derivative is 2x."
    rubric = {"correct": "2x", "partial": ["2*x", "x*2"]}

    payload = {
        "model": os.environ.get("RUN_LIVE_OLLAMA_MODEL", "llama3.1:8b"),
        "messages": [
            {"role": "system", "content": JUDGE_PROMPTS["factual_judge"]},
            {"role": "user", "content": _make_judge_prompt(question, expected, answer, rubric)},
        ],
        "stream": False,
        "options": {"num_ctx": 2048},
        "format": _get_judge_format(),
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("http://localhost:11434/api/chat", json=payload, timeout=180) as resp:
            data = await resp.json()

    assert resp.status == 200
    content = data.get("message", {}).get("content", "")
    assert content


if __name__ == "__main__":
    asyncio.run(test_ollama_format_live())
