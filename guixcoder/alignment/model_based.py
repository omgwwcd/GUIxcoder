"""Trained DOM → Code alignment model (Phase 2 interface).

This module defines the *interface* for a learned alignment model. The full
training pipeline (contrastive learning on (code, DOM) pairs) is documented
in ``docs/DESIGN.md``.

Design rationale
----------------
Pre-trained code models (CodeBERT, UniXcoder, StarCoder) understand code
syntax, but they do not natively know "what DOM this JSX renders to". That
mapping must be learned from data. The model here is a dual-tower encoder:

- ``encode_code(text)`` → dense vector for a code snippet
- ``encode_dom(html)`` → dense vector for a DOM subtree

Cosine similarity in the shared space is used for retrieval.

Why keep this as a stub
-----------------------
Training is a week-scale effort (data collection + compute). Phase 1 ships a
rule-based aligner that already improves feedback quality. This interface
lets users plug in a trained model whenever it becomes available, without
changing downstream code in :class:`guixcoder.FeedbackEnhancer`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..code_graph import CodeGraph, GraphNode


@dataclass
class AlignmentResult:
    """A single DOM → Code alignment result."""

    dom_snippet: str
    code_node: GraphNode
    score: float


class ModelBasedAligner:
    """Contrastive dual-tower aligner (not yet trained).

    Load a trained checkpoint via :meth:`from_checkpoint`. If no checkpoint
    is available, methods raise :class:`NotImplementedError`. Falling back
    to :class:`RuleBasedAligner` is the expected behaviour when no model is
    loaded — see :func:`guixcoder.FeedbackEnhancer`.
    """

    def __init__(self, model=None, code_encoder=None, dom_encoder=None) -> None:
        self.model = model
        self.code_encoder = code_encoder
        self.dom_encoder = dom_encoder

    @classmethod
    def from_checkpoint(cls, path: str) -> "ModelBasedAligner":
        """Load a trained checkpoint.

        Not yet implemented. Training pipeline lives in
        ``scripts/train_alignment.py`` (planned, Phase 2).
        """
        raise NotImplementedError(
            "Trained alignment checkpoint not available yet. "
            "Use RuleBasedAligner for now; see docs/DESIGN.md for the training "
            "plan."
        )

    # ── Public API (shape is stable so downstream code can depend on it) ──

    def encode_code(self, text: str):
        raise NotImplementedError

    def encode_dom(self, html: str):
        raise NotImplementedError

    def align(
        self,
        graph: CodeGraph,
        dom_snippets: List[str],
        top_k: int = 3,
    ) -> List[Tuple[str, List[AlignmentResult]]]:
        """Align every DOM snippet to top-k code nodes."""
        raise NotImplementedError
