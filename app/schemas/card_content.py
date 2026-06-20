"""
app/schemas/card_content.py — Validated shapes for the fluid part of a card.

Each CardKind (see app/models/solution_card.py) has its own content
shape. This is what makes cards "fluid but not rigid": the classifier
is free in *wording*, but every card of a given kind has the same
*fields*, so the frontend can render a known component per kind without
the backend needing one schema per category.

Adding a new kind later (e.g. "comparison" for input cards comparing two
products) means adding one class here + one entry in CARD_KIND_BY_CATEGORY
— nothing else in the pipeline needs to change shape.

Field-length guidance: these are meant to render as compact mobile cards,
not articles. Keep bullets short — the classifier prompt enforces this,
these schemas just guard against a model run wild.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

# A single bullet point. Short by design — this is a card, not an essay.
Bullet = Annotated[str, Field(min_length=3, max_length=240)]


class ProblemContent(BaseModel):
    """card_kind = 'problem' — pest, disease, soil issues.

    Something is wrong; the farmer needs to recognise it, act on it,
    and stop it recurring.
    """

    kind: Literal["problem"] = "problem"
    identify: list[Bullet] = Field(
        min_length=1, max_length=5,
        description="How to recognise this problem — visible signs/symptoms.",
    )
    treat: list[Bullet] = Field(
        min_length=1, max_length=5,
        description="Immediate action steps to address it now.",
    )
    prevent: list[Bullet] = Field(
        min_length=1, max_length=4,
        description="Long-term steps to stop it recurring.",
    )


class PracticeContent(BaseModel):
    """card_kind = 'practice' — livestock husbandry, harvest/storage,
    general crop practice.

    Not a problem to fix — a skill or routine the farmer follows.
    """

    kind: Literal["practice"] = "practice"
    overview: str = Field(
        min_length=10, max_length=400,
        description="One or two sentences on what this practice is and why it matters.",
    )
    steps: list[Bullet] = Field(
        min_length=1, max_length=6,
        description="The routine or method, as ordered or unordered steps.",
    )
    tips: list[Bullet] = Field(
        default_factory=list, max_length=4,
        description="Optional extra tips — common mistakes, local adaptations.",
    )


class AdvisoryContent(BaseModel):
    """card_kind = 'advisory' — weather-driven and seasonal alerts.

    Time-bound and action-oriented, not a diagnosis.
    """

    kind: Literal["advisory"] = "advisory"
    summary: str = Field(
        min_length=10, max_length=400,
        description="What's happening or expected, in plain terms.",
    )
    recommended_actions: list[Bullet] = Field(
        min_length=1, max_length=5,
        description="What the farmer should do in response.",
    )
    risk_level: Literal["low", "moderate", "high", "severe"] = Field(
        description="Overall urgency of this advisory.",
    )


class InputContent(BaseModel):
    """card_kind = 'input' — fertiliser, pesticide, seed variety guidance
    tied to a specific input product or category.
    """

    kind: Literal["input"] = "input"
    product_overview: str = Field(
        min_length=10, max_length=400,
        description="What the input is and what it's used for.",
    )
    usage: list[Bullet] = Field(
        min_length=1, max_length=5,
        description="How and when to apply/use it.",
    )
    cautions: list[Bullet] = Field(
        default_factory=list, max_length=4,
        description="Safety notes, dosage limits, incompatibilities.",
    )


# Discriminated union — validate any incoming dict against the right
# shape based on its "kind" field. Use CardContent.model_validate(d)
# to parse content coming back from the classifier or out of the DB.
CardContent = Annotated[
    Union[ProblemContent, PracticeContent, AdvisoryContent, InputContent],
    Field(discriminator="kind"),
]


CONTENT_MODEL_BY_KIND: dict[str, type[BaseModel]] = {
    "problem": ProblemContent,
    "practice": PracticeContent,
    "advisory": AdvisoryContent,
    "input": InputContent,
}
