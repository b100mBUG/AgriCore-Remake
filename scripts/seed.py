"""
scripts/seed.py — Development seed data.

Populates the database with sample data for local development and testing.
Run with:
    python scripts/seed.py

Creates:
  - 3 extension officer profiles (free / basic / pro tier) — no passwords,
    since officers don't have accounts; the admin creates these directly
  - 6 solution cards spanning all four card_kind shapes (problem, practice,
    advisory, input) so you can see every card layout the frontend needs
    to render
  - 2 input ads
  - 1 test farmer

Data is for Kenyan context — real county names, real crop/animal names,
real pest/disease names that Kenyan smallholders face.

Safe to run multiple times — checks for existing records before inserting.
"""

import asyncio
import sys
import os

# Add project root to path so imports work from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, init_db
from app.models.farmer import Farmer
from app.models.input_ad import InputAd
from app.models.officer import ExtensionOfficer, OfficerTier
from app.models.solution_card import CardCategory, CardKind, CardStatus, SolutionCard


# ── Sample officer profiles (admin-created — no passwords) ─────────────────────

OFFICERS = [
    {
        "full_name": "James Mwangi",
        "email": "james.mwangi@agricore.dev",   # contact info only, never a login
        "title": "Crops Specialist",
        "county": "Nakuru",
        "specialization": "crops",
        "bio": "Maize and wheat specialist with 12 years in the Rift Valley. "
               "Monday to Friday, 8am–5pm. Reach me on WhatsApp anytime.",
        "years_experience": 12,
        "tier": OfficerTier.pro,
        "is_featured": True,
        "is_verified": True,
        "whatsapp_link": "https://wa.me/254712000001",
        "phone_number": "+254712000001",
        "crops_json": '["maize", "wheat", "barley"]',
    },
    {
        "full_name": "Faith Wanjiku",
        "email": "faith.wanjiku@agricore.dev",
        "title": "Horticulture Officer",
        "county": "Meru",
        "specialization": "horticulture",
        "bio": "French beans and tomato specialist. Export-grade production "
               "for Kilimo Fresh and Vegpro. Based in Meru County.",
        "years_experience": 8,
        "tier": OfficerTier.basic,
        "is_featured": False,
        "is_verified": True,
        "whatsapp_link": "https://wa.me/254712000002",
        "phone_number": "+254712000002",
        "crops_json": '["tomatoes", "french beans", "capsicum", "kale"]',
    },
    {
        "full_name": "Peter Omondi",
        "email": "peter.omondi@agricore.dev",
        "title": "Livestock & Soil Officer",
        "county": "Kisumu",
        "specialization": "livestock",
        "bio": "Dairy, poultry, and soil fertility specialist in Nyanza region.",
        "years_experience": 5,
        "tier": OfficerTier.free,
        "is_featured": False,
        "is_verified": False,
        "whatsapp_link": "https://wa.me/254712000003",
        "phone_number": "+254712000003",
        "crops_json": "[]",
    },
]

# ── Sample solution cards — one per card_kind, plus extras ─────────────────────
# Each `content` dict must match the shape its card_kind requires; see
# app/schemas/card_content.py. The classifier builds these automatically in
# production — this is just to populate something to browse locally.

