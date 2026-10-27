# -*- coding: utf-8 -*-
"""Gestalt-Net — dependency-free retrospective memory for long texts.

Public API re-exported from :mod:`gestalt.gestalt`. Importing this package
pulls in nothing beyond the Python standard library.
"""

from .gestalt import (
    CHARS,
    CLOSED,
    OBJECTS,
    POVERTY,
    UNRESOLVED,
    Concept,
    Decision,
    GestaltNet,
    RandomScorer,
    Sentence,
    Triad,
    TrainedScorer,
    feat,
    features_for,
    find_chars,
    find_objects,
    has_cue,
    norm,
    sigmoid,
    tokens,
)

__all__ = [
    "CHARS", "CLOSED", "OBJECTS", "POVERTY", "UNRESOLVED",
    "Concept", "Decision", "GestaltNet", "RandomScorer", "Sentence", "Triad",
    "TrainedScorer", "feat", "features_for", "find_chars", "find_objects",
    "has_cue", "norm", "sigmoid", "tokens",
]

__version__ = "1.0.0"
