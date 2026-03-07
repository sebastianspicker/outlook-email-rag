"""NLP-based entity extraction using spaCy with regex fallback."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .entity_extractor import ExtractedEntity, extract_entities

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# spaCy entity types we care about, mapped to our schema
_SPACY_TYPE_MAP = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "location",
    "LOC": "location",
    "MONEY": "money",
    "EVENT": "event",
}

# Types we skip (too noisy or not useful)
_SPACY_SKIP_TYPES = frozenset({"DATE", "TIME", "CARDINAL", "ORDINAL", "QUANTITY", "PERCENT", "LANGUAGE"})

_nlp_model = None
_nlp_load_attempted = False


def _get_nlp():
    """Lazy-load spaCy model. Returns None if spaCy is not available."""
    global _nlp_model, _nlp_load_attempted
    if _nlp_load_attempted:
        return _nlp_model
    _nlp_load_attempted = True
    try:
        import spacy

        _nlp_model = spacy.load("en_core_web_sm", disable=["parser", "lemmatizer"])
        logger.info("spaCy model loaded: en_core_web_sm")
    except (ImportError, OSError) as exc:
        logger.debug("spaCy not available, will use regex-only extraction: %s", exc)
        _nlp_model = None
    return _nlp_model


def is_spacy_available() -> bool:
    """Check if spaCy and en_core_web_sm are available."""
    return _get_nlp() is not None


def _normalize_person(text: str) -> str:
    """Normalize a person name for dedup."""
    # Strip titles/prefixes
    parts = text.strip().split()
    # Remove common titles
    titles = {"mr", "mrs", "ms", "dr", "prof", "sir", "dame"}
    cleaned = [p for p in parts if p.lower().rstrip(".") not in titles]
    return " ".join(cleaned).lower().strip() if cleaned else text.lower().strip()


def _normalize_entity(text: str, entity_type: str) -> str:
    """Normalize an entity for dedup based on type."""
    if entity_type == "person":
        return _normalize_person(text)
    return text.lower().strip()


def extract_spacy_entities(text: str) -> list[ExtractedEntity]:
    """Extract entities using spaCy NER.

    Returns empty list if spaCy is not available.
    """
    nlp = _get_nlp()
    if nlp is None:
        return []

    if not text or len(text.strip()) < 3:
        return []

    # Truncate very long texts to avoid memory issues
    max_chars = 100_000
    if len(text) > max_chars:
        text = text[:max_chars]

    doc = nlp(text)
    entities: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()

    for ent in doc.ents:
        spacy_label = ent.label_
        if spacy_label in _SPACY_SKIP_TYPES:
            continue
        our_type = _SPACY_TYPE_MAP.get(spacy_label)
        if our_type is None:
            continue

        ent_text = ent.text.strip()
        if not ent_text or len(ent_text) < 2:
            continue

        # Skip single-character or purely numeric entities
        if ent_text.isdigit():
            continue

        normalized = _normalize_entity(ent_text, our_type)
        key = (normalized, our_type)
        if key in seen:
            continue
        seen.add(key)

        entities.append(ExtractedEntity(
            text=ent_text,
            entity_type=our_type,
            normalized_form=normalized,
        ))

    return entities


def extract_nlp_entities(
    text: str, sender_email: str | None = None
) -> list[ExtractedEntity]:
    """Extract entities using spaCy NER + regex, with deduplication.

    Attempts spaCy first for PERSON, ORG, GPE, MONEY, EVENT entities.
    Always runs regex for URLs, phones, mentions, email addresses.
    Falls back to regex-only if spaCy is not available.

    Args:
        text: Email body text to extract entities from.
        sender_email: Sender email for organization-from-domain extraction.

    Returns:
        Deduplicated list of ExtractedEntity objects.
    """
    # Always get regex entities (URLs, phones, mentions, emails, org-from-domain)
    regex_entities = extract_entities(text, sender_email)

    # Try spaCy for NLP entities
    spacy_entities = extract_spacy_entities(text)

    if not spacy_entities:
        return regex_entities

    # Merge: spaCy entities first, then regex entities, dedup by (normalized, type)
    merged: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()

    # spaCy entities take priority for overlapping types (e.g., organization)
    for entity in spacy_entities:
        key = (entity.normalized_form, entity.entity_type)
        if key not in seen:
            seen.add(key)
            merged.append(entity)

    # Regex entities fill in types spaCy doesn't cover (url, phone, mention, email)
    for entity in regex_entities:
        key = (entity.normalized_form, entity.entity_type)
        if key not in seen:
            seen.add(key)
            merged.append(entity)

    return merged


def reset_model_cache() -> None:
    """Reset the model cache (useful for testing)."""
    global _nlp_model, _nlp_load_attempted
    _nlp_model = None
    _nlp_load_attempted = False
