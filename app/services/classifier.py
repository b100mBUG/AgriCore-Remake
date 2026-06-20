"""
app/services/classifier.py — AI classifier background service.

Reads pending RawContent rows, sends them to Groq, and produces
structured SolutionCard rows. This is the core intelligence of AgriCore.

Architecture
────────────
1. Fetch a batch of pending raw_content rows (status="pending")
2. For each row, call Groq with the classification prompt
3. Parse the JSON response into a SolutionCard
4. On success: create card, mark raw_content as "processed"
5. On low confidence or irrelevant: mark raw_content as "rejected"
6. On Groq error: increment retry_count; mark "error" after 3 retries

Prompt design
─────────────
The prompt enforces JSON-only output with a strict schema, and we also
set response_format={"type": "json_object"} so Groq's API enforces valid
JSON server-side. We still strip markdown fences defensively in case a
future model swap doesn't support strict JSON mode. Confidence < threshold
goes to "review" status, not published.

Rate limiting
─────────────
Groq's per-minute token/request caps are tighter than Gemini's, so 429s
are expected under normal batch load, not exceptional failures. Each
Groq call is wrapped with exponential backoff (honoring Retry-After when
present) before it's allowed to count as a real error against the row's
retry_count.

Batch size
──────────
Processes MAX_BATCH_SIZE rows per run to avoid long-running jobs that
block the scheduler. Remaining pending rows are picked up on the next run.
"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone

from openai import AsyncOpenAI, APIStatusError, RateLimitError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.raw_content import ContentStatus, RawContent
from app.models.solution_card import (
    CARD_KIND_BY_CATEGORY,
    CardCategory,
    CardStatus,
    SolutionCard,
)
from app.schemas.card_content import CONTENT_MODEL_BY_KIND

log = logging.getLogger("agricore.classifier")

_MAX_BATCH_SIZE = 20          # rows per classifier run
_MAX_BODY_CHARS = 8_000       # truncate very long articles before sending to Groq
_MAX_RETRIES = 3              # give up after this many Groq errors on one row

_MAX_RATE_LIMIT_ATTEMPTS = 5  # transient 429 retries per single Groq call
_BASE_BACKOFF_SECONDS = 2.0   # exponential backoff base when Groq gives no Retry-After

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


# ── Groq client ────────────────────────────────────────────────────────────────
# Groq exposes an OpenAI-compatible /v1/chat/completions endpoint, so we reuse
# the official `openai` SDK rather than pulling in a separate Groq client.
# AsyncOpenAI matches the async-everywhere style of the rest of this service —
# the previous Gemini client call was actually blocking the event loop.

def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.groq_api_key, base_url=_GROQ_BASE_URL)


def _retry_after_seconds(exc: APIStatusError) -> float | None:
    """Pull Retry-After from a 429 response if Groq sent one."""
    try:
        header_val = exc.response.headers.get("retry-after")
    except AttributeError:
        return None
    if header_val is None:
        return None
    try:
        return float(header_val)
    except ValueError:
        return None


async def _create_completion_with_backoff(client: AsyncOpenAI, **kwargs):
    """Call chat.completions.create, retrying on transient 429s.

    Groq's per-minute token/request caps are much tighter than Gemini's,
    so a 429 here is an expected, recoverable event rather than a real
    failure. We honor Retry-After when present, otherwise back off
    exponentially with jitter. This is separate from the row-level
    retry_count / _MAX_RETRIES bookkeeping below, which tracks repeated
    *content* failures across scheduler runs, not transient throttling
    within a single call.
    """
    attempt = 0
    while True:
        try:
            return await client.chat.completions.create(**kwargs)
        except RateLimitError as exc:
            attempt += 1
            if attempt >= _MAX_RATE_LIMIT_ATTEMPTS:
                log.warning(
                    "Groq rate limit persisted after %d attempts — giving up on this row.",
                    attempt,
                )
                raise

            wait = _retry_after_seconds(exc)
            if wait is None:
                wait = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                wait += random.uniform(0, 1)  # jitter to avoid thundering herd

            log.info(
                "Groq rate limited (attempt %d/%d) — waiting %.1fs before retry.",
                attempt, _MAX_RATE_LIMIT_ATTEMPTS, wait,
            )
            await asyncio.sleep(wait)


# ── Classification prompt ─────────────────────────────────────────────────────

_SYSTEM_PROMPT = """
You are an expert agricultural knowledge classifier for East Africa, specialized for smallholder farmers in Kenya.
Your job is to parse a raw agricultural article and extract a clean, structured solution card.

