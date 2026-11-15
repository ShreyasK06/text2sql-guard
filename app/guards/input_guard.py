"""Layer 1: deterministic heuristic screen of the natural-language question (no LLM).

Block rule ids: input.empty, input.too_long, input.obfuscation, input.injection_phrase,
input.role_override, input.embedded_sql, input.destructive_intent, input.restricted_data_intent.

The screen favours precision on benign analytics wording: it is one of six layers, and every
false block is a usability cost. It is a heuristic, not a boundary; later layers do not rely on it.
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

from app.types import Decision, GuardResult

LAYER = "input_guard"
MAX_CHARS = 2000

# --------------------------------------------------------------------------- normalisation
_ZW_CHARS = (
    "​‌‍‎‏⁠⁡⁢⁣⁤﻿­᠎"
    "‪‫‬‭‮⁦⁧⁨⁩"
)
_ZW_SET = frozenset(_ZW_CHARS)
_ZW_TABLE = dict.fromkeys(map(ord, _ZW_CHARS))
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x",
        "у": "y", "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d",
        "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v",
        "ρ": "p", "τ": "t", "υ": "u", "Α": "a", "Β": "b", "Ε": "e",
        "Ι": "i", "Ο": "o", "Ρ": "p", "Τ": "t", "А": "a", "В": "b",
        "Е": "e", "К": "k", "М": "m", "Н": "h", "О": "o", "Р": "p",
        "С": "c", "Т": "t", "Х": "x",
    }
)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def _normalise(text: str) -> tuple[str, int]:
    """NFKC, strip zero-width characters, fold homoglyphs, lowercase, collapse whitespace."""
    t = unicodedata.normalize("NFKC", text)
    zw = sum(1 for ch in t if ch in _ZW_SET)
    t = t.translate(_ZW_TABLE).translate(_HOMOGLYPHS).lower()
    return re.sub(r"\s+", " ", t).strip(), zw


def _punct_words(s: str) -> str:
    """Letters/digits/space plus sentence punctuation; apostrophes dropped (i'm -> im)."""
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9.;!?\n ]+", " ", s)
    s = re.sub(r"\b(?:[a-z] ){3,}[a-z]\b", lambda m: m.group(0).replace(" ", ""), s)  # i g n o r e
    return re.sub(r"\s+", " ", s).strip()


def _plain_words(pw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.;!?]+", " ", pw)).strip()


# --------------------------------------------------------------------------- obfuscation
_B64 = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_HEX = re.compile(r"(?:\b0x[0-9a-fA-F]{16,}\b|\b[0-9a-fA-F]{32,}\b|(?:\\x[0-9a-fA-F]{2}){4,}|(?:%[0-9a-fA-F]{2}){4,}|(?:&#x?[0-9a-fA-F]+;){3,}|(?:\\u[0-9a-fA-F]{4}){3,})")
_DECODE = re.compile(
    r"\b(?:decode|decrypt|deobfuscate|unhex|unescape)\b.{0,80}\b(?:follow|execute|run|obey|apply|carry out|do what)\b"
    r"|\b(?:base64|rot13|rot-13|hex[- ]encoded|url[- ]encoded)\b.{0,60}\b(?:instruction|command|prompt|decode|follow|run|execute)"
    r"|\b(?:follow|execute|run|obey)\b.{0,40}\b(?:decoded|decrypted|deobfuscated)\b"
)


def _mixed_script(text: str) -> bool:
    for tok in re.findall(r"\w+", unicodedata.normalize("NFKC", text)):
        latin = any("a" <= c.lower() <= "z" for c in tok)
        other = any("Ͱ" <= c <= "Ͽ" or "Ѐ" <= c <= "ӿ" for c in tok)
        if latin and other:
            return True
    return False


