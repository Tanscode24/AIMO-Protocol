"""Optional LLM provider for structured intent extraction.

Supported: anthropic | openai | gemini (raw HTTPS via httpx, no SDK dependency).
The model is only asked to return JSON matching ProcurementIntent; the result is validated
and sanitised by app.services.parser. The LLM has no tools that touch prices, policies,
approvals or orders, so it cannot bypass backend authorization.
"""
from __future__ import annotations

import json
import re
from datetime import date

import httpx

from app.config import get_settings
from app.schemas import ProcurementIntent

DEFAULT_MODELS = {"anthropic": "claude-haiku-4-5", "openai": "gpt-4o-mini", "gemini": "gemini-2.0-flash"}


class LLMError(Exception):
    pass


SYSTEM_PROMPT = """You extract structured procurement intent for a food-service wholesaler catalogue.
Return ONLY a JSON object with these keys:
product_query (core product noun phrase, English, without dietary words, e.g. "milk"),
display_query (product phrase as written), quantity (number or null),
unit ("L" for litres, "KG" for kilograms, "SALES_UNIT" for cases/packs/pieces, or null), unit_text,
delivery_date (YYYY-MM-DD or null; resolve weekdays to the NEXT occurrence after today), delivery_date_text,
budget_limit (number in EUR or null), excluded_allergens (GS1 allergen codes: AM milk, AE eggs, AW gluten cereals,
UW wheat, AY soy, AP peanuts, AN tree nuts, AF fish, AC crustaceans, UM molluscs, AS sesame, BM mustard, BC celery,
NL lupin, AU sulphites), required_attributes (subset of FREE_FROM_LACTOSE, LOW_LACTOSE, VEGAN, VEGETARIAN,
NO_ADDED_SUGARS, LOW_FAT, FREE_FROM_FAT, HIGH_PROTEIN, HIGH_FIBRE, FREE_FROM_ADDITIVES, FREE_FROM_PRESERVATIVES, ORGANIC),
preferred_attributes, priorities (subset of price, reliability, sustainability, availability),
delivery_preferences (list of strings), sustainability_preference (bool), assumptions (list), warnings (list).
IMPORTANT: lactose-free is FREE_FROM_LACTOSE and does NOT exclude milk (AM). Do not invent constraints."""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError("No JSON object in LLM output")
    return json.loads(m.group(0))


def extract_intent(text: str, today: date) -> dict:
    s = get_settings()
    provider = s.llm_provider.lower()
    model = s.llm_model or DEFAULT_MODELS.get(provider, "")
    key = s.llm_api_key.get_secret_value()
    user = f"Today is {today.isoformat()} ({today.strftime('%A')}). Request: {text}"
    try:
        with httpx.Client(timeout=s.llm_timeout_seconds) as c:
            if provider == "anthropic":
                r = c.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": 800, "system": SYSTEM_PROMPT,
                          "messages": [{"role": "user", "content": user}]},
                )
                r.raise_for_status()
                out = "".join(b.get("text", "") for b in r.json().get("content", []))
            elif provider == "openai":
                r = c.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]},
                )
                r.raise_for_status()
                out = r.json()["choices"][0]["message"]["content"]
            elif provider == "gemini":
                r = c.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                          "contents": [{"role": "user", "parts": [{"text": user}]}],
                          "generationConfig": {"responseMimeType": "application/json"}},
                )
                r.raise_for_status()
                out = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            else:
                raise LLMError(f"Unsupported LLM provider '{provider}'")
    except httpx.HTTPError as e:
        raise LLMError(f"LLM request failed: {type(e).__name__}") from e
    data = _extract_json(out)
    ProcurementIntent.model_json_schema()  # schema available for future tool-calling providers
    return data