This system covers ALL of agriculture, not just crops — pests, plant disease, soil, CROP FARMING,
LIVESTOCK REARING (cattle, goats, sheep, poultry, pigs, rabbits, bees), weather-driven advisories,
farm inputs, and post-harvest handling all matter equally. Do not default to a crop framing for
articles that are actually about animal husbandry, weather, or inputs.

STEP 1 — Pick the category, honestly:
  pest       → insect, worm, rodent damage to a crop
  disease    → fungal, bacterial, viral, or nutrient-deficiency symptoms in a crop
  soil       → pH, fertility, erosion, compaction issues
  livestock  → ANYTHING about raising/feeding/managing/breeding cattle, goats, sheep,
               poultry, pigs, rabbits, or bees — including animal health, not just disease
  weather    → drought, frost, flood, wind, rainfall outlook and what to do about it
  input      → fertiliser, pesticide, seed variety product information
  harvest    → post-harvest handling, storage, grading, spoilage prevention

STEP 2 — Match the category to its card_kind (this determines which fields you fill in):
  pest, disease, soil          → card_kind = "problem"   (something is wrong; identify/treat/prevent)
  livestock, harvest           → card_kind = "practice"  (a routine or skill; overview/steps/tips)
  weather                      → card_kind = "advisory"  (a time-bound alert; summary/recommended_actions/risk_level)
  input                        → card_kind = "input"     (a product; product_overview/usage/cautions)

Do NOT force livestock or weather content into identify/treat/prevent — that shape is ONLY for
pest, disease, and soil problems. A card about feeding dairy goats is not "a problem to identify."

CRITICAL LANGUAGE & UI RULES:
1. All free-text fields (title, and every field inside "content", and extra_notes) MUST be written
   entirely in clear, natural KISWAHILI or ENGLISH.
2. The fields "crop", "category", and "region" MUST remain in ENGLISH as specified below.
   For livestock cards, put the animal in "crop" (e.g. "dairy goats", "poultry", "general" if not animal-specific).
3. Every item inside a list field (e.g. "identify", "steps", "usage") is ONE bullet, on its own
   array entry — never bundle multiple bullets into one string.