def _obfuscation(raw: str, base: str, zw: int) -> str | None:
    if zw >= 3:
        return f"{zw} zero-width or invisible characters in the question"
    if _mixed_script(raw):
        return "mixed Latin and Cyrillic/Greek letters inside one word (homoglyph obfuscation)"
    for m in _B64.finditer(raw):
        s = m.group(0)
        if re.search(r"[A-Za-z]", s) and re.search(r"\d", s):
            return "long base64-like blob in the question"
    m = _HEX.search(raw)
    if m:
        return "long hex or escape-encoded blob in the question"
    m = _DECODE.search(base)
    if m:
        return f"instruction to decode and follow hidden content ('{m.group(0)[:50]}')"
    return None


# --------------------------------------------------------------------------- injection phrases
_FILL = (
    r"(?:(?:all|any|the|your|my|every|of|previous|prior|above|earlier|preceding|original|former|system|"
    r"safety|security|these|those|and|everything|said|before|you|were|given|have|been|told|about|me|us|our|"
    r"initial|other|existing|current|content|sql|query|input|output|database|access|policy|policies|guard|guards)\s+){0,6}"
)
_INJECTION = [
    re.compile(
        r"\b(?:ignore|disregard|forget|override|overrule|discard|neglect|dismiss|abandon|drop|skip)\s+" + _FILL
        + r"(?:instructions?|rules?|guardrails?|guidelines?|restrictions?|constraints?|prompts?|polic(?:y|ies)|"
        r"safeguards?|directions?|commands?|programming|training|limitations?|protocols?)\b"
    ),
    re.compile(r"\b(?:ignore|disregard|forget)\s+(?:everything|all)\s+(?:above|before|prior|you\s+(?:were|have|know|learned))"),
    re.compile(
        r"\b(?:bypass|circumvent|evade|disable|deactivate|turn\s+off|switch\s+off|get\s+around|work\s+around|"
        r"defeat|sidestep|disarm|neutrali[sz]e)\s+" + _FILL
        + r"(?:filters?|guardrails?|guards?|safety|safeguards?|restrictions?|protections?|security(?:\s+checks?)?|"
        r"validation|moderation|polic(?:y|ies)|firewall|sandbox|checks?|controls?)\b"
    ),
    re.compile(
        r"\b(?:remove|lift|break|override|skip)\s+" + _FILL
        + r"(?:guardrails?|guards?|safety|safeguards?|restrictions?|protections?|security\s+checks?|validation|moderation|firewall|sandbox)\b"
    ),
    re.compile(
        r"\bdeveloper\s+mode\b|\bjail\s*break(?:ing|en|ed)?\b|\bdan\s+mode\b|\bdo\s+anything\s+now\b|\bgod\s+mode\b"
        r"|\bunrestricted\s+mode\b|\bdebug\s+mode\b|\bsudo\s+mode\b|\b(?:you\s+are|act\s+as|pretend\s+to\s+be|youre)\s+dan\b"
        r"|\bdan\b(?=\s+(?:prompt|jailbreak))"
    ),
    re.compile(
        r"\bpretend\s+(?:that\s+)?(?:you|there|we|the|i)\b|\bpretend\s+to\s+be\b|\brole\s*play\s+as\b|\bact\s+as\s+(?:if|though)\b"
        r"|\bimagine\s+you\s+(?:have|are|were|can)\b|\byou\s*(?:are|re)\s+now\b|\bfrom\s+now\s+on\s+(?:you|ignore|answer|respond|act)\b"
        r"|\b(?:with|have|having|has)\s+no\s+(?:restrictions?|rules?|filters?|guardrails?|constraints?|boundaries|safeguards?|limitations?)\b"
        r"|\bwithout\s+(?:any\s+)?(?:restrictions?|rules?|filters?|guardrails?|limitations?|constraints?|safeguards?|censorship)\b"
        r"|\bunfiltered\b|\buncensored\b"
    ),
    re.compile(
        r"\bsystem\s+(?:prompt|message|instructions?)\b|\b(?:initial|hidden|original|secret)\s+(?:prompt|instructions?)\b"
        r"|\b(?:reveal|show|print|repeat|display|output|leak|tell\s+me)\s+(?:me\s+)?(?:your|the\s+(?:original|initial|hidden|secret))\s+"
        r"(?:prompt|instructions?|rules|guidelines|configuration|policy)\b"
        r"|\brepeat\s+(?:the\s+)?(?:text|words|everything)\s+above\b"
    ),
]
_MARKERS = re.compile(
    r"(?:^|[\n\r.!?;>\]\"'(])\s*(?:system|assistant|developer)\s*:"
    r"|\[/?\s*(?:inst|sys|system)\s*\]|<<\s*/?\s*sys\s*>>"
    r"|<\|\s*(?:im_start|im_end|system|user|assistant|endoftext|eot_id|start_header_id)\s*\|>"
    r"|###\s*(?:system|instruction|human|assistant)|</?\s*(?:system|instructions?|prompt)\s*>"
    r"|\b(?:new|updated|real|actual|additional)\s+instructions?\s*[:\-]"
)


