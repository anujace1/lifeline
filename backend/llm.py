"""One place for all AI calls. Switch provider with LIFELINE_LLM=mock|anthropic|sap.
The mock provider is rule-based so the whole app runs offline with no API key."""

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
import config

log = logging.getLogger("lifeline")
WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}
KNOWN_REASONS = [
    "Damage before Delivering",
    "Rough product handling",
    "Miss handling of the product",
    "Wrong location dispatch",
    "Delivery dispute and undelivered",
    "Partner not answering the call",
    "Product left behind in courier warehouse",
    "Partner rejected the product",
    "Connecting local courier team issues",
    "Vehicle breakdown",
]
REASONS = [  # (keyword regex, reason label), first match wins
    (r"damag", "Damage before Delivering"),
    (r"rough", "Rough product handling"),
    (r"mis-?handl|miss handl", "Miss handling of the product"),
    (
        r"wrong (?:location|hub|city|address|route|dispatch)|misrout",
        "Wrong location dispatch",
    ),
    (r"disput|undeliver|not delivered", "Delivery dispute and undelivered"),
    (
        r"not answering|not responding|no response|unreachable|not reachable|not picking|didn't answer",
        "Partner not answering the call",
    ),
    (
        r"left behind|left in (?:the )?warehouse|stuck in (?:the )?warehouse|missed dispatch",
        "Product left behind in courier warehouse",
    ),
    (r"reject|refused", "Partner rejected the product"),
    (
        r"connecting|hand-?over|local courier|local team",
        "Connecting local courier team issues",
    ),
    (r"breakdown|broke down|vehicle", "Vehicle breakdown"),
    (r"wafer", "wafer shortage"),
    (r"raw material|material", "raw material shortage"),
    (r"customs", "customs hold"),
    (r"port|shipment|logistic|truck|freight|vessel", "logistics delay"),
    (r"yield", "yield issue"),
    (r"power|outage", "power outage"),
    (r"strike", "labour strike"),
    (r"capacity|fab\b", "capacity constraint"),
    (r"payment|cash|dues|invoice", "payment / cash-flow issue"),
]
ORDER_RE = re.compile(r"\b(?:PO-?\s?\d+|[A-Z]{2,5}-[A-Z]{2,5}-\d{6,})\b", re.I)
SKU_RE = re.compile(r"\b[A-Z]{3}-\d{3}\b")


def _norm_order(s):
    s = re.sub(r"\s", "", s.upper())
    m = re.fullmatch(r"PO-?(\d+)", s)
    return f"PO-{m.group(1)}" if m else s


EXTRACT_SYSTEM = (
    "You read messages (any language) from suppliers and delivery partners for a manufacturer. Reply with JSON only: "
    '{"intent":"delay|cashflow|other","delay_days":int|null,"reason":"short English phrase|null",'
    '"new_date":"YYYY-MM-DD|null","po_hint":"order or consignment number like PO-1234 or AIC-HYP-20261001|null","sku_hint":"CMP-001|null","confidence":0.0-1.0}. '
    "delay_days is total days late versus the original promised date; if the supplier gives a new delivery date instead, set new_date (YYYY-MM-DD) and leave delay_days null. Lower confidence when unsure. Never guess numbers. For reason, use one of these when it fits: "
    + "; ".join(KNOWN_REASONS)
    + "."
)


NUM = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
}
UNIT = {"day": 1, "din": 1, "week": 7, "wk": 7, "fortnight": 14}
MONTHS = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
]
MON = "|".join(MONTHS)


