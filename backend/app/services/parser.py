"""Natural-language request -> ProcurementIntent.

The deterministic rule parser always works (no API key needed). If an LLM provider is
configured it is tried first; its output is validated against the same schema and against
the allowed code lists. Any LLM failure falls back to the rule parser.
The LLM only extracts intent; it never decides prices, availability, ranking or policy.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.config import get_settings
from app.integrations import llm
from app.schemas import ProcurementIntent
from app.services.allergens import ALLERGEN_WORDS, ATTRIBUTE_WORDS, KNOWN_CLAIMS

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
            "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})

UNIT_WORDS = {
    "l": "L", "ltr": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L",
    "kg": "KG", "kgs": "KG", "kilo": "KG", "kilos": "KG", "kilogram": "KG", "kilograms": "KG",
    "case": "SALES_UNIT", "cases": "SALES_UNIT", "carton": "SALES_UNIT", "cartons": "SALES_UNIT",
    "box": "SALES_UNIT", "boxes": "SALES_UNIT", "unit": "SALES_UNIT", "units": "SALES_UNIT",
    "pcs": "SALES_UNIT", "pieces": "SALES_UNIT", "piece": "SALES_UNIT", "pack": "SALES_UNIT", "packs": "SALES_UNIT",
    "packages": "SALES_UNIT", "bottles": "SALES_UNIT", "bottle": "SALES_UNIT", "bags": "SALES_UNIT", "bag": "SALES_UNIT",
    "jars": "SALES_UNIT", "cans": "SALES_UNIT", "trays": "SALES_UNIT",
}
GRAM_WORDS = {"g", "gram", "grams", "grams."}

RE_QTY = re.compile(
    r"(?P<num>\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)\s*(?P<unit>"
    + "|".join(sorted(list(UNIT_WORDS) + ["g", "grams", "gram"], key=len, reverse=True))
    + r")\b",
    re.I,
)
PHRASE_STOP = re.compile(
    r"\b(by|for|delivered|delivery|before|until|on|to be|with|that|which|within|under|below|max|maximum|budget|"
    r"and must|must|please|find|prioriti[sz]e|asap|no more than|deliver|but|so|because|due|next)\b|[,.;:!?]",
    re.I,
)
FILLER = re.compile(r"^(of|some|the|a|an|our|good|quality|fresh)\s+", re.I)
BUDGET_RE = re.compile(
    r"(?:budget(?:\s+(?:of|is))?|max(?:imum)?(?:\s+(?:budget|spend|of))?|under|below|less than|up to|no more than|within)"
    r"\s*(?:€|eur\s*)?(?P<amt>\d[\d ,]*(?:\.\d+)?)\s*(?P<cur>€|eur|euros)?",
    re.I,
)
CURRENCY_AMOUNT_RE = re.compile(r"(?:€\s*(?P<a1>\d[\d ,]*(?:\.\d+)?))|(?:(?P<a2>\d[\d ,]*(?:\.\d+)?)\s*(?:€|eur\b|euros\b))", re.I)


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def _to_number(s: str) -> float:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?", s):
        return float(s.replace(",", "").replace(" ", ""))
    return float(s.replace(",", ".").replace(" ", ""))


def parse_date_phrase(text: str, today: date) -> tuple[date | None, str | None]:
    t = text.lower()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?\b", t)
    if m:
        try:
            y = int(m.group(3)) if m.group(3) else today.year
            d = date(y, int(m.group(2)), int(m.group(1)))
            if not m.group(3) and d < today:
                d = date(y + 1, d.month, d.day)
            return d, m.group(0)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")\b", t) or re.search(
        r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", t
    )
    if m:
        a, b = m.group(1), m.group(2)
        day_s, mon_s = (a, b) if a.isdigit() else (b, a)
        try:
            d = date(today.year, MONTHS[mon_s], int(day_s))
            if d < today:
                d = date(today.year + 1, d.month, d.day)
            return d, m.group(0)
        except ValueError:
            pass
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1), "tomorrow"
    if re.search(r"\btoday\b", t):
        return today, "today"
    m = re.search(r"\bin\s+(\d+)\s+days?\b", t)
    if m:
        return today + timedelta(days=int(m.group(1))), m.group(0)
    if re.search(r"\bnext week\b", t):
        return today + timedelta(days=(7 - today.weekday()) or 7), "next week"
    m = re.search(r"\b(?:next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        delta = (WEEKDAYS[m.group(1)] - today.weekday()) % 7 or 7
        return today + timedelta(days=delta), m.group(0)
    return None, None


def _extract_product_phrase(text: str, qty_match: re.Match | None) -> str:
    if qty_match:
        rest = text[qty_match.end():]
    else:
        m = re.search(r"\b(?:need|want|order|buy|purchase|source|find|looking for|get)\s+(?:me\s+)?(?:some\s+)?", text, re.I)
        rest = text[m.end():] if m else text
    rest = re.sub(r"^\s*(?:of\s+)?", "", rest)
    stop = PHRASE_STOP.search(rest)
    phrase = rest[: stop.start()] if stop else rest
    phrase = FILLER.sub("", phrase.strip())
    phrase = re.sub(r"\s+", " ", phrase).strip()
    if not phrase and qty_match:
        # Quantity came after the product, e.g. "Find lactose-free milk for 1000 litres"
        head = text[: qty_match.start()]
        m = re.search(r"\b(?:need|want|order|buy|purchase|source|find|looking for|get)\s+(?:me\s+)?(?:some\s+)?", head, re.I)
        head = head[m.end():] if m else head
        stop = PHRASE_STOP.search(head)
        phrase = FILLER.sub("", (head[: stop.start()] if stop else head).strip())
        phrase = re.sub(r"\s+", " ", phrase).strip()
    return phrase


def _strip_terms(phrase: str, terms: list[str]) -> str:
    out = phrase
    for t in sorted(terms, key=len, reverse=True):
        out = re.sub(rf"(?i)\b{re.escape(t)}\b", " ", out)
    out = re.sub(r"(?i)\b(free|free from|free of|without|no)\b", " ", out)
    out = re.sub(r"[-–]+", " ", out)
    return re.sub(r"\s+", " ", out).strip(" -")


def rule_parse(text: str, today: date | None = None) -> ProcurementIntent:
    today = today or _today()
    raw = text.strip()
    low = raw.lower()
    assumptions: list[str] = []
    warnings: list[str] = []
    confirm: list[str] = []

    # Quantity + unit (ignore numbers that are part of a budget expression)
    qty, unit, unit_text, qty_match = None, None, None, None
    for m in RE_QTY.finditer(raw):
        before = low[max(0, m.start() - 12): m.start()]
        if "€" in before[-3:] or re.search(r"(budget|under|below|max)\s*$", before):
            continue
        qty_match = m
        break
    if qty_match:
        qty = _to_number(qty_match.group("num"))
        u = qty_match.group("unit").lower()
        unit_text = qty_match.group("unit")
        if u in GRAM_WORDS:
            qty, unit = qty / 1000.0, "KG"
            assumptions.append("Grams converted to kilograms")
        else:
            unit = UNIT_WORDS[u]
        if unit == "SALES_UNIT":
            assumptions.append(f"'{unit_text}' interpreted as the product's own sales unit")
            if unit_text.lower() not in ("unit", "units"):
                confirm.append(f"'{unit_text}' assumed to equal one product sales unit (pack sizes differ per product)")
    else:
        m = re.search(r"\b(\d+)\s+(?!days?\b)([a-z]+)", low)
        if m:
            qty, unit, unit_text = float(m.group(1)), "SALES_UNIT", None
            assumptions.append("No unit given; quantity interpreted as sales units")
            confirm.append(f"Quantity {qty:g} has no unit; assumed to mean {qty:g} sales units of the chosen product")
        else:
            warnings.append("No quantity found in request")
            confirm.append("No quantity stated; the purchase quantity cannot be determined")

    # Delivery date
    d, dtext = parse_date_phrase(raw, today)
    if d is None:
        d = today + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        assumptions.append(f"No delivery date given; assumed next business day {d.isoformat()}")
        confirm.append(f"No delivery date stated; assumed next business day {d.isoformat()}")
    elif d < today:
        warnings.append(f"Requested date {d.isoformat()} is in the past")

    # Budget
    budget = None
    bm = BUDGET_RE.search(raw)
    if bm and (bm.group("cur") or "€" in raw[max(0, bm.start() - 2): bm.end()] or "budget" in bm.group(0).lower()):
        try:
            budget = _to_number(bm.group("amt").rstrip(" ,"))
        except ValueError:
            budget = None
    if budget is None:
        cm = CURRENCY_AMOUNT_RE.search(raw)
        if cm:
            try:
                budget = _to_number((cm.group("a1") or cm.group("a2")).rstrip(" ,"))
            except ValueError:
                budget = None

    # Required dietary attributes
    required: list[str] = []
    attr_terms: list[str] = []
    for word, claim in sorted(ATTRIBUTE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?i)\b{re.escape(word)}\b", raw) and claim not in required:
            if claim == "LOW_LACTOSE" and "FREE_FROM_LACTOSE" in required:
                continue
            if word in ("fat free", "fat-free") and re.search(r"(?i)\bfat[- ]free\b", raw) is None:
                continue
            required.append(claim)
            attr_terms.append(word)
    if re.search(r"(?i)\b(organic|luomu)\b", raw):
        required.append("ORGANIC")
        attr_terms.append("organic")
        warnings.append("'Organic' has no declared claim field in the dataset; only product names can indicate it (unverified)")

    # Allergen exclusions
    excluded: list[str] = []
    excl_terms: list[str] = []
    words_alt = "|".join(sorted((re.escape(w) for w in ALLERGEN_WORDS), key=len, reverse=True))
    patterns = [
        rf"\b(?:no|without|free of|free from|allergic to|allergy to|exclude|excluding|avoid)\s+(?P<w>{words_alt})\b",
        rf"\b(?P<w>{words_alt})[- ]free\b",
        rf"\b(?P<w>{words_alt})\s+allerg(?:y|ies|ic)\b",
    ]
    for p in patterns:
        for m in re.finditer(p, low):
            w = m.group("w")
            if w == "milk" and re.search(r"lactose[- ]free\s+milk", low) and not re.search(r"(no|without|free of|free from)\s+milk|milk[- ]free", low):
                continue
            excl_terms.append(m.group(0))
            for code in ALLERGEN_WORDS[w]:
                if code not in excluded:
                    excluded.append(code)
    if "AW" in excluded and "FREE_FROM_GLUTEN" not in required:
        pass  # gluten-free is enforced as allergen exclusion AW (claim FREE_FROM_GLUTEN counts as evidence)

    # Priorities
    priorities: list[str] = []
    if re.search(r"(?i)\b(price|cheap|cheapest|cost|budget|affordable|reasonable pric)", raw):
        priorities.append("price")
    if re.search(r"(?i)reliab", raw):
        priorities.append("reliability")
    sustain = bool(re.search(r"(?i)(sustainab|carbon|co2|climate|green|low[- ]emission|environment)", raw))
    if sustain:
        priorities.append("sustainability")
    if re.search(r"(?i)availab", raw):
        priorities.append("availability")
    prefs = []
    if re.search(r"(?i)\b(stock|in stock)\b", raw):
        prefs.append("prefer stocked products")
    if re.search(r"(?i)\b(on time|on-time|guaranteed)\b", raw):
        prefs.append("on-time delivery")

    phrase = _extract_product_phrase(raw, qty_match)
    if not phrase:
        phrase = raw[:80]
        warnings.append("Could not isolate a product phrase; using full text for search")
    core = _strip_terms(phrase, attr_terms + [t.split()[-1] if " " in t else t for t in excl_terms] +
                        [w for w in ALLERGEN_WORDS if re.search(rf"\b{re.escape(w)}[- ]free\b", phrase.lower())])
    core = re.sub(r"(?i)\b(products?|items?|stuff)\b", " ", core)
    core = re.sub(r"\s+", " ", core).strip() or phrase

    return ProcurementIntent(
        product_query=core,
        display_query=phrase,
        quantity=qty,
        unit=unit,
        unit_text=unit_text,
        delivery_date=d,
        delivery_date_text=dtext,
        budget_limit=budget,
        excluded_allergens=excluded,
        excluded_allergen_terms=excl_terms,
        required_attributes=required,
        priorities=priorities,  # type: ignore[arg-type]
        delivery_preferences=prefs,
        sustainability_preference=sustain,
        assumptions=assumptions,
        warnings=warnings,
        confirmation_required=confirm,
    )


def _sanitize_llm(data: dict, fallback: ProcurementIntent, today: date) -> ProcurementIntent:
    from app.services.allergens import GS1_ALLERGEN_LABELS

    data = dict(data)
    data["excluded_allergens"] = [c for c in data.get("excluded_allergens") or [] if c in GS1_ALLERGEN_LABELS]
    data["required_attributes"] = [c for c in data.get("required_attributes") or [] if c in KNOWN_CLAIMS]
    data["preferred_attributes"] = [c for c in data.get("preferred_attributes") or [] if c in KNOWN_CLAIMS]
    data["priorities"] = [p for p in data.get("priorities") or [] if p in ("price", "reliability", "sustainability", "availability")]
    if data.get("unit") not in ("L", "KG", "SALES_UNIT", None):
        data["unit"] = None
    data.setdefault("display_query", data.get("product_query") or fallback.display_query)
    intent = ProcurementIntent.model_validate(data)
    if intent.delivery_date is None or intent.delivery_date < today:
        intent.delivery_date = fallback.delivery_date
        intent.assumptions.append("Delivery date taken from deterministic parser")
    # Safety: never drop an allergen exclusion the deterministic parser found
    for c in fallback.excluded_allergens:
        if c not in intent.excluded_allergens:
            intent.excluded_allergens.append(c)
    for c in fallback.required_attributes:
        if c not in intent.required_attributes:
            intent.required_attributes.append(c)
    # Conservative: assumptions the deterministic parser had to make always require confirmation
    for c in fallback.confirmation_required:
        if c not in intent.confirmation_required:
            intent.confirmation_required.append(c)
    return intent


def parse_request(text: str, today: date | None = None) -> tuple[ProcurementIntent, str, str | None]:
    """Returns (intent, parser_source, llm_error)."""
    today = today or _today()
    fallback = rule_parse(text, today)
    settings = get_settings()
    if not settings.llm_configured:
        return fallback, "RULES", None
    try:
        data = llm.extract_intent(text, today)
        return _sanitize_llm(data, fallback, today), f"LLM:{settings.llm_provider}", None
    except (llm.LLMError, ValidationError, ValueError, KeyError, TypeError) as e:
        return fallback, "RULES_FALLBACK", f"LLM extraction failed ({type(e).__name__}); deterministic parser used"