def _injection(base: str, plain: str, leet_plain: str) -> str | None:
    m = _MARKERS.search(base)
    if m:
        return f"fake system/role marker in the question ('{m.group(0).strip()[:40]}')"
    for form in (plain, leet_plain):
        for pat in _INJECTION:
            m = pat.search(form)
            if m:
                return f"instruction-override or jailbreak phrasing ('{m.group(0)[:60]}')"
    return None


# --------------------------------------------------------------------------- role override
_ADMINISH = r"(?:admin(?:istrator)?|dba|developers?|root|superuser|sysadmin|owner|engineer|ceo|cto|cfo|auditor|security\s+(?:officer|admin|team)|postgres)"
_ROLE_OVERRIDE = [
    re.compile(
        r"\bi\s*(?:am|m)\s+(?:actually\s+|really\s+|now\s+|also\s+)?(?:the\s+|an?\s+|your\s+)?"
        r"(?:system\s+|database\s+|db\s+|site\s+|super\s+|lead\s+|senior\s+)?" + _ADMINISH + r"\b"
    ),
    re.compile(
        r"\bmy\s+(?:role|permissions?|access|privileges?|clearance|account|job|position)\s+(?:is|are|was|were|has|have|level\s+is)\b"
        r".{0,30}\b(?:admin|root|superuser|dba|elevated|full|unrestricted|upgraded|changed|updated)\b"
    ),
    re.compile(r"\bas\s+(?:the\s+|an?\s+)?(?:root|admin|administrator|superuser|dba|sysadmin|system\s+admin)\b"),
    re.compile(
        r"\b(?:act|acting|behave|operate|run|login|log\s*in|sign\s*in|connect|authenticated?|execute|proceed)\s+(?:as|with|using)\s+"
        r"(?:the\s+|an?\s+)?(?:root|admin|administrator|superuser|dba|sysadmin|owner|postgres)\b"
    ),
    re.compile(r"\bswitch\s+(?:me\s+)?to\s+(?:the\s+)?(?:admin|root|superuser|dba)\b"),
    re.compile(
        r"\b(?:grant|give|allow|enable|provide)\s+(?:me|us)\s+(?:(?:full|admin|root|elevated|higher|more|write|superuser|"
        r"unrestricted|special|complete|all)\s+)*(?:access|permissions?|privileges?|rights|admin|control)\b"
    ),
    re.compile(
        r"\bwith\s+(?:superuser|root|admin(?:istrator)?|elevated|dba|sudo|full|unrestricted)\s+(?:privileges?|rights|permissions?|access|clearance)\b"
    ),
    re.compile(r"\bsudo\b|\bsu\s+root\b|\bset\s+role\b|\bchange\s+my\s+(?:role|permissions?|access|privileges?)\b"),
    re.compile(r"\b(?:promote|upgrade|elevate|escalate)\s+(?:me|my)\b"),
    re.compile(
        r"\bi\s+(?:have|hold|got|was\s+given|been\s+given)\s+(?:been\s+)?(?:granted\s+)?(?:admin|root|superuser|dba|elevated|full|special|unrestricted)\s+"
        r"(?:access|privileges?|permissions?|rights|clearance|rank|authority|authori[sz]ation)\b"
    ),
    re.compile(r"\bi\s*(?:am|m)\s+(?:fully\s+)?(?:authori[sz]ed|permitted|allowed|cleared)\b"),
    re.compile(r"\bemergency\s+override\b|\bbreak\s+glass\b|\bmaster\s+(?:key|password|override)\b"),
]


