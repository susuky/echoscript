from __future__ import annotations


def _count(text: str, tokenizer, *, leading_space: bool = False) -> tuple[int, str]:
    if not text:
        return 0, "model_tokenizer"
    value = (" " if leading_space else "") + text
    if tokenizer is not None:
        try:
            encoded = tokenizer.encode(value, add_special_tokens=False)
            tokens = encoded if isinstance(encoded, (list, tuple)) else encoded.ids
            return len(tokens), "model_tokenizer"
        except (AttributeError, TypeError, ValueError):
            pass
    # Both backends use byte-level tokenizers. A byte count is conservative when
    # a tokenizer is unavailable; never label this estimate as measured tokens.
    return len(value.encode("utf-8")), "utf8_byte_upper_bound"


def bounded_text(text: str, budget: int, tokenizer=None, *, tail: bool = False,
                 leading_space: bool = False) -> tuple[str, dict]:
    text = text.strip()
    original, counter = _count(text, tokenizer, leading_space=leading_space)
    if original <= budget:
        accepted = text
    else:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = text[-middle:] if tail else text[:middle]
            if _count(candidate, tokenizer, leading_space=leading_space)[0] <= budget:
                low = middle
            else:
                high = middle - 1
        accepted = (text[-low:] if tail else text[:low]).strip() if low else ""
    count, _ = _count(accepted, tokenizer, leading_space=leading_space)
    return accepted, {"budget": budget, "input_tokens": original, "used_tokens": count,
                      "truncated": accepted != text, "counter": counter}


def prepare_prompts(backend: str, *, context: str, glossary: str, previous_text: str,
                    tokenizer=None, context_token_budget: int | None = None,
                    glossary_token_budget: int | None = None) -> tuple[str, str, dict]:
    whisper = backend == "faster-whisper"
    total = 192 if whisper else 1024
    overhead = 16 if whisper else 48
    glossary_limit = 32 if whisper else 256
    context_limit = 96 if whisper else 512
    for value in (context_token_budget, glossary_token_budget):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("Prompt token budgets must be nonnegative integers")
    glossary_limit = min(glossary_limit if glossary_token_budget is None else glossary_token_budget,
                         32 if whisper else 256)
    context_limit = min(context_limit if context_token_budget is None else context_token_budget,
                        total - glossary_limit - overhead)
    glossary, glossary_info = bounded_text(glossary, glossary_limit, tokenizer, leading_space=whisper)
    context, context_info = bounded_text(context, context_limit, tokenizer, leading_space=whisper)
    previous_text, previous_info = bounded_text(
        previous_text, min(64 if whisper else 256, total - context_info["used_tokens"]
                           - glossary_info["used_tokens"] - overhead), tokenizer, tail=True,
        leading_space=whisper,
    )
    if whisper:
        initial_prompt = "\n".join(part for part in (context, previous_text) if part)
        initial_prompt, combined = bounded_text(
            initial_prompt, total - glossary_info["used_tokens"], tokenizer, leading_space=True,
        )
        hotwords = glossary
    else:
        parts = [context]
        if glossary:
            parts.append("Vocabulary: " + glossary)
        if previous_text:
            parts.append("Previous transcript: " + previous_text)
        initial_prompt, combined = bounded_text("\n".join(part for part in parts if part), total, tokenizer)
        hotwords = ""
    return initial_prompt, hotwords, {
        "backend": backend, "input_budget": total, "context": context_info,
        "glossary": glossary_info, "previous_text": previous_info,
        "combined": combined,
    }