4. Use standard KivyMD style text formatting markers directly inside string values to make them
   readable on mobile viewports:
   - Use [b]text[/b] for bolding key headings, names of pests/breeds/products, or warnings.
   - Use [i]text[/i] for local product or botanical/breed names (e.g., [i]Duduthrin[/i], [i]Sahiwal[/i]).
   - Use color hex designations where highly relevant:
     - Danger/Alert items: [color=#B00020][b]Hatari:[/b][/color]
     - Safe/recommended paths: [color=#2E7D32][b]Suluhisho:[/b][/color]
5. Never add dangling structural characters like stray '[]' or '{}' inside text string values.

Output schema (all fields required; "content" shape depends on category — see STEP 2 above):
{
  "relevant": true or false,
  "title": "[b]Mada fupi ya kadi, herufi zisizozidi 100[/b]",
  "category": one of ["pest", "disease", "soil", "livestock", "weather", "input", "harvest"],
  "crop": "primary crop or animal type in lowercase English (e.g. 'maize', 'dairy goats', 'poultry'), or 'general'",
  "region": "Kenya region in English if location-specific (e.g. 'Rift Valley', 'Coast'), or null if universal",
  "content": {
    // EXACTLY ONE of the four shapes below, matching the category's card_kind from STEP 2.

    // card_kind = "problem"  (category: pest, disease, soil)
    "kind": "problem",
    "identify": ["[b]Dalili:[/b] Angalia kama kuna madoa...", "Majani yanapoanza kukauka..."],
    "treat": ["[color=#2E7D32][b]Suluhisho:[/b][/color] Nyunyizia dawa ya [i]Duduthrin[/i]...", "Ng'oa mimea iliyoathirika..."],
    "prevent": ["Badilisha mazao kila msimu...", "Tumia mbegu zilizoidhinishwa..."]

    // card_kind = "practice"  (category: livestock, harvest)
    "kind": "practice",
    "overview": "Sentensi moja au mbili kuhusu zoezi hili na umuhimu wake.",
    "steps": ["Lisha mara mbili kwa siku...", "Toa maji safi kila wakati..."],
    "tips": ["Epuka nyasi zenye ukungu..."]

    // card_kind = "advisory"  (category: weather)
    "kind": "advisory",
    "summary": "Maelezo ya hali ya hewa inayotarajiwa kwa lugha rahisi.",
    "recommended_actions": ["Panda mapema kabla ya mvua...", "Hifadhi maji ya kunyweshea..."],
    "risk_level": one of ["low", "moderate", "high", "severe"]

    // card_kind = "input"  (category: input)
    "kind": "input",
    "product_overview": "Maelezo ya pembejeo hii na matumizi yake.",
    "usage": ["Tumia kiasi cha...", "Weka wakati wa..."],
    "cautions": ["Usizidishe kiwango...", "Epuka kuchanganya na..."]
  },
  "extra_notes": "Maelezo ya ziada kuhusu vipimo au kiasi. Weka null kama hakuna.",
  "confidence": a float between 0.0 and 1.0
}

Rules:
- If the text is irrelevant to farming/livestock/agriculture for smallholders, set relevant=false and confidence=0.0.
- All advice must be practical, specific, and optimized for farmers with primary school education.
- "content" must contain ONLY the fields for the one kind you picked — do not mix fields from different kinds.
- Respond ONLY with valid, raw JSON. No markdown fences, no commentary.
""".strip()


# ── JSON parsing ──────────────────────────────────────────────────────────────

def _parse_groq_json(raw: str) -> dict:
    """Parse Groq's response, stripping any accidental markdown fences.

    response_format={"type": "json_object"} should make this a no-op in
    practice, but we keep the stripping as a defensive fallback (e.g. if
    the model is swapped to one without strict JSON-mode support).
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    return json.loads(cleaned)


def _is_valid_category(value: str) -> bool:
    return value in {c.value for c in CardCategory}


# ── Card builder ──────────────────────────────────────────────────────────────

def _build_card(data: dict, raw: RawContent) -> SolutionCard:
    """Build a SolutionCard ORM instance from classifier output dict.

    card_kind is never trusted from the model directly — it's derived
    from the category via CARD_KIND_BY_CATEGORY, so a model that
    hallucinates a mismatched "kind" inside content can't desync the
    card's shell from its actual content shape. The content dict itself
    IS validated against that kind's schema; if it doesn't fit (missing
    fields, wrong types), the card is still created but forced into
    "review" status rather than dropped, so nothing silently vanishes —
    an admin can fix or discard it.
    """
    category_raw = str(data.get("category", "pest")).lower()
    category = CardCategory(category_raw) if _is_valid_category(category_raw) else CardCategory.pest
    card_kind = CARD_KIND_BY_CATEGORY[category]

    confidence = float(data.get("confidence", 0.0))
    threshold = settings.classifier_confidence_threshold

    content_raw = data.get("content")
    if not isinstance(content_raw, dict):
        content_raw = {}
    # Force the kind to match the category-derived kind regardless of what
    # the model put in content["kind"] — see docstring above.
    content_raw["kind"] = card_kind.value

    content_model = CONTENT_MODEL_BY_KIND[card_kind.value]
    try:
        validated_content = content_model.model_validate(content_raw).model_dump()
        content_is_valid = True
    except ValidationError as exc:
        log.warning(
            "Card content failed schema validation for raw_content id=%d "
            "(category=%s, kind=%s): %s — saving as 'review' with raw content.",
            raw.id, category.value, card_kind.value, exc,
        )
        validated_content = content_raw
        content_is_valid = False

    # Cards below confidence threshold OR with malformed content go to
    # review, never straight to published.
    status = (
        CardStatus.published
        if confidence >= threshold and content_is_valid
        else CardStatus.review
    )

    return SolutionCard(
        title=str(data.get("title", ""))[:300],
        category=category,
        card_kind=card_kind,
        crop=str(data.get("crop", "general")).lower()[:120],
        region=data.get("region"),
        content=validated_content,
        extra_notes=data.get("extra_notes"),
        confidence=confidence,
        ai_model_version=settings.groq_model,
        source_url=raw.url,
        raw_content_id=raw.id,
        status=status,
    )


# ── Per-row classifier ────────────────────────────────────────────────────────

async def _classify_one(client: AsyncOpenAI, raw: RawContent, session: AsyncSession) -> str:
    """Classify a single RawContent row. Returns the action taken.

    Returns one of: "published" | "review" | "rejected" | "error"
    """
    body = (raw.body or "")[:_MAX_BODY_CHARS]
    prompt = f"Article title: {raw.title or 'Unknown'}\n\nArticle text:\n{body}"

    try:
        response = await _create_completion_with_backoff(
            client,
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,       # low temp for consistent structured output
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        raw_text = (response.choices[0].message.content or "").strip()

    except Exception as exc:
        log.warning("Groq API error for raw_content id=%d: %s", raw.id, exc)
        raw.retry_count += 1
        raw.error_message = str(exc)
        if raw.retry_count >= _MAX_RETRIES:
            raw.status = ContentStatus.error
            log.error(
                "Giving up on raw_content id=%d after %d retries.",
                raw.id, _MAX_RETRIES,
            )
        await session.commit()
        return "error"

    # ── Parse response ────────────────────────────────────────────────────────
    try:
        data = _parse_groq_json(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning(
            "JSON parse failed for raw_content id=%d: %s\nRaw output: %.200s",
            raw.id, exc, raw_text,
        )
        raw.retry_count += 1
        raw.error_message = f"JSON parse error: {exc}"
        if raw.retry_count >= _MAX_RETRIES:
            raw.status = ContentStatus.error
        await session.commit()
        return "error"

    # ── Relevance check ───────────────────────────────────────────────────────
    if not data.get("relevant", False) or float(data.get("confidence", 0.0)) < 0.20:
        raw.status = ContentStatus.rejected
        raw.classified_at = datetime.now(timezone.utc)
        await session.commit()
        log.debug("Rejected (not relevant): raw_content id=%d", raw.id)
        return "rejected"

    # ── Build and save card ───────────────────────────────────────────────────
    card = _build_card(data, raw)
    session.add(card)

    raw.status = ContentStatus.processed
    raw.classified_at = datetime.now(timezone.utc)
    raw.error_message = None

    await session.commit()
    log.info(
        "Card created: %r [%s/%s] confidence=%.2f status=%s",
        card.title, card.category, card.crop, card.confidence, card.status,
    )
    return card.status.value


# ── Batch runner ──────────────────────────────────────────────────────────────

async def run_classifier() -> None:
    """Main classifier entry point — called by APScheduler.

    Fetches up to _MAX_BATCH_SIZE pending rows and classifies them.
    Designed to be called repeatedly; partial batches are fine.
    """
    log.info("Classifier started.")

    if not settings.groq_api_key:
        log.warning("GROQ_API_KEY not set — classifier skipped.")
        return

    client = _get_client()
    counts: dict[str, int] = {
        "published": 0, "review": 0, "rejected": 0, "error": 0
    }

    async with AsyncSessionLocal() as session:
        # Fetch pending rows that haven't exceeded retry limit
        result = await session.execute(
            select(RawContent)
            .where(
                RawContent.status == ContentStatus.pending,
                RawContent.retry_count < _MAX_RETRIES,
            )
            .order_by(RawContent.crawled_at.asc())  # oldest first
            .limit(_MAX_BATCH_SIZE)
        )
        rows = result.scalars().all()

        if not rows:
            log.info("No pending raw content to classify.")
            return

        log.info("Classifying %d rows...", len(rows))

        for raw in rows:
            action = await _classify_one(client, raw, session)
            counts[action] = counts.get(action, 0) + 1

    log.info(
        "Classifier finished. published=%d review=%d rejected=%d error=%d",
        counts["published"], counts["review"], counts["rejected"], counts["error"],
    )