def _role_override(plain: str) -> str | None:
    for pat in _ROLE_OVERRIDE:
        m = pat.search(plain)
        if m:
            return f"claim of elevated identity or permission change ('{m.group(0)[:60]}')"
    return None


# --------------------------------------------------------------------------- embedded SQL
_DEFAULT_TABLES = ("users", "employees", "products", "orders", "order_items", "payments", "support_tickets", "api_keys")


def _embedded_patterns(tables: tuple[str, ...]) -> list[re.Pattern[str]]:
    tbl = "|".join(sorted((re.escape(t) for t in tables), key=len, reverse=True))
    return [
        re.compile(r"\bunion\s+(?:all\s+)?select\b"),
        re.compile(r";\s*(?:drop|delete|update|insert|alter|truncate|create|grant|revoke|select|copy|exec|execute|set|call|do|declare|vacuum)\b"),
        re.compile(r";\s*--"),
        re.compile(r"(?<!-)--(?:\s|$)"),
        re.compile(r"/\*|\*/"),
        re.compile(r"'\s*or\s+'?\w+'?\s*=\s*'?\w+|\bor\s+1\s*=\s*1\b|\bor\s+'1'\s*=\s*'1'|\band\s+1\s*=\s*[12]\b|\b1\s*=\s*1\b|'\s*or\s+'|\"\s*or\s+\""),
        re.compile(
            r"\bpg_sleep\b|\bsleep\s*\(|\bbenchmark\s*\(|\bwaitfor\s+delay\b|\bpg_[a-z_]+\s*\(|\bpg_catalog\b|\binformation_schema\b"
            r"|\bpg_(?:shadow|authid|user|roles|tables|stat\w*|class|proc)\b|\bcurrent_setting\s*\(|\blo_import|\blo_export"
            r"|\bdblink\b|\bxp_cmdshell\b|\bload_file\s*\(|\binto\s+(?:out|dump)file\b|\bgenerate_series\s*\("
        ),
        re.compile(r"\bcopy\s+\S+.{0,60}\b(?:to|from)\s+(?:program\b|stdout|stdin|['\"/])|\bcopy\s+\(?\s*select\b"),
        re.compile(
            r"\bselect\s+(?:distinct\s+)?(?:\*|(?:count|sum|avg|min|max|lower|upper|coalesce|round)\s*\(|[\w.]+\s*(?:,|\s+from\s+(?:public\.)?(?:"
            + tbl
            + r")\b))"
        ),
        re.compile(r"\bselect\s+(?:version|current_user|session_user|current_database|current_schema)\b|\bselect\s+\d+\s*(?:;|$)"),
        re.compile(r"\bwith\s+(?:recursive\s+)?\w+\s+as\s*\(\s*select\b|\bexec(?:ute)?\s*\("),
    ]


def _embedded_sql(base: str, tables: tuple[str, ...]) -> str | None:
    for pat in _embedded_patterns(tables):
        m = pat.search(base)
        if m:
            return f"question contains SQL or an injection fragment ('{m.group(0).strip()[:50]}')"
    return None


