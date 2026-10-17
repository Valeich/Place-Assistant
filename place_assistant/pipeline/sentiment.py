"""Gemini summary of review sentiment, based only on the review text actually
collected (Google's up-to-5 reviews plus any Foursquare tips). This is the
one pipeline step that genuinely needs an LLM; the rest are plain API calls.
"""

import json

from google.genai import types

from .. import config
from ..shared.llm import get_client

_SENTIMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_sentiment": {
            "type": "string",
            "enum": ["positive", "neutral", "negative", "mixed", "no_data"],
        },
        "summary": {"type": "string"},
        "pros": {"type": "array", "items": {"type": "string"}},
        "cons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overall_sentiment", "summary", "pros", "cons"],
}


def _no_data(summary: str) -> dict:
    """Returns an empty sentiment result marked "no_data", with summary
    explaining why there was nothing to analyse.
    """
    return {"overall_sentiment": "no_data", "summary": summary, "pros": [], "cons": []}


def summarize_sentiment(place_name: str, review_texts: list[str]) -> dict:
    """Summarizes sentiment from review_texts only, never from prior knowledge
    of the business. Returns a "no_data" result instead of guessing when there
    are no reviews.
    """
    if not review_texts:
        return _no_data("No reviews were available to summarize.")

    joined = "\n---\n".join(review_texts)
    prompt = f"""\
Here are real customer reviews for "{place_name}":

{joined}

Based ONLY on these reviews — not any outside knowledge of this business —
summarize the overall customer sentiment.
"""
    response = get_client().models.generate_content(
        model=config.PIPELINE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SENTIMENT_SCHEMA,
        ),
    )

    try:
        return json.loads(response.text)
    except Exception:
        return _no_data("Sentiment analysis failed to parse the model's response.")
