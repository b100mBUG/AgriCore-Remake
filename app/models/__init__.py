"""
app/models/__init__.py — Import all models so Base.metadata is complete.

Alembic's env.py imports this module (via `import app.models`) to
auto-detect table definitions. If you add a new model file, add its
import here — otherwise Alembic won't see it and migrations will miss
the table.
"""

from app.core.database import Base  # noqa: F401 — needed by Alembic

from app.models.farmer import Farmer  # noqa: F401
from app.models.officer import ExtensionOfficer, OfficerTier  # noqa: F401
from app.models.raw_content import RawContent, ContentStatus  # noqa: F401
from app.models.solution_card import SolutionCard, CardCategory, CardStatus  # noqa: F401
from app.models.input_ad import InputAd  # noqa: F401
from app.models.card_view import CardView  # noqa: F401

__all__ = [
    "Base",
    "Farmer",
    "ExtensionOfficer",
    "OfficerTier",
    "RawContent",
    "ContentStatus",
    "SolutionCard",
    "CardCategory",
    "CardStatus",
    "InputAd",
    "CardView",
]