# --------------------------------------------------------------------------- destructive intent
_LEAD = (
    r"(?:^|[.;!?]\s*|\b(?:please|pls|plz|now|then|and|also|just|first|next|finally|kindly|can\s+you|could\s+you|would\s+you|will\s+you|"
    r"you\s+(?:should|must|need\s+to|have\s+to|can|may)|i\s+want\s+(?:you\s+)?to|i\s+need\s+(?:you\s+)?to|i\s+would\s+like\s+(?:you\s+)?to|"
    r"id\s+like\s+(?:you\s+)?to|i\s+wanna|lets|let\s+s|go\s+ahead\s+and|try\s+to|trying\s+to|help\s+me\s+(?:to\s+)?|want\s+to|need\s+to|have\s+to|"
    r"how\s+(?:do|can|would|could|should)\s+(?:i|we|you)|how\s+to)\s+)"
)
_DET = r"(?:(?:all|the|any|every|my|our|these|those|that|this|a|an|some)\s+)"
_ADJ = r"(?:(?:old|duplicate|test|entire|whole|existing|inactive|stale|expired|cancelled|canceled|current|production|prod|customer|user|employee|payment|order)\s+)"
_OBJ_ANY = (
    r"(?:tables?|rows?|records?|databases?|db|schema|columns?|data|everything|users?|customers?|orders?|employees?|products?|payments?|"
    r"tickets?|accounts?|entries|logs?|indexes|index|views?|from|passwords?)"
)
_NEG_NEXT = r"(?!\s+(?:date|dates|time|times|timestamp|timestamps|id|ids|count|counts|total|totals|amount|amounts|value|values|number|numbers|volume|status|history|frequency|rate|rates|size|per|by|over|each))"
_NEG_TAIL = (
    r"(?!\s+(?:from|before|when|in|for)\s+(?:the\s+)?(?:count|average|avg|total|sum|calculation|analysis|result|results|report|revenue|"
    r"query|metrics?|stats|statistics|aggregate|mean|median|computation|chart|graph))"
)
_TBL_WORDS = r"(?:users|employees|products|orders|order\s+items|payments|support\s+tickets|tickets|api\s+keys)"
_DESTRUCTIVE = [
    re.compile(
        _LEAD + r"(?:drop|truncate|delete|wipe|erase|destroy|purge|nuke|obliterate|shred)\s+(?:out\s+)?" + _DET + r"{0,2}" + _ADJ + r"{0,2}"
        + _OBJ_ANY + r"\b" + _NEG_NEXT + _NEG_TAIL
    ),
    re.compile(
        _LEAD + r"(?:remove|clear|empty|reset|modify|edit|overwrite|alter|rename)\s+" + _DET + r"{0,3}"
        r"(?:tables?|databases?|db|schema|columns?|indexes|index|permissions?|privileges|passwords?)\b" + _NEG_TAIL
    ),
    re.compile(_LEAD + r"(?:remove|clear|empty|wipe)\s+(?:out\s+)?(?:all|every|the\s+entire|the\s+whole)\s+(?:the\s+)?(?:data|rows|records|entries|contents)\s+(?:from|in|of)\s+(?:the\s+)?" + _TBL_WORDS + r"\b"),
    re.compile(_LEAD + r"(?:empty|clear|reset|flush)\s+(?:out\s+)?(?:the\s+|all\s+|my\s+|our\s+)?(?:\w+\s+)?(?:table|tables|database)\b"),
    re.compile(_LEAD + r"insert\s+(?:into|a\s+new|new|a|an|some|\d+|the\s+following|these|rows?|records?|values)\b"),
    re.compile(
        _LEAD + r"update\s+" + _DET + r"{0,2}(?:tables?|rows?|records?|orders?|users?|customers?|employees?|products?|payments?|tickets?|"
        r"accounts?|data|database|salary|salaries|price|prices|passwords?|status|statuses|permissions?|roles?)\b"
        r"(?!\s+(?:date|dates|time|times|history|frequency|count|log|logs|timestamp|rate|trend))(?!\s+(?:from|before|when|in|for)\s+the\s+(?:report|dashboard))"
    ),
    re.compile(r"\bupdate\s+\w+\s+set\b"),
    re.compile(
        _LEAD + r"set\s+(?:(?:all|the|every|each|my|our)\s+)*(?:\w+\s+){0,2}(?:salary|salaries|prices?|status|statuses|passwords?|roles?|amounts?|balance)\s+(?:to|as)\b"
    ),
    re.compile(
        _LEAD + r"(?:change|modify|edit|overwrite|alter)\s+(?:(?:all|the|every|each|my|our)\s+|\w+s\s+)*(?:salary|salaries|prices?|passwords?|roles?|balances?)\b"
    ),
    re.compile(
        _LEAD + r"create\s+(?:(?:a|an|the|new|temporary|temp|another|my|our)\s+){0,3}"
        r"(?:tables?|databases?|schema|users?|roles?|accounts?|logins?|index|indexes|views?|functions?|procedures?|triggers?|extension|admin|backdoor)\b"
        r"(?!\s+(?:of|showing|that\s+shows|which\s+shows|listing|that\s+lists|comparing|summar\w*|for\s+(?:the\s+)?(?:report|dashboard|presentation|chart)|"
        r"with\s+(?:the\s+)?(?:revenue|totals?|counts?|sales|orders|columns?\s+for\s+(?:revenue|sales))))"
    ),
    re.compile(_LEAD + r"(?:grant|revoke)\s+(?:\w+\s+){0,4}(?:access|privileges?|permissions?|admin|superuser|select|all|rights|role)\b"),
    re.compile(_LEAD + r"add\s+(?:a\s+)?new\s+(?:row|record|user|customer|employee|order|product|admin|entry)\b"),
]


