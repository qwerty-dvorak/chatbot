"""Tier configuration for the ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from .models import IngestionTier


@dataclass(frozen=True)
class IngestOptions:
    """Options controlling ingestion behaviour per tier."""

    tier: IngestionTier
    extract_pdf_text_directly: bool
    ocr_dpi: int
    use_text_embedding: bool
    use_multimodal_embedding: bool
    chunk_strategy: str
    hypothetical_questions_per_chunk: int
    query_enhancements: tuple[str, ...]
    use_reranker: bool


INSTANT_OPTIONS = IngestOptions(
    tier=IngestionTier.INSTANT,
    extract_pdf_text_directly=True,
    ocr_dpi=72,
    use_text_embedding=True,
    use_multimodal_embedding=False,
    chunk_strategy="recursive",
    hypothetical_questions_per_chunk=0,
    query_enhancements=(),
    use_reranker=False,
)

SLOW_OPTIONS = IngestOptions(
    tier=IngestionTier.SLOW,
    extract_pdf_text_directly=False,
    ocr_dpi=150,
    use_text_embedding=True,
    use_multimodal_embedding=True,
    chunk_strategy="sentence_window",
    hypothetical_questions_per_chunk=2,
    query_enhancements=("hyde",),
    use_reranker=True,
)

_TIER_MAP = {IngestionTier.INSTANT: INSTANT_OPTIONS, IngestionTier.SLOW: SLOW_OPTIONS}


def options_for_tier(tier: IngestionTier) -> IngestOptions:
    """Return ingestion options for a tier."""
    return _TIER_MAP[tier]


def tier_from_str(s: str) -> IngestionTier:
    """Parse a tier string into an IngestionTier."""
    try:
        return IngestionTier(s.lower())
    except ValueError:
        valid = ", ".join(t.value for t in IngestionTier)
        msg = f"Unknown tier {s!r}. Valid values: {valid}"
        raise ValueError(msg) from None
