"""Test Ollama format parameter directly to isolate the issue."""
import asyncio
import json
import aiohttp
import pytest

JUDGE_PROMPTS = {
    "factual_judge": "You are a factual accuracy checker for science and mathematics. Determine whether the student's answer is scientifically or mathematically correct, ignoring grammar and spelling. Flag anything factually wrong even if it sounds similar to the correct answer.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
}

def _make_judge_prompt(question, expected, answer, rubric):
    return (
        f"Question: {question}\nExpected: {expected}\nAnswer: {answer}\n\n"
        f"Rubric for reference (do not return this):\n{json.dumps(rubric)}\n\n"
        "Provide your evaluation as a JSON object with these fields:"
    )


def _get_judge_format(role):
    return {
        "type": "object",
        "properties": {
            "semantic_similarity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "concept_coverage": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "factual_accuracy": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "misconception_detected": {"type": "boolean"},
            "misconception_description": {"type": "string"},
            "language_noise_ratio": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "decision": {"type": "string", "enum": ["YES", "NO", "ABSTAIN"]},
            "reason_short": {"type": "string"}
        },
        "required": [
            "semantic_similarity", "concept_coverage", "factual_accuracy",
            "misconception_detected", "misconception_description",
            "language_noise_ratio", "confidence", "decision", "reason_short"
        ],
        "additionalProperties": False
    }


@pytest.mark.asyncio
async def test_ollama_format():
    """Test Ollama with format parameter directly."""
    question = "What is the derivative of x^2?"
    expected = "2x"
    answer = "The derivative is 2x."
    rubric = {"correct": "2x", "partial": ["2*x", "x*2"]}
    
    test_cases = [
        ("llama3.1:8b", 2048),
        ("llama3.1:8b", 4096),
        ("gemma3:12b", 2048),
        ("deepseek-r1:8b", 2048),
    ]
    
    async with aiohttp.ClientSession() as session:
        for model, num_ctx in test_cases:
            print(f"\n{'='*60}")
            print(f"Testing: {model} with num_ctx={num_ctx}")
            print('='*60)
            
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": JUDGE_PROMPTS["factual_judge"]},
                    {"role": "user", "content": _make_judge_prompt(question, expected, answer, rubric)}
                ],
                "stream": False,
                "options": {"num_ctx": num_ctx},
                "format": _get_judge_format("factual_judge"),
            }
            
            try:
                async with session.post("http://localhost:11434/api/chat", json=payload, timeout=180) as resp:
                    data = await resp.json()
                    
                print(f"Response status: {resp.status}")
                print(f"Full response keys: {data.keys()}")
                print(f"Message keys: {data.get('message', {}).keys()}")
                
                content = data.get("message", {}).get("content", "")
                print(f"Content type: {type(content)}")
                print(f"Content length: {len(content)}")
                print(f"Content (first 500 chars): {repr(content)[:500]}")
                
                if "error" in data:
                    print(f"ERROR from Ollama: {data['error']}")
                    
            except Exception as e:
                print(f"Exception: {e}")


if __name__ == "__main__":
    asyncio.run(test_ollama_format())