def _destructive(pw: str) -> str | None:
    for pat in _DESTRUCTIVE:
        m = pat.search(pw)
        if m:
            return f"request to modify or destroy data or schema ('{m.group(0).strip()[:60]}')"
    return None


# --------------------------------------------------------------------------- restricted data intent
_REQ_VERBS = frozenset(
    "show list give get tell display print dump export reveal fetch find retrieve pull extract return provide send share expose leak "
    "see read lookup look need want select view output include what whats which who gimme".split()
)
_NEG_PREV = frozenset("by via per using through with without from in on".split())

# column name -> (description, strong patterns, weak patterns, words after a weak term that cancel it)
_TERMS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], str]] = {
    "ssn": (
        "social security numbers",
        (r"\bssns?\b", r"\bsocial\s+security(?:\s+(?:numbers?|nos?|ids?))?\b", r"\bnational\s+insurance\b", r"\btax\s+ids?\b"),
        (),
        "",
    ),
    "password_hash": (
        "password hashes",
        (r"\bpassword\s+hash(?:es)?\b", r"\bhashed\s+passwords?\b", r"\bpassword\s+digests?\b", r"\bbcrypt\b", r"\bpasswords\b"),
        (r"\bpassword\b", r"\bhash(?:es)?\b"),
        r"(?:reset|resets|change|changes|policy|policies|expir\w*|reminder|recovery|complexity|manager|rotation|requirements?)\b",
    ),
    "card_number": (
        "full card numbers",
        (r"\bcard\s+numbers?\b", r"\bfull\s+card\b", r"\bcvv\b", r"\bcvc\b", r"\bpan\b", r"\bcard\s+nos?\b", r"\b16\s*digit\b"),
        (r"\b(?:credit|debit|payment)\s+cards?\b", r"\bcard\s+details\b", r"\bcards?\s+on\s+file\b", r"\bcard\s+info\w*\b"),
        "",
    ),
    "salary": (
        "salary or compensation",
        (
            r"\bsalar(?:y|ies)\b", r"\bpayroll\b", r"\bwages?\b", r"\bcompensation\b", r"\bpay\s*(?:rate|scale|grade|band|check|cheque)s?\b",
            r"\bpaychecks?\b", r"\b(?:highest|top|best|lowest)\s+paid\b", r"\btop\s+earners?\b", r"\bwho\s+earns?\b",
            r"\bhow\s+much\s+(?:do|does|are|is)\s+(?:the\s+|our\s+|each\s+|an?\s+)?(?:employees?|staff|people|managers?|reps?|engineers?|workers?|team|we|they|salespeople|\w+\s+(?:employees?|staff|team))\s+(?:earn|make|get\s+paid|paid)\b",
        ),
        (),
        "",
    ),
    "email": (
        "email addresses",
        (r"\be[\s-]?mail\s+addresses\b", r"\be[\s-]?mail\s+addy\b", r"\bemail\s+ids?\b", r"\bcontact\s+(?:details|info|information|data)\b"),
        (r"\be[\s-]?mails?\b",),
        r"(?:campaigns?|marketing|newsletters?|promo\w*|templates?|channels?|volume|notifications?|opens?|clicks?|blasts?|opt\w*|subscriptions?|support|service|bounces?|sends?|sent)\b",
    ),
    "phone": (
        "phone numbers",
        (
            r"\bphone\s+numbers?\b", r"\btelephone\s+numbers?\b", r"\bmobile\s+numbers?\b", r"\bcell\s*(?:phone)?\s+numbers?\b",
            r"\bcontact\s+numbers?\b", r"\bphone\s+nos?\b", r"\bcontact\s+(?:details|info|information|data)\b",
        ),
        (r"\bphones?\b", r"\btelephones?\b"),
        r"(?:case|cases|accessor\w*|products?|categor\w*|chargers?|sales|orders?|brands?|models?|apps?|screens?|covers?|support|calls?|tickets?|channels?)\b",
    ),
    "address": (
        "home addresses",
        (r"\b(?:home|street|mailing|residential|postal|billing|physical)\s+address(?:es)?\b",),
        (r"\baddress(?:es)?\b",),
        "",
    ),
    "date_of_birth": (
        "dates of birth",
        (
            r"\bdate\s+of\s+birth\b", r"\bdob\b", r"\bbirth\s*dates?\b", r"\bbirthdays?\b", r"\bbirth\s*days?\b", r"\bborn\s+on\b",
            r"\bbirth\s+years?\b", r"\bwhen\s+(?:was|were)\s+\w+\s+born\b",
        ),
        (),
        "",
    ),
    "full_name": (
        "personal names",
        (
            r"\b(?:full\s+|first\s+|last\s+|sur)?names?\s+(?:of|for)\s+(?:(?:all|the|each|every|our|these|those|top\s+\d+|top\s+\w+)\s+)*"
            r"(?:customers?|users?|employees?|staff|people|clients|buyers|shoppers|reps?|salespeople|managers?|workers)\b",
            r"\b(?:customers?|users?|employees?|staff|clients?|buyers?|shoppers?|reps?|people|managers?|workers?)\s+(?:full\s+|first\s+|last\s+)?names?\b",
            r"\bfull\s+names?\b",
            r"\b(?:first|last|sur)\s*names?\b",
        ),
        (),
        "",
    ),
    "secret": (
        "API keys, secrets or credentials",
        (
            r"\bapi[\s_-]?keys?\b", r"\bsecret\s+keys?\b", r"\baccess\s+(?:keys?|tokens?)\b", r"\bcredentials\b", r"\bprivate\s+keys?\b",
            r"\bauth\s+tokens?\b", r"\bapi\s+secrets?\b",
        ),
        (r"\bsecrets?\b", r"\btokens?\b", r"\bcredential\b"),
        "",
    ),
}
_STRONG_RE = {c: tuple(re.compile(p) for p in v[1]) for c, v in _TERMS.items()}
_WEAK_RE = {c: tuple(re.compile(p) for p in v[2]) for c, v in _TERMS.items()}
_CANCEL_RE = {c: (re.compile(r"\s*" + v[3]) if v[3] else None) for c, v in _TERMS.items()}
_LAST4 = re.compile(r"(?:last\s+(?:4|four)|ending\s+in|final\s+(?:4|four))")


