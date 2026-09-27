"""Token estimate for DAYANERA's local Qwen models, without loading a tokenizer.

Chunk limits are expressed in *model tokens*, because the model's context window
(``OLLAMA_NUM_CTX``) is what the retrieved passages must fit into. Loading the real
Qwen tokenizer at ingestion time would add a heavy dependency, so a linear model
over simple character classes is used instead. Its coefficients were fitted on
DAYANERA's own chunks against exact counts reported by the local Ollama
``qwen3:14b-q4_K_M`` (``prompt_eval_count`` of a raw prompt), see
``app/evaluation/token_calibration.py`` and ``docs/chunking/token_calibration.json``.

Engineering text tokenises worse than prose: Greek letters, subscripted symbols,
decimal commas and table pipes each cost a token or more, so a character-count
rule ("4 characters per token") underestimates formula and table chunks badly.
"""
from __future__ import annotations

import math
import re

TOKENIZER_REFERENCE = "qwen3:14b-q4_K_M (Ollama prompt_eval_count)"
# fitted coefficients (tokens per unit) against 214 DAYANERA chunks of all content types
# (docs/chunking/token_calibration.json): mean abs. error 5.4 %, p95 15 % (the "4 characters per token"
# rule: 23.5 % / 60 %, and it underestimates formula and table chunks by up to 2x)
COEF = {
    "word": 0.842,  # words of ASCII letters (common English words are ~1 token, rare ones 2-3)
    "long_word_chars": 0.276,  # extra characters of words longer than 7 letters
    "digit": 1.249,  # digits: Qwen splits numbers into single digits
    "non_ascii": 1.327,  # Greek letters, math symbols, dashes, ° ...
    "punct": 0.987,  # ASCII punctuation (, . ; : | ( ) = + - / ...)
    "newline": 1.108,
}
INTERCEPT = 0.217
_WORD = re.compile(r"[A-Za-z]+")



def estimate_tokens(text: str) -> int:
    """Estimated Qwen token count of ``text`` (deterministic, rounded up)."""
    if not text:
        return 0
    words = _WORD.findall(text)
    long_chars = sum(len(w) - 7 for w in words if len(w) > 7)
    digits = sum(c.isdigit() for c in text)
    non_ascii = sum(ord(c) > 127 for c in text if not c.isspace())
    punct = sum(1 for c in text if ord(c) < 128 and not c.isalnum() and not c.isspace())
    newlines = text.count("\n")
    est = (INTERCEPT + COEF["word"] * len(words) + COEF["long_word_chars"] * long_chars + COEF["digit"] * digits
           + COEF["non_ascii"] * non_ascii + COEF["punct"] * punct + COEF["newline"] * newlines)
    return max(1, math.ceil(est))


def features(text: str) -> dict[str, float]:
    """The regression features (used by the calibration script)."""
    words = _WORD.findall(text)
    return {"word": len(words), "long_word_chars": sum(len(w) - 7 for w in words if len(w) > 7),
            "digit": sum(c.isdigit() for c in text), "non_ascii": sum(ord(c) > 127 for c in text if not c.isspace()),
            "punct": sum(1 for c in text if ord(c) < 128 and not c.isalnum() and not c.isspace()),
            "newline": text.count("\n")}