def _parse_days(t):
    words = "|".join(sorted(NUM, key=len, reverse=True))
    m = re.search(rf"\b(\d+|{words})\s*(days?|din|weeks?|wks?|fortnights?)\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUM[m.group(1)]
        return n * UNIT[m.group(2).rstrip("s")]
    m = re.search(r"\b(\d+)\s*d\b", t)  # "8d"
    return int(m.group(1)) if m else None


def _parse_date(t):
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?({MON})[a-z]*\b", t)
    if m:
        day, mon = int(m.group(1)), m.group(2)
    else:
        m = re.search(rf"\b({MON})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t)
        if not m:
            return None
        mon, day = m.group(1), int(m.group(2))
    try:
        d = date(config.TODAY.year, MONTHS.index(mon) + 1, day)
        if (config.TODAY - d).days > 30:
            d = date(config.TODAY.year + 1, d.month, d.day)
        return d.isoformat()
    except ValueError:
        return None


def mock_extract(text, parts=None):
    t = text.lower()
    days = _parse_days(t)
    new_date = None if days else _parse_date(t)
    delay_words = re.search(
        r"late|delay|postpone|pushed|push back|slip|behind|won't make|cannot deliver|can't deliver|reschedul|held up|stuck|later than",
        t,
    )
    pay_words = re.search(r"payment|cash|dues|invoice", t)
    reason = next((label for rx, label in REASONS if re.search(rx, t)), None)
    po = ORDER_RE.search(text)
    sku = SKU_RE.search(text)
    sku_hint = (
        sku.group(0)
        if sku
        else next((s for n, s in (parts or {}).items() if n.lower() in t), None)
    )
    if days or new_date or delay_words:
        intent = "delay"
        conf = (
            0.3
            + (0.35 if days else 0.3 if new_date else 0)
            + (0.15 if reason else 0)
            + (0.1 if (po or sku_hint) else 0)
        )
    elif pay_words:
        intent, conf = "cashflow", 0.7
    else:
        intent, conf = "other", 0.0
    return {
        "intent": intent,
        "delay_days": days,
        "new_date": new_date,
        "reason": reason,
        "po_hint": _norm_order(po.group(0)) if po else None,
        "sku_hint": sku_hint,
        "confidence": round(min(conf, 0.95), 2),
    }


def _openai_compat(system, user):
    """Any OpenAI-compatible chat endpoint. Defaults to a local Ollama server."""
    import urllib.request

    base = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    body = json.dumps(
        {
            "model": os.getenv("LLM_MODEL", "qwen2.5:3b"),
            "temperature": 0,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        base + "/chat/completions",
        body,
        {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.getenv("LLM_API_KEY", "none"),
        },
    )
    with urllib.request.urlopen(req, timeout=int(os.getenv("LLM_TIMEOUT", "90"))) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def complete(system, user):
    if config.LLM_PROVIDER == "openai":
        return _openai_compat(system, user)
    if config.LLM_PROVIDER == "anthropic":
        import anthropic

        r = anthropic.Anthropic().messages.create(
            model=config.MODEL,
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return r.content[0].text
    if config.LLM_PROVIDER == "sap":
        # SAP Generative AI Hub via LiteLLM's SAP provider (pip install litellm).
        # Credentials are read from AICORE_* environment variables (see README / test_sap.py).
        from litellm import completion

        r = completion(
            model=os.getenv("SAP_MODEL", "sap/anthropic--claude-4.5-haiku"),
            max_tokens=500,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return r.choices[0].message.content
    raise RuntimeError("mock provider has no completion")


def _num(v):
    try:
        n = int(float(v))
        return n if 0 < n < 400 else None
    except (TypeError, ValueError):
        return None


def _clean(ex, text, parts):
    """Validate whatever a model returned and cross-check it against the rule-based reader.
    Small models sometimes miss numbers, invent fields or format things oddly; this keeps the pipeline safe.
    """
    rule = mock_extract(text, parts)
    intent = (
        ex.get("intent")
        if ex.get("intent") in ("delay", "cashflow", "other")
        else rule["intent"]
    )
    try:
        conf = max(0.0, min(1.0, float(ex.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    days = _num(ex.get("delay_days"))
    new_date = None
    try:
        new_date = (
            date.fromisoformat(str(ex.get("new_date"))).isoformat()
            if ex.get("new_date")
            else None
        )
    except ValueError:
        pass
    if days is None and new_date is None and rule["delay_days"]:
        days = rule["delay_days"]  # the model missed an obvious number
    if days and rule["delay_days"] and days != rule["delay_days"]:
        conf = min(conf, 0.5)  # model and rules disagree: send to a human
    if intent == "other" and rule["intent"] != "other":
        intent, conf = rule["intent"], min(
            conf, 0.5
        )  # the model saw nothing, the rules saw something
    po = str(ex.get("po_hint") or "").strip()
    sku = str(ex.get("sku_hint") or "").strip()
    reason = (
        ex.get("reason")
        if isinstance(ex.get("reason"), str) and ex.get("reason").lower() != "null"
        else rule["reason"]
    )
    return {
        "intent": intent,
        "delay_days": days,
        "new_date": new_date,
        "reason": reason,
        "po_hint": _norm_order(po) if ORDER_RE.fullmatch(po) else rule["po_hint"],
        "sku_hint": sku if SKU_RE.fullmatch(sku) else rule["sku_hint"],
        "confidence": round(conf, 2),
    }


def extract(text, parts=None):
    if config.LLM_PROVIDER == "mock":
        return mock_extract(text, parts)
    try:
        raw = complete(EXTRACT_SYSTEM + f" Today is {config.TODAY.isoformat()}.", text)
        return _clean(json.loads(re.search(r"\{.*\}", raw, re.S).group(0)), text, parts)
    except Exception as e:  # never let an AI hiccup kill the demo
        log.warning("LLM extract failed (%s); falling back to mock", e)
        return mock_extract(text, parts)


def explain(facts, template):
    """facts: dict of verified numbers. The LLM may only reword them."""
    if config.LLM_PROVIDER == "mock":
        return template
    try:
        return complete(
            "You are a supply-chain analyst. Explain in at most 3 short sentences using ONLY the facts in the JSON. "
            "Do not invent numbers.",
            json.dumps(facts, default=str),
        ).strip()
    except Exception as e:
        log.warning("LLM explain failed (%s); using template", e)
        return template


# ---------------------------------------------------------------- response plans
_DEFAULT_PLAN_SYSTEM = (
    "You are a supply-chain recovery assistant. Using ONLY the facts in the JSON, propose a response plan for a human planner. "
    'Reply with JSON only: {"summary":"1-2 sentences","actions":[{"step":"...","owner":"role","when":"now|today|this week"}],'
    '"supplier_message":"short polite message to the partner","questions":["..."]}. '
    "Give 3 to 5 actions. Build on recommended_option and choose the relevant items from policy_options. "
    "Do not invent numbers, dates, penalties or contract terms; write 'subject to contract terms' where relevant."
)


def _load_prompt(name, default):
    """Prompts live in backend/prompts/*.md so they can be edited without touching Python. Restart the server after editing."""
    try:
        return (Path(__file__).resolve().parent / "prompts" / name).read_text(
            encoding="utf-8"
        ).strip() or default
    except OSError:
        return default


PLAN_SYSTEM = _load_prompt("plan_system.md", _DEFAULT_PLAN_SYSTEM)


def model_name():
    return {
        "openai": os.getenv("LLM_MODEL", "qwen2.5:3b"),
        "anthropic": config.MODEL,
        "sap": os.getenv("SAP_MODEL", "sap/anthropic--claude-4.5-haiku"),
    }.get(config.LLM_PROVIDER, "rules")


def _s(v, n):
    return v.strip()[:n] if isinstance(v, str) else ""


def _clean_plan(obj, fb, facts):
    acts = obj.get("actions") if isinstance(obj.get("actions"), list) else []
    actions = []
    for a in acts[:5]:
        a = {"step": a} if isinstance(a, str) else a
        if isinstance(a, dict) and _s(a.get("step"), 240):
            actions.append(
                {
                    "step": _s(a["step"], 240),
                    "owner": _s(a.get("owner"), 40) or "Planner",
                    "when": _s(a.get("when"), 30) or "today",
                }
            )
    qs = obj.get("questions") if isinstance(obj.get("questions"), list) else []
    plan = {
        "summary": _s(obj.get("summary"), 1000) or fb["summary"],
        "actions": actions or fb["actions"],
        "supplier_message": _s(obj.get("supplier_message"), 700)
        or fb["supplier_message"],
        "questions": (
            [_s(q, 200) for q in qs if _s(q, 200)][:5]
            if isinstance(obj.get("questions"), list)
            else fb["questions"]
        ),
    }
    # numbers the AI wrote that are not in the verified facts get flagged for the human
    blob = json.dumps(facts, default=str).replace(",", "")
    text = " ".join(
        [plan["summary"], plan["supplier_message"]]
        + [a["step"] for a in plan["actions"]]
    ).replace(",", "")
    plan["flags"] = sorted(
        {n for n in re.findall(r"\d{2,}(?:\.\d+)?", text) if n not in blob}
    )
    plan["source"] = "ai" if actions else "ai (partly rules)"
    return plan


def advise(facts, fallback):
    """AI drafts a response plan from verified facts. Any failure returns the rule-based plan instead."""
    base = {**fallback, "flags": [], "model": model_name()}
    if config.LLM_PROVIDER == "mock":
        return {**base, "source": "rules"}
    try:
        raw = complete(PLAN_SYSTEM, json.dumps(facts, default=str))
        obj = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        return {**_clean_plan(obj, fallback, facts), "model": model_name()}
    except Exception as e:
        log.warning("LLM plan failed (%s); using rule-based plan", e)
        return {**base, "source": "rules (AI unavailable)"}


# ---------------------------------------------------------------- free-form questions
ASK_SYSTEM = (
    "You are Lifeline's supply-chain analyst. Answer the user's question using ONLY the data in the JSON. "
    "Be concise (at most 6 sentences), name specific orders and partners, quote numbers exactly as given, and end with a recommended next step. "
    "If the data does not contain the answer, say what is missing. Never invent numbers, dates or contract terms. Do not use markdown tables."
)


def answer(question, facts, fallback_text):
    base = {"answer": fallback_text, "flags": [], "model": model_name()}
    if config.LLM_PROVIDER == "mock":
        return {**base, "source": "rules", "model": "rules"}
    try:
        text = complete(
            ASK_SYSTEM, json.dumps({"question": question, "data": facts}, default=str)
        ).strip()[:1400]
        if not text:
            raise ValueError("empty answer")
        blob = json.dumps(facts, default=str).replace(",", "") + " " + question
        flags = sorted(
            {
                n
                for n in re.findall(r"\d{2,}(?:\.\d+)?", text.replace(",", ""))
                if n not in blob
            }
        )
        return {"answer": text, "flags": flags, "source": "ai", "model": model_name()}
    except Exception as e:
        log.warning("LLM answer failed (%s); using rule-based answer", e)
        return {**base, "source": "rules (AI unavailable)"}