def _requested(prefix: str) -> bool:
    seg = re.split(r"[.;!?]", prefix)[-1]
    words = seg.split()[-9:]
    if any(w in _REQ_VERBS for w in words):
        return True
    return bool(words) and words[-1] in {"their", "his", "her", "its", "each", "every", "customers", "customer", "users", "user", "employees", "employee"}


def _denied_columns(role: Any, policy: Any) -> dict[str, list[str]]:
    """column name -> ['table.column', ...] for columns the role cannot read in any form anywhere."""
    out: dict[str, list[str]] = {}
    seen: dict[str, list[str]] = {}
    for table, col, _kind in policy.sensitive_for(role.name):
        seen.setdefault(col, []).append(f"{table}.{col}")
    for col, names in seen.items():
        accessible = any(
            role.column_access(t, col) != "denied" for t, cols in policy.schema.items() if col in cols
        )
        if not accessible:
            out[col] = names
    return out


def _hidden_table_patterns(role: Any, policy: Any) -> list[tuple[str, re.Pattern[str], bool]]:
    pats = []
    for table in policy.schema:
        if table in role.tables:
            continue
        words = table.split("_")
        pat = r"\b" + r"[\s_-]?".join(re.escape(w) for w in words) + r"s?\b"
        pats.append((table, re.compile(pat), len(words) > 1))
    return pats


