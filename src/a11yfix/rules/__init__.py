"""Accessibility rules. Each rule lives in its own module and self-registers."""

# Import all rule modules to trigger registration.
from a11yfix.rules import (  # noqa: F401
    alt_text,
    captions_media,
    color_contrast,
    decorative_flag,
    document_language,
    document_title,
    drm_irm,
    floating_objects,
    heading_structure,
    link_text,
    list_semantics,
    merged_cells,
    nontext_contrast,
    reading_order,
    slide_title,
    table_headers,
)
from a11yfix.rules.base import REGISTRY, Rule, register_rule  # noqa: F401
