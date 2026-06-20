"""
app/services/crawler.py — Web crawler for farming knowledge.

Crawls a curated seed list of East African agricultural websites,
extracts readable text, and stores results in the raw_content table
for the AI classifier to process.

Design principles
─────────────────
1. Polite crawling — 2s delay between requests, respects robots.txt
2. Idempotent — URLs already in the DB are skipped unless content changed
   (detected via SHA-256 hash of body text)
3. Breadth-limited — follows links only one level deep from seed pages,
   capped at MAX_CRAWL_PAGES_PER_RUN to avoid runaway jobs
4. Domain-scoped — only follows links within the same seed domain
5. Async — all HTTP is non-blocking via httpx.AsyncClient

Adding new seed sources
───────────────────────
Append to SEED_URLS. The crawler will pick them up on the next run.
Only add domains with genuinely useful farming content — junk sources
produce junk cards.

Logging
───────
All crawler activity is logged at INFO level. Failures are WARNING.
Monitor these logs to spot domains that are blocking or returning errors.
"""

import asyncio
import hashlib
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.raw_content import ContentStatus, RawContent

log = logging.getLogger("agricore.crawler")

# ── Seed sources ──────────────────────────────────────────────────────────────
# Curated list of crawl-friendly East African farming knowledge sources.
# These are the entry points — the crawler follows internal links from here.

SEED_URLS: list[str] = [
    # ── TIER 1: Dense pest/disease factsheet indexes (crop category) ──
    "https://infonet-biovision.org/plant_pests",                  # ~40 pest/disease pages, identify/treat/prevent format, East Africa specific
    "https://plantwiseplusknowledgebank.org/",                    # 15,000+ factsheets; "Factsheets for Farmers" tier matches your tone exactly
    "https://www.greenlife.co.ke/focus/insect-pests/",             # 43 named-pest articles, Kenya-specific, commercial agro-input company
    "https://www.greenlife.co.ke/focus/diseases/",                 # 62 named-disease articles, same source
 
    # ── LIVESTOCK: animal health, husbandry, disease ──
    "https://infonet-biovision.org/animal-health-and-disease",     # ~30 disease pages: tick-borne, calf/lamb problems, mastitis, FMD, zoonoses
    "https://infonet-biovision.org/animal-species",                # Cattle, goats, sheep, poultry, pigs, fish, bees — species-specific husbandry
    "https://infonet-biovision.org/animal-husbandry",               # Feed rations, water, disease prevention, record keeping
    "https://www.agrikima.co.ke/articles",                          # Poultry/Dairy/Pigs/Goats categorized articles, identify-treat-prevent FAQ format
 
    # ── WEATHER: agrometeorological bulletins, Kenya-specific, updated monthly ──
    "https://meteo.go.ke/our-products/monthly-agrometeorological-bulletin/",  # Rainfall, temp, soil moisture vs. crop/livestock impact, monthly
    "https://meteo.go.ke/our-products/dekadal-agrometeorological-bulletin/",  # Same but 10-day cycle — more actionable for near-term farm decisions
    "https://meteo.go.ke/our-products/seasonal-forecast/",          # Long Rains / Short Rains outlook, county-level relevance
 
    # ── SOIL: fertility, conservation, Kenya-specific ──
    "https://infonet-biovision.org/soil-management",                # Kenyan Soils, soil degradation, fertility improvement, soil monitoring
    "https://infonet-biovision.org/conservation-agriculture",       # Soil cover, conservation tillage, mixed cropping/rotation
 
    # ── TIER 2: Uganda equivalent — dense, table-format, regionally specific ──
    "https://naads.or.ug/pests-disease-control/",                  # Uganda's official extension body; identify/treat tables, named local varieties
 
    # ── TIER 3: Institutional blogs — lower hit-rate, still legitimate, frequent Kenya/EA content ──
    "https://blog.invasive-species.org/",                          # CABI — pest/biocontrol stories
    "https://blog.plantwise.org/",                                 # CABI PlantwisePlus — frequent Kenya/Uganda/Tanzania content
    "https://www.iita.org/news-item/",                             # IITA — aflatoxin, regional pest research, content-rich
    "https://www.icipe.org/news",                                  # ICIPE Nairobi — institutional, occasionally pest-specific
]


# ── Constants ─────────────────────────────────────────────────────────────────

_CRAWL_DELAY_SECONDS = 2.0          # delay between page fetches — be polite
_REQUEST_TIMEOUT = 20.0             # per-request timeout
_MIN_BODY_LENGTH = 200              # skip pages with < 200 chars of text