CARDS = [
    {
        # card_kind = problem (category: pest)
        "title": "Fall Armyworm (FAW) on Maize",
        "category": CardCategory.pest,
        "card_kind": CardKind.problem,
        "crop": "maize",
        "region": "Rift Valley",
        "content": {
            "kind": "problem",
            "identify": [
                "Window-pane damage on young leaves — irregular holes with transparent membrane",
                "Frass (sawdust-like droppings) visible in leaf whorl",
                "Caterpillars are 1–4cm, greenish-brown with white Y-shaped mark on head",
            ],
            "treat": [
                "Apply Duduthrin (lambda-cyhalothrin) 1L/ha — spray directly into the whorl",
                "Spray in the evening when caterpillars are most active",
                "For severe infestations, use Emmaron 36 SC or Ampligo",
            ],
            "prevent": [
                "Adopt push-pull system (intercrop with Desmodium, border plant with Napier grass)",
                "Plant early to avoid peak moth flight season",
            ],
        },
        "confidence": 0.95,
        "status": CardStatus.published,
    },
    {
        # card_kind = problem (category: disease)
        "title": "Late Blight on Tomatoes",
        "category": CardCategory.disease,
        "card_kind": CardKind.problem,
        "crop": "tomatoes",
        "region": None,
        "content": {
            "kind": "problem",
            "identify": [
                "Water-soaked lesions on leaves that turn brown/black rapidly",
                "White fluffy mould visible on undersides of leaves in humid conditions",
                "Fruit develops brown, firm rot starting from any part",
            ],
            "treat": [
                "Remove and destroy all infected plant material immediately",
                "Apply Ridomil Gold MZ 68 WG or Acrobat MZ — spray every 5–7 days",
                "Spray when weather is clear — rain washes off fungicides",
            ],
            "prevent": [
                "Plant resistant varieties (e.g. Tylka F1, Kilele F1)",
                "Avoid overhead irrigation — use drip; water in the morning only",
            ],
        },
        "confidence": 0.92,
        "status": CardStatus.published,
    },
    {
        # card_kind = practice (category: livestock) — NOT forced into problem shape
        "title": "Foot and Mouth Disease (FMD) Prevention in Cattle",
        "category": CardCategory.livestock,
        "card_kind": CardKind.practice,
        "crop": "cattle",
        "region": None,
        "content": {
            "kind": "practice",
            "overview": "FMD spreads fast and devastates herds — routine vaccination and "
                        "movement control are the real defense, not just treating outbreaks.",
            "steps": [
                "Vaccinate all cattle twice a year with FMD vaccine from vets or agrovet shops",
                "Isolate any animal showing mouth/foot blisters immediately",
                "Wash sores with antiseptic (potassium permanganate) if an outbreak occurs",
            ],
            "tips": [
                "Report suspected FMD to your local vet immediately — it's a notifiable disease",
                "Control animal movement during regional outbreaks",
            ],
        },
        "confidence": 0.93,
        "status": CardStatus.published,
    },
    {
        # card_kind = practice (category: livestock) — dairy goats, the example from our chat
        "title": "Feeding Dairy Goats in the Dry Season",
        "category": CardCategory.livestock,
        "card_kind": CardKind.practice,
        "crop": "dairy goats",
        "region": "Rift Valley",
        "content": {
            "kind": "practice",
            "overview": "Pasture thins out fast in the dry season — goats need supplemented "
                        "feed to keep milk yield steady through to the next rains.",
            "steps": [
                "Provide hay or crop residues twice daily as a pasture substitute",
                "Add mineral lick blocks for calcium and phosphorus",
                "Ensure clean water is available at all times — goats drink more when feed is dry",
            ],
            "tips": [
                "Avoid moldy or dusty hay — it causes liver damage and respiratory issues",
            ],
        },
        "confidence": 0.89,
        "status": CardStatus.published,
    },
    {
        # card_kind = advisory (category: weather)
        "title": "Drought Risk This Week — Eastern Kenya",
        "category": CardCategory.weather,
        "card_kind": CardKind.advisory,
        "crop": "general",
        "region": "Eastern",
        "content": {
            "kind": "advisory",
            "summary": "Below-average rainfall is expected across Eastern counties this week, "
                      "following two dry weeks.",
            "recommended_actions": [
                "Delay planting if you haven't started — wait for confirmed rain",
                "Prioritize available water for livestock over irrigation",
                "Mulch standing crops to reduce soil moisture loss",
            ],
            "risk_level": "moderate",
        },
        "confidence": 0.88,
        "status": CardStatus.published,
    },
    {
        # card_kind = practice (category: harvest)
        "title": "Post-Harvest Maize Storage — Aflatoxin Prevention",
        "category": CardCategory.harvest,
        "card_kind": CardKind.practice,
        "crop": "maize",
        "region": None,
        "content": {
            "kind": "practice",
            "overview": "Aflatoxin builds up in maize stored too wet or in poor conditions — "
                        "it's invisible at first and dangerous to both people and livestock.",
            "steps": [
                "Dry maize on raised platforms away from bare ground before storage",
                "Test moisture with a meter before bagging — must be below 13%",
                "Use hermetic storage bags (PICS bags, GrainPro) to seal dry grain",
            ],
            "tips": [
                "Discard any grain with musty smell or visible mould — don't feed it to animals either",
                "Aflasafe KE (biological control) reduces aflatoxin risk by 80%+ if applied pre-harvest",
            ],
        },
        "confidence": 0.91,
        "status": CardStatus.published,
    },
    {
        # card_kind = input (category: input)
        "title": "Duduthrin — Broad-Spectrum Insecticide",
        "category": CardCategory.input,
        "card_kind": CardKind.input,
        "crop": "general",
        "region": None,
        "content": {
            "kind": "input",
            "product_overview": "Lambda-cyhalothrin based insecticide, effective against "
                                "Fall Armyworm, aphids, and thrips on most field crops.",
            "usage": [
                "Apply at 1L per hectare, diluted per label instructions",
                "Spray directly into the whorl for FAW control on maize",
                "Best applied in the evening when target pests are most active",
            ],
            "cautions": [
                "Do not exceed label dosage — resistance builds quickly with overuse",
                "Observe the pre-harvest interval stated on the product label",
            ],
        },
        "confidence": 0.85,
        "status": CardStatus.published,
    },
]

