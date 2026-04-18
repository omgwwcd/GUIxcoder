"""DOM → Code alignment strategies.

Two strategies ship with GUIxcoder:

- :class:`RuleBasedAligner` (Phase 1 baseline): keyword / structural matching
  on top of a CodeGraph. Zero training. Works today.
- :class:`ModelBasedAligner` (Phase 2): contrastive alignment model trained on
  (code snippet, DOM subtree) pairs. Interface defined; training pipeline
  sketched in ``guixcoder/alignment/model_based.py``.
"""

from .rule_based import RuleBasedAligner
from .model_based import ModelBasedAligner

__all__ = ["RuleBasedAligner", "ModelBasedAligner"]
