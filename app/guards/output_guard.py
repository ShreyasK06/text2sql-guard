"""Output guard: column masking, value-pattern scrub, prompt-injection scan, truncation.

Never blocks. Called by app/pipeline.py after execution. Layer name: "output_guard".
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, datetime

from app.guards.policy import Policy, RolePolicy
from app.types import Decision, GuardResult, OutputOutcome

LAYER = "output_guard"
INJECTION_TOKEN = "[REDACTED: possible prompt injection]"

# ---------------------------------------------------------------- mask_value


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value))


def mask_value(value: object, kind: str) -> str | None:
    """Mask one value according to its kind. Never raises; odd input falls back to '***'."""
    if value is None:
        return None
    try:
        if kind == "email":
            s = str(value)
            if "@" in s and s.index("@") > 0:
                local, _, domain = s.partition("@")
                return local[0] + "***@" + domain
            return "***"
        if kind == "phone":
            d = _digits(value)
            return "***-***-" + d[-4:] if len(d) >= 4 else "***"
        if kind == "ssn":
            d = _digits(value)
            return "***-**-" + d[-4:] if len(d) >= 4 else "***"
        if kind == "card":
            d = _digits(value)
            return "**** **** **** " + d[-4:] if len(d) >= 4 else "***"
        if kind == "name":
            parts = [p for p in re.split(r"[\s.]+", str(value)) if p and p[0].isalpha()]
            return " ".join(p[0].upper() + "." for p in parts) if parts else "***"
        if kind == "date":
            if isinstance(value, (date, datetime)):
                return f"{value.year:04d}-**-**"
            m = re.match(r"\s*(\d{4})-\d{2}-\d{2}", str(value))
            return f"{m.group(1)}-**-**" if m else "***"
    except Exception:
        return "***"
    return "***"  # number / text / secret / unknown


# ------------------------------------------------------------- value scrub
_SSN_RE = re.compile(
    r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])|(?<![\d-])(?<!\d[ .])\d{3}[ .]\d{2}[ .]\d{4}(?!\d)"
)
_CARD_RE = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_PHONE_RE = re.compile(
    r"(?<![\w-])(?:\+1[ .-]?|1[ .-])?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\d-])"
)


def _luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = ord(ch) - 48
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _card_sub(m: re.Match) -> str:
    return "[CARD REDACTED]" if _luhn_ok(re.sub(r"\D", "", m.group(0))) else m.group(0)


def _scrub(text: str, patterns: frozenset[str]) -> str:
    if "card" in patterns:
        text = _CARD_RE.sub(_card_sub, text)
    if "ssn" in patterns:
        text = _SSN_RE.sub("[SSN REDACTED]", text)
    if "email" in patterns and "@" in text:
        text = _EMAIL_RE.sub("[EMAIL REDACTED]", text)
    if "phone" in patterns:
        text = _PHONE_RE.sub("[PHONE REDACTED]", text)
    return text


# --------------------------------------------------------- injection scan
_S = r"[\W_]+"  # separator tolerant of extra whitespace and punctuation
_DET = r"(?:all|any|the|your|of|these|those|every)"

# Cell is replaced when the summed weight of distinct matching patterns reaches _THRESHOLD.
# Normalised patterns run on a lower-cased, NFKC-normalised, leetspeak-folded copy of the cell.
_THRESHOLD = 3
_NORM_PATTERNS: list[tuple[re.Pattern, int]] = [
    # instruction-override phrasing
    (re.compile(
        rf"\b(?:ignore|disregard|forget|override|bypass|discard)\b(?:{_S}{_DET})*{_S}"
        rf"(?:previous|prior|above|earlier|preceding|system|safety|original|initial)(?:{_S}\w+)?{_S}"
        r"(?:instructions?|rules?|prompts?|directions?|guidelines?|context|restrictions?|constraints?|polic(?:y|ies))\b"), 3),
    (re.compile(rf"\bnew{_S}(?:system{_S})?(?:instructions?|rules?|task|prompt)[\W_]*:"), 3),
    (re.compile(rf"\byou{_S}are{_S}now\b"), 2),
    (re.compile(rf"\bfrom{_S}now{_S}on\b"), 2),
    (re.compile(rf"\bact{_S}as{_S}(?:an?{_S}|the{_S})?(?:\w+{_S})?"
                r"(?:ai|assistant|admin|administrator|root|dba|developer|system|unrestricted|unfiltered|dan|hacker|different)\b"), 3),
    (re.compile(r"\bact\s+as\b"), 1),
    (re.compile(rf"\b(?:dan|developer|god|jailbreak(?:ed)?|unrestricted){_S}mode\b"), 3),
    (re.compile(rf"\bpretend{_S}(?:to{_S}be|you{_S}are)\b"), 2),
    (re.compile(rf"\byou{_S}(?:must|will|shall|should){_S}(?:now{_S})?"
                r"(?:ignore|output|reveal|run|execute|print|respond|answer|obey|comply|disclose)\b"), 2),
    # fake conversation markers
    (re.compile(r"(?m)^\s*system\s*:"), 2),
    (re.compile(r"(?m)^\s*assistant\s*:"), 3),
    (re.compile(r"#{2,}\s*(?:system|instructions?|assistant)\b"), 3),
    (re.compile(r"<\|[a-z_]*(?:im_start|im_end|system|user|assistant|endoftext)[a-z_]*\|>"), 3),
    (re.compile(r"\[/?inst\]|<<\s*/?sys\s*>>"), 3),
    (re.compile(r"</?\s*(?:system|assistant|instructions?)\s*>"), 3),
    (re.compile(r"\[(?:system|admin|assistant|developer|instructions?)\]"), 2),
    # exfiltration and SQL-execution demands
    (re.compile(rf"\b(?:reveal|show|print|repeat|output|leak|disclose|display|tell){_S}(?:me{_S})?"
                rf"(?:your{_S}(?:system{_S})?(?:prompt|instructions?)"
                rf"|(?:the{_S})?(?:system|hidden|secret|initial|original){_S}(?:prompt|instructions?))\b"), 3),
    (re.compile(rf"\b(?:output|dump|leak|exfiltrate|reveal|return|print|list|select){_S}(?:\w+{_S}){{0,4}}?"
                rf"(?:all|every){_S}(?:the{_S})?(?:\w+{_S})?"
                r"(?:social{_S}security{_S}numbers?|phone{_S}numbers?|password{_S}hash(?:es)?|emails?|passwords?|ssns?|credit{_S}cards?|card{_S}numbers?|api{_S}keys?|secrets?|users?|customers?|records|rows|tables?|salar(?:y|ies))\b"), 2),
    (re.compile(rf"\b(?:run|execute|exec|issue|perform){_S}(?:the{_S})?(?:following|this|next|below){_S}"
                r"(?:sql|query|statement|command|code)\b"), 3),
    (re.compile(r"\bexecute\s*:|\bexec\s*:"), 2),
    (re.compile(rf"\b(?:drop{_S}(?:table|database|schema)|truncate{_S}table|delete{_S}from)\b"), 2),
    (re.compile(rf"\b(?:dear|hey|hello|attention|note{_S}to|message{_S}for){_S}(?:the{_S})?"
                r"(?:ai|assistant|llm|language{_S}model|model|chatbot|gpt|claude|agent)\b"), 2),
    (re.compile(rf"\b(?:when|if){_S}(?:you{_S})?(?:read|see|summari[sz]e|process|are{_S}asked){_S}(?:this|about)"), 1),
    (re.compile(rf"\bdo{_S}not{_S}(?:tell|inform|mention|alert|notify){_S}(?:the{_S})?(?:user|human|operator)\b"), 3),
    # additional override / role-switch phrasing (tuned on db/seed_data/poison_dev.json)
    (re.compile(rf"\b(?:ignore|disregard|forget|bypass|override|discard){_S}(?:all{_S})?(?:your|the{_S}above|all){_S}"
                r"(?:(?:safety|security|schema|column|previous|prior){_S})?"
                r"(?:rules?|polic(?:y|ies)|guardrails?|instructions?|restrictions?|constraints?|permissions?|guidelines?|guardrails)\b"), 3),
    (re.compile(rf"\bignore{_S}the{_S}above\b"), 2),
    (re.compile(rf"\b(?:skip|bypass|disable|turn{_S}off|circumvent){_S}(?:the{_S})?(?:permission|security|safety|column|access|row){_S}"
                r"(?:checks?|restrictions?|controls?|rules?|level{_S}security)\b"), 3),
    (re.compile(rf"\byou{_S}are{_S}(?:no{_S}longer|dan\b)"), 3),
    (re.compile(rf"\b(?:with|has|have|having|and){_S}no{_S}(?:rules|restrictions|limits|guardrails|filters)\b"), 2),
    (re.compile(rf"\bnew{_S}(?:system{_S})?prompt\b|\bnew{_S}task{_S}for{_S}(?:the{_S})?(?:model|assistant|ai|llm)\b"), 3),
    (re.compile(rf"\b(?:to|for){_S}the{_S}(?:ai|assistant|llm|model){_S}(?:reader|summari[sz]\w*|reading|processing)\b"), 3),
    (re.compile(rf"\b(?:automated{_S}systems?|ai{_S}(?:agents?|systems?)|llm{_S}agents?)\b"), 2),
    (re.compile(rf"\b(?:hi|hello|hey|dear){_S}(?:the{_S})?(?:ai|assistant|llm|chatbot)\b"), 2),
    (re.compile(rf"\bjust{_S}this{_S}once\b|\brespond{_S}only{_S}with\b|\bincluding{_S}(?:the{_S})?(?:password{_S}hash|ssn|api{_S}key)"), 1),
    (re.compile(r"\b(?:ssn|password_hash|card_number|api_keys)\b"), 1),
    (re.compile(rf"\b(?:show|print|output|reveal|list|dump|export|append|paste){_S}(?:me{_S})?"
                rf"(?:(?:the|all|every|full|unmasked|raw|contents|of){_S})*"
                r"(?:ssns?|social{_S}security|password{_S}hash(?:es)?|api{_S}keys?|card{_S}numbers?|users?{_S}table|unmasked{_S}\w+|emails?{_S}and{_S}phone)\b"), 2),
    (re.compile(rf"\b(?:run|execute|exec|paste){_S}?:?\s*(?:select|delete|drop|update|insert|truncate)\b"), 3),
    (re.compile(rf"\b(?:translate|convert){_S}the{_S}following{_S}(?:to|into){_S}sql\b"), 2),
    (re.compile(rf"\bselect\b[^;]{{0,80}}\bfrom{_S}(?:users|api_keys|payments|employees)\b"), 2),
    (re.compile(rf"\btruncate{_S}\w+{_S}cascade\b"), 2),
    # markdown data exfiltration
    (re.compile(r"!\[[^\]]*\]\(https?://[^)\s]*[?&][^)\s]*\)"), 2),
]
# Case-sensitive patterns run on the raw (NFKC) text.
_RAW_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"(?m)^\s*(?:SYSTEM|ASSISTANT|INSTRUCTIONS?)\s*:|(?:SYSTEM|ASSISTANT)\s*:"), 3),
    (re.compile(r"\bDROP\s+(?:TABLE|SCHEMA|DATABASE)\b|\bTRUNCATE\s+TABLE\b|\bDELETE\s+FROM\b"), 3),
    (re.compile(r"\bIGNORE\b[^.\n]{0,30}\bINSTRUCTIONS\b"), 3),
]
_HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
_COMMENT_CUES = re.compile(
    r"ignore|instruction|assistant|system|execute|sql|prompt|reveal|you\s+must|output|password|select\s", re.I
)
_INVISIBLE = re.compile("[​-‏⁠﻿‪-‮]")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_LETTER = re.compile(r"[A-Za-z]")
_SPACED = re.compile(r"(?<!\S)(?:[a-z] ){2,}[a-z](?!\S)")


def injection_score(text: str) -> int:
    """Summed weight of distinct heuristics that match `text` (0 for ordinary text)."""
    if len(text) < 8 or not _LETTER.search(text):
        return 0
    raw = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text))
    norm = raw.lower().translate(_LEET)
    if _SPACED.search(norm):  # "i g n o r e   a l l" letter-spacing evasion
        norm = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), norm)
    score = 0
    for rx, w in _NORM_PATTERNS:
        if rx.search(norm):
            score += w
            if score >= _THRESHOLD:
                return score
    for rx, w in _RAW_PATTERNS:
        if rx.search(raw):
            score += w
    for m in _HTML_COMMENT.finditer(raw):
        if _COMMENT_CUES.search(m.group(1)):
            score += 3
            break
    return score


def looks_like_injection(text: str) -> bool:
    return injection_score(text) >= _THRESHOLD


# ------------------------------------------------------------------ guard
def guard_output(
    columns: list[str],
    rows: list[tuple],
    role: RolePolicy,
    policy: Policy,
    *,
    masked_outputs: dict[int, str] | None = None,
) -> OutputOutcome:
    t0 = time.perf_counter()
    limits = policy.limits
    ncols = len(columns)

    # a. column masking plan
    if masked_outputs is not None:
        mask_plan = {i: k for i, k in masked_outputs.items() if 0 <= i < ncols}
        mask_rule = "output.masked_column"
    else:
        by_name: dict[str, str] = {}
        for _table, col, kind in policy.sensitive_for(role.name):
            by_name.setdefault(col.lower(), kind)
        mask_plan = {i: by_name[c.lower()] for i, c in enumerate(columns) if c.lower() in by_name}
        mask_rule = "output.masked_by_name"
    masked_columns = [columns[i] for i in sorted(mask_plan)]

    patterns = frozenset(role.scrub_patterns)
    cache: dict[str, tuple[str, str]] = {}
    redactions = 0
    pii_hit = inj_hit = trunc_hit = False
    max_chars = limits.max_cell_chars
    capped = len(rows) > limits.max_rows
    work = rows[: limits.max_rows] if capped else rows

    out_rows: list[tuple] = []
    for row in work:
        new = list(row)
        for i, v in enumerate(row):
            if v is None:
                continue
            if i in mask_plan:
                masked = mask_value(v, mask_plan[i])
                if masked != v:
                    new[i] = masked
                    redactions += 1
                continue  # already-masked values are not scrubbed or scanned again
            if not isinstance(v, str):
                continue
            hit = cache.get(v)
            if hit is None:
                kind, cur = "ok", v
                if patterns:
                    scrubbed = _scrub(v, patterns)
                    if scrubbed != v:
                        kind, cur = "pii", scrubbed
                if injection_score(v) >= _THRESHOLD:  # scan the original text, not the scrubbed one
                    kind, cur = "inj", INJECTION_TOKEN
                hit = cache[v] = (kind, cur)
            kind, cur = hit
            if kind == "pii":
                pii_hit = True
            elif kind == "inj":
                inj_hit = True
            if cur != v:
                redactions += 1
                new[i] = cur
            if len(cur) > max_chars:
                new[i] = cur[:max_chars] + "...[truncated]"
                trunc_hit = True
        out_rows.append(tuple(new))

    fired: list[str] = []
    if mask_plan:
        fired.append(mask_rule)
    if pii_hit:
        fired.append("output.pii_scrubbed")
    if inj_hit:
        fired.append("output.injection_redacted")
    if trunc_hit:
        fired.append("output.cell_truncated")
    if capped:
        fired.append("output.rows_capped")

    result = GuardResult(
        layer=LAYER,
        decision=Decision.ALLOW,
        rule_id=fired[0] if fired else None,
        reason="; ".join(fired),
        latency_ms=(time.perf_counter() - t0) * 1000,
        details={"fired": fired, "masked_columns": masked_columns, "redactions": redactions},
    )
    return OutputOutcome(result=result, columns=list(columns), rows=out_rows,
                         masked_columns=masked_columns, redactions=redactions)