def _restricted(pw: str, role: Any, policy: Any) -> str | None:
    denied = _denied_columns(role, policy)
    for col, names in denied.items():
        if col not in _TERMS:
            continue
        desc = _TERMS[col][0]
        target = ", ".join(sorted(set(names)))
        for pat in _STRONG_RE[col]:
            m = pat.search(pw)
            if not m:
                continue
            if col == "card_number" and _LAST4.search(pw[max(0, m.start() - 40): m.start()]):
                continue
            return f"question asks for {desc} ({target}), which role '{role.name}' may not read"
        for pat in _WEAK_RE[col]:
            for m in pat.finditer(pw):
                before = pw[: m.start()]
                prev = before.split()[-1] if before.split() else ""
                if prev in _NEG_PREV or (col == "address" and prev in {"email", "ip", "web", "mac", "url", "ipv4"}):
                    continue
                cancel = _CANCEL_RE[col]
                if cancel is not None and cancel.match(pw[m.end():]):
                    continue
                if col == "password_hash" and pw[m.end():].lstrip().startswith(("reset", "change")):
                    continue
                if _requested(before):
                    return f"question asks for {desc} ({target}), which role '{role.name}' may not read"
    for table, pat, multiword in _hidden_table_patterns(role, policy):
        m = pat.search(pw)
        if m and (multiword or _requested(pw[: m.start()])):
            return f"question asks for table '{table}', which role '{role.name}' may not read"
    return None


# --------------------------------------------------------------------------- entry point
def _result(decision: Decision, rule_id: str | None, reason: str, t0: float, **details: Any) -> GuardResult:
    return GuardResult(
        layer=LAYER,
        decision=decision,
        rule_id=rule_id,
        reason=reason,
        latency_ms=(time.perf_counter() - t0) * 1000,
        details=dict(details),
    )


def check_input(question: str, role: Any, policy: Any) -> GuardResult:
    t0 = time.perf_counter()

    def block(rule: str, why: str) -> GuardResult:
        return _result(Decision.BLOCK, rule, why, t0)

    if question is None or not str(question).strip():
        return block("input.empty", "the question is empty")
    raw = str(question)
    if len(raw) > MAX_CHARS:
        return block("input.too_long", f"the question has {len(raw)} characters; the limit is {MAX_CHARS}")

    base, zw = _normalise(raw)
    pw = _punct_words(base)
    plain = _plain_words(pw)
    leet_plain = _plain_words(_punct_words(base.translate(_LEET)))

    why = _obfuscation(raw, base, zw)
    if why:
        return block("input.obfuscation", why)
    why = _injection(base, plain, leet_plain)
    if why:
        return block("input.injection_phrase", why)
    why = _role_override(plain)
    if why:
        return block("input.role_override", why)
    tables = tuple(getattr(policy, "schema", {}) or _DEFAULT_TABLES)
    why = _embedded_sql(base, tables)
    if why:
        return block("input.embedded_sql", why)
    why = _destructive(pw)
    if why:
        return block("input.destructive_intent", why)
    why = _restricted(pw, role, policy)
    if why:
        return block("input.restricted_data_intent", why)
    return _result(Decision.ALLOW, None, "", t0, score=0.0)