# ── Sample input ads ───────────────────────────────────────────────────────────

ADS = [
    {
        "business_name": "Amiran Kenya",
        "product_name": "Duduthrin 1L",
        "description": "Broad-spectrum insecticide. Highly effective against FAW, aphids, and thrips.",
        "price_kes": "KES 920/litre",
        "location": "Available in Nakuru, Eldoret, Nairobi",
        "is_general": False,
        "target_categories_json": '["pest"]',
        "target_crops_json": '["maize", "beans", "kale"]',
        "whatsapp_link": "https://wa.me/254700000001",
        "phone_number": "+254700000001",
        "is_active": True,
    },
    {
        "business_name": "Vegpro Kenya",
        "product_name": "Ridomil Gold MZ 68 WG — 1kg",
        "description": "Systemic + contact fungicide. The gold standard for late blight control.",
        "price_kes": "KES 1,450/kg",
        "location": "Nairobi, Meru, Nakuru",
        "is_general": False,
        "target_categories_json": '["disease"]',
        "target_crops_json": '["tomatoes", "potatoes"]',
        "whatsapp_link": "https://wa.me/254700000002",
        "phone_number": "+254700000002",
        "is_active": True,
    },
]

# ── Seed test farmer ───────────────────────────────────────────────────────────

TEST_FARMER = {
    "device_id": "dev-test-device-001",
    "name": "Test Farmer",
    "county": "Nakuru",
    "primary_crop": "maize",
    "farm_size_acres": 2.5,
}


# ── Runner ─────────────────────────────────────────────────────────────────────

async def seed() -> None:
    print("Initialising database...")
    await init_db()

    async with AsyncSessionLocal() as session:
        # ── Officers ──────────────────────────────────────────────────────────
        print("\nSeeding officer profiles (no passwords — admin-managed)...")
        for o_data in OFFICERS:
            existing = await session.execute(
                select(ExtensionOfficer).where(ExtensionOfficer.email == o_data["email"])
            )
            if existing.scalar_one_or_none():
                print(f"  SKIP (exists): {o_data['email']}")
                continue

            officer = ExtensionOfficer(**o_data)
            session.add(officer)
            print(f"  CREATED: {officer.full_name} ({officer.tier}) — {officer.county}")

        await session.commit()

        # ── Cards ─────────────────────────────────────────────────────────────
        print("\nSeeding solution cards...")
        for c_data in CARDS:
            existing = await session.execute(
                select(SolutionCard).where(SolutionCard.title == c_data["title"])
            )
            if existing.scalar_one_or_none():
                print(f"  SKIP (exists): {c_data['title']}")
                continue

            card = SolutionCard(
                **c_data,
                ai_model_version="seed-v1",
                source_url="https://agricore.app/seed",
                view_count=0,
            )
            session.add(card)
            print(f"  CREATED [{card.card_kind.value}]: {card.title}")

        await session.commit()

        # ── Ads ───────────────────────────────────────────────────────────────
        print("\nSeeding input ads...")
        for a_data in ADS:
            existing = await session.execute(
                select(InputAd).where(InputAd.product_name == a_data["product_name"])
            )
            if existing.scalar_one_or_none():
                print(f"  SKIP (exists): {a_data['product_name']}")
                continue

            ad = InputAd(**a_data)
            session.add(ad)
            print(f"  CREATED: {ad.product_name} — {ad.business_name}")

        await session.commit()

        # ── Test farmer ───────────────────────────────────────────────────────
        print("\nSeeding test farmer...")
        existing = await session.execute(
            select(Farmer).where(Farmer.device_id == TEST_FARMER["device_id"])
        )
        if not existing.scalar_one_or_none():
            session.add(Farmer(**TEST_FARMER))
            await session.commit()
            print(f"  CREATED: {TEST_FARMER['name']}")
        else:
            print(f"  SKIP (exists): {TEST_FARMER['device_id']}")

    print("\n✅ Seed complete.")
    print(
        "\nNote: officers have no login — manage them via the admin API "
        "(POST /admin/login, then /officers/* with the Bearer token)."
    )


if __name__ == "__main__":
    asyncio.run(seed())
