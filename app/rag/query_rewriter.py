"""Rule-based query rewriting (no LLM call; Groq lives in Express).

Expands campus shorthand and, for very short follow-ups, prepends the previous user message so
"what about sem 6?" still retrieves relevant chunks."""
import re

EXPANSIONS = {
    r"\bpyqs?\b": "previous year question paper",
    r"\bsem\b": "semester",
    r"\bendsem\b": "end semester exam",
    r"\bmidsem\b": "mid semester exam",
    r"\bcgpa\b": "CGPA grade point",
    r"\bnitrr\b": "NIT Raipur",
    r"\btt\b": "timetable",
    r"\bpyp\b": "previous year paper",
}
_FOLLOWUP = re.compile(r"\b(it|that|this|those|them|same|also|what about|and for|there)\b", re.I)


def rewrite_query(query: str, history: list[str] | None = None) -> str:
    q = " ".join(query.split())
    for pat, repl in EXPANSIONS.items():
        q = re.sub(pat, lambda m, r=repl: f"{m.group(0)} {r}" if m.group(0).lower() != r.lower() else m.group(0),
                   q, flags=re.I)
    history = [h for h in (history or []) if h and h.strip()]
    if history and (len(q.split()) <= 6 or _FOLLOWUP.search(q)):
        prev = " ".join(history[-1].split())[:300]
        if prev.lower() not in q.lower():
            q = f"{prev} {q}"
    return q[:1000]
