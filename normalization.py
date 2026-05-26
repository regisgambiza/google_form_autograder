import re
import unicodedata
from collections import OrderedDict, defaultdict
from typing import Callable, Dict, List, Tuple

CONTRACTIONS = {
    "don't": "do not",
    "doesn't": "does not",
    "can't": "cannot",
    "it's": "it is",
    "i'm": "i am",
    "isn't": "is not",
    "aren't": "are not",
}
TYPO_FIXES = {"recieve": "receive", "definately": "definitely", "teh": "the", "dont": "do not"}
ARTICLES = {"a", "an", "the"}
FILLERS = ["i think that", "i believe", "in my opinion"]


def normalize(text: str) -> str:
    """Normalize text for robust matching across ESL/typo variants."""
    s = unicodedata.normalize("NFC", str(text or "")).lower().strip()
    for c, e in CONTRACTIONS.items():
        s = re.sub(rf"\b{re.escape(c)}\b", e, s)
    for typo, fix in TYPO_FIXES.items():
        s = re.sub(rf"\b{re.escape(typo)}\b", fix, s)
    for filler in FILLERS:
        s = re.sub(rf"^\s*{re.escape(filler)}\s+", "", s)
    s = re.sub(r"[^\w\s+\-*/=^%/.<>]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [t for t in s.split() if t not in ARTICLES]
    if tokens and tokens[0] in {"is", "are", "was", "were"}:
        tokens = ["it"] + tokens
    if len(tokens) >= 2 and tokens[1] not in {"is", "are", "was", "were"} and tokens[0] not in {"it", "this", "that"}:
        tokens.insert(1, "is")
    return " ".join(tokens)


def semantic_deduplicate(answers: List[str], normalize_fn: Callable[[str], str] = normalize) -> Tuple[List[str], Dict[str, List[str]]]:
    """Group answers by normalized representation, keeping a stable representative."""
    groups: Dict[str, List[str]] = defaultdict(list)
    reps: "OrderedDict[str, str]" = OrderedDict()
    for ans in answers:
        key = normalize_fn(ans)
        groups[key].append(ans)
        if key not in reps:
            reps[key] = ans
    unique = list(reps.values())
    mapping = {rep: groups[key] for key, rep in reps.items()}
    return unique, mapping
