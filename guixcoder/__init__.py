"""GUIxcoder: Bridge the gap between GUI observations and source code.

GUIxcoder turns opaque VLM / GUI-Agent feedback
(e.g. "page has a red error box") into actionable, code-grounded feedback
(e.g. "SearchResultPage.jsx:82-84 renders the error message under
``{notFound && …}``") for LLM-based web-generation agents.

Quickstart:

    from guixcoder import FeedbackEnhancer

    enhancer = FeedbackEnhancer(workspace_dir="./my-app")
    result = enhancer.enhance(
        vlm_feedback="Page shows a red error box saying No stock found",
        url="http://localhost:5173/search?q=INVALID",
    )
    print(result.formatted_text)

See ``docs/DESIGN.md`` for the full design and ``examples/`` for runnable
demos.
"""

from .feedback_enhancer import (
    FeedbackEnhancer,
    EnhancedFeedback,
    CodeLocation,
)
from .code_graph import CodeGraph, build_code_graph, graph_to_context
from .dom_extractor import CLIDOMExtractor
from .alignment import RuleBasedAligner, ModelBasedAligner

__version__ = "0.1.0"

__all__ = [
    "FeedbackEnhancer",
    "EnhancedFeedback",
    "CodeLocation",
    "CodeGraph",
    "build_code_graph",
    "graph_to_context",
    "CLIDOMExtractor",
    "RuleBasedAligner",
    "ModelBasedAligner",
    "__version__",
]
