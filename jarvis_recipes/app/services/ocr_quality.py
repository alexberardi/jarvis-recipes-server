import re
from typing import Dict


def _gibberish_flags(text: str) -> Dict[str, float | bool]:
    alpha_chars = len(re.findall(r"[A-Za-z]", text))
    total_chars = len(text)
    alpha_ratio = alpha_chars / total_chars if total_chars else 0.0

    tokens = re.findall(r"[A-Za-z]{2,}", text)
    token_chars = sum(len(t) for t in tokens) or 1
    vowels = sum(len(re.findall(r"[AEIOUaeiou]", t)) for t in tokens)
    vowel_ratio = vowels / token_chars
    vowelful_tokens = sum(1 for t in tokens if re.search(r"[AEIOUaeiou]", t))

    # Heuristic: tighten thresholds to reject nonsensical OCR blobs
    is_gibberish = (
        alpha_ratio < 0.65
        or vowel_ratio < 0.30
        or vowelful_tokens < 20
        or len(tokens) < 50
    )
    return {
        "alpha_ratio": alpha_ratio,
        "vowel_ratio": vowel_ratio,
        "token_count": len(tokens),
        "vowelful_tokens": vowelful_tokens,
        "is_gibberish": is_gibberish,
    }


def score_quality(text: str, mean_confidence: float | None) -> Dict[str, int | bool | float]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    char_count = len(text)
    line_count = len(lines)

    hard_fail = char_count < 500 or line_count < 10
    gib = _gibberish_flags(text)

    score = 0
    if mean_confidence is not None and mean_confidence >= 50:
        score += 1

    keywords = {"ingredients", "directions", "instructions", "method", "serves", "yield"}
    if sum(1 for ln in lines if any(kw in ln.lower() for kw in keywords)) >= 2:
        score += 1

    ingredient_like = any(re.search(r"^\s*[\d\-\/\.\s]+[a-zA-Z]?", ln) for ln in lines)
    if ingredient_like:
        score += 1

    step_like = any(re.match(r"^\s*\d+[\).\s]", ln) for ln in lines)
    if step_like:
        score += 1

    return {
        "char_count": char_count,
        "line_count": line_count,
        "hard_fail": hard_fail or gib["is_gibberish"],
        "gibberish": gib["is_gibberish"],
        "alpha_ratio": gib["alpha_ratio"],
        "vowel_ratio": gib["vowel_ratio"],
        "token_count": gib["token_count"],
        "vowelful_tokens": gib["vowelful_tokens"],
        "score": score,
        "pass_gate": (not hard_fail) and score >= 2 and not gib["is_gibberish"],
        "warnings": [],
    }

