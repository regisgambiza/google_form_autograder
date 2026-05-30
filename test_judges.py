import json
import ollama
from ai_judges import run_judges
from rubric_generator import generate_rubric

question = "What is photosynthesis?"
expected = "Plants make food using sunlight, water, and carbon dioxide."
answer = "plants use sun to make food"

rubric = generate_rubric(question, expected)
print("Rubric:")
print(json.dumps(rubric, indent=2))

print("\nRunning judges...")
res = run_judges(answer, question, expected, rubric)
print("\nJudges output:")
for r in res:
    print(r)