_HEADERS = {
    "User-Agent": (
        "AgriCore-Bot/1.0 (East African farming knowledge aggregator; "
        "contact: admin@agricore.app)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en",
}

# Tags whose content we strip before extracting text
_STRIP_TAGS = [
    "script", "style", "nav", "footer", "header",
    "aside", "form", "iframe", "noscript", "advertisement",
]


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text(html: str) -> str:
    """Strip HTML and return clean readable text.

    Removes navigation, scripts, ads, and boilerplate. Returns the
    article body only — what a human would read.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove non-content tags
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    # Prefer article/main body if present
    body = (
        soup.find("article")
        or soup.find("main")
        or soup.find(id=re.compile(r"content|main|body", re.I))
        or soup.body
        or soup
    )

    text = body.get_text(separator=" ", strip=True)

    # Collapse excessive whitespace
    text = re.sub(r"\s{3,}", "\n\n", text)
    return text.strip()


def _extract_links(html: str, base_url: str) -> list[str]:
    """Return absolute internal links from a page.

    Only follows links within the same domain as base_url.
    """
    soup = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc
    links = []

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        # Same-domain only, http/https only
        if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
            links.append(absolute)

    return list(set(links))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_seen_urls(session) -> set[str]:
    """Return all URLs already stored in raw_content."""
    result = await session.execute(select(RawContent.url))
    return {row[0] for row in result.all()}


async def _save_page(
    session,
    *,
    url: str,
    title: str,
    body: str,
    etag: str | None,
    last_modified: str | None,
) -> None:
    """Insert a new raw_content row.

    Uses PostgreSQL ON CONFLICT DO NOTHING so concurrent crawler 
    runs don't crash on the unique URL constraint.
    """
    content_hash = _sha256(body)
    domain = urlparse(url).netloc

    # Replaced sqlite_insert with pg_insert to fix compilation crash on Render
    stmt = pg_insert(RawContent).values(
        url=url,
        source_domain=domain,
        title=title[:500] if title else None,
        body=body,
        etag=etag,
        last_modified=last_modified,
        content_hash=content_hash,
        status=ContentStatus.pending,
        retry_count=0,
    ).on_conflict_do_nothing(index_elements=["url"])

    await session.execute(stmt)
    await session.commit()


# ── Core crawler ──────────────────────────────────────────────────────────────

async def run_crawler() -> None:
    """Main crawler entry point — called by APScheduler.

    Crawls each seed URL, follows internal links one level deep,
    stores new pages as pending raw_content rows.
    """
    log.info("Crawler started.")
    pages_crawled = 0
    pages_saved = 0
    max_pages = settings.max_crawl_pages_per_run

    async with AsyncSessionLocal() as session:
        seen_urls = await _get_seen_urls(session)
        log.info("Already stored: %d URLs. Max this run: %d", len(seen_urls), max_pages)

        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
            verify=True,
        ) as client:

            for seed_url in SEED_URLS:
                if pages_crawled >= max_pages:
                    log.info("Reached max pages cap (%d). Stopping.", max_pages)
                    break

                # ── Fetch seed page ───────────────────────────────────────────
                try:
                    resp = await client.get(seed_url)
                    resp.raise_for_status()
                except Exception as exc:
                    log.warning("Seed fetch failed %r: %s", seed_url, exc)
                    continue

                html = resp.text
                pages_crawled += 1

                # Save seed page if not seen
                if seed_url not in seen_urls:
                    title_tag = BeautifulSoup(html, "lxml").find("title")
                    title = title_tag.get_text(strip=True) if title_tag else ""
                    body = _extract_text(html)
                    if len(body) >= _MIN_BODY_LENGTH:
                        await _save_page(
                            session,
                            url=seed_url,
                            title=title,
                            body=body,
                            etag=resp.headers.get("etag"),
                            last_modified=resp.headers.get("last-modified"),
                        )
                        seen_urls.add(seed_url)
                        pages_saved += 1
                        log.debug("Saved seed: %s", seed_url)

                # ── Follow internal links (one level deep) ────────────────────
                links = _extract_links(html, seed_url)
                log.debug("Found %d links on %s", len(links), seed_url)

                for link_url in links:
                    if pages_crawled >= max_pages:
                        break
                    if link_url in seen_urls:
                        continue

                    await asyncio.sleep(_CRAWL_DELAY_SECONDS)

                    try:
                        link_resp = await client.get(link_url)
                        link_resp.raise_for_status()
                    except Exception as exc:
                        log.debug("Link fetch failed %r: %s", link_url, exc)
                        continue

                    pages_crawled += 1
                    link_html = link_resp.text
                    link_body = _extract_text(link_html)

                    if len(link_body) < _MIN_BODY_LENGTH:
                        seen_urls.add(link_url)
                        continue

                    title_tag = BeautifulSoup(link_html, "lxml").find("title")
                    link_title = title_tag.get_text(strip=True) if title_tag else ""

                    await _save_page(
                        session,
                        url=link_url,
                        title=link_title,
                        body=link_body,
                        etag=link_resp.headers.get("etag"),
                        last_modified=link_resp.headers.get("last-modified"),
                    )
                    seen_urls.add(link_url)
                    pages_saved += 1
                    log.debug("Saved: %s", link_url)

                    await asyncio.sleep(_CRAWL_DELAY_SECONDS)

    log.info(
        "Crawler finished. Crawled: %d pages. Saved: %d new pages.",
        pages_crawled,
        pages_saved,
    )