"""Top-level feedback enhancement API.

Typical usage:

    >>> from guixcoder import FeedbackEnhancer
    >>> enhancer = FeedbackEnhancer(workspace_dir="./examples/sample_react_project")
    >>> enhanced = enhancer.enhance(
    ...     vlm_feedback="Page shows a red error box saying No stock found",
    ...     url="http://localhost:5174/search?q=INVALID",
    ... )
    >>> print(enhanced.formatted_text)

The enhancer composes three modules:

- :class:`~guixcoder.code_graph.CodeGraph` — static view of the source code
- :class:`~guixcoder.dom_extractor.CLIDOMExtractor` — dynamic view of the
  rendered page
- :class:`~guixcoder.alignment.RuleBasedAligner` — maps DOM anomalies to
  code locations

Swap :class:`~guixcoder.alignment.RuleBasedAligner` for
:class:`~guixcoder.alignment.ModelBasedAligner` once a trained model is
available. The downstream API does not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .code_graph import CodeGraph, build_code_graph
from .dom_extractor import CLIDOMExtractor
from .alignment import RuleBasedAligner


@dataclass
class CodeLocation:
    """A single code location attached to a DOM anomaly."""

    file: str
    line_start: int
    line_end: int
    snippet: str
    reason: str = ""


@dataclass
class EnhancedFeedback:
    """Output of :meth:`FeedbackEnhancer.enhance`."""

    original_feedback: str
    structural_analysis: str
    code_locations: List[CodeLocation] = field(default_factory=list)

    @property
    def formatted_text(self) -> str:
        """Single-string representation suitable for LLM prompts."""
        parts = [self.original_feedback.rstrip()]
        if self.structural_analysis:
            parts.append("")
            parts.append(self.structural_analysis)
        return "\n".join(parts)


class FeedbackEnhancer:
    """Enhance raw VLM / GUI-Agent feedback with code-level grounding.

    Parameters
    ----------
    workspace_dir : str
        Path to the source code workspace. Used to build the CodeGraph.
    aligner : object, optional
        Aligner instance. Defaults to :class:`RuleBasedAligner`. Any object
        exposing a ``fuse(code_graph, dom_insights, url_path) -> str``
        interface works.
    """

    def __init__(
        self,
        workspace_dir: str,
        aligner: Optional[object] = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.code_graph: CodeGraph = build_code_graph(workspace_dir)
        self.dom_extractor = CLIDOMExtractor(workspace_dir)
        self.aligner = aligner if aligner is not None else RuleBasedAligner()

    def refresh_code_graph(self) -> None:
        """Rebuild the CodeGraph after the workspace changed."""
        self.code_graph = build_code_graph(self.workspace_dir)

    def enhance(
        self,
        vlm_feedback: str,
        url: str,
        url_path: Optional[str] = None,
    ) -> EnhancedFeedback:
        """Attach code-level grounding to a raw VLM/GUI-Agent feedback string.

        Parameters
        ----------
        vlm_feedback : str
            Raw feedback text produced by a VLM or GUI agent.
        url : str
            Live URL of the running page (used for DOM extraction).
        url_path : str, optional
            Route path like ``/search``. If omitted, inferred from ``url``.

        Returns
        -------
        EnhancedFeedback
            Original feedback plus structural analysis pointing at code.
        """
        if url_path is None:
            url_path = _extract_path(url)

        dom_insights = self.dom_extractor.get_page_insights(url)

        structural_analysis = self.aligner.fuse(
            code_graph=self.code_graph,
            dom_insights=dom_insights or None,
            url_path=url_path,
        )

        code_locations = _extract_locations_from_analysis(
            structural_analysis, self.code_graph
        )

        return EnhancedFeedback(
            original_feedback=vlm_feedback,
            structural_analysis=structural_analysis,
            code_locations=code_locations,
        )


def _extract_path(url: str) -> str:
    """Extract the path portion of a URL (without query string)."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.path or "/"


def _extract_locations_from_analysis(
    analysis: str, graph: CodeGraph
) -> List[CodeLocation]:
    """Parse structural analysis text to extract CodeLocation objects.

    The rule-based analysis produces lines like::

        → Code: src/pages/SearchResultPage.jsx:82-84

    We parse those so callers can render them in the web demo without
    re-parsing the text.
    """
    import re

    locations: List[CodeLocation] = []
    pattern = re.compile(
        r"Code:\s+([^\s:]+):(\d+)(?:-(\d+))?"
    )
    seen = set()
    for m in pattern.finditer(analysis):
        file_path = m.group(1)
        line_start = int(m.group(2))
        line_end = int(m.group(3)) if m.group(3) else line_start
        key = (file_path, line_start, line_end)
        if key in seen:
            continue
        seen.add(key)

        # Best-effort: pull code_snippet from the graph if available
        snippet = ""
        for node in graph.nodes.values():
            if (
                node.file_path == file_path
                and node.line_start == line_start
                and node.line_end == line_end
                and node.code_snippet
            ):
                snippet = node.code_snippet
                break

        locations.append(
            CodeLocation(
                file=file_path,
                line_start=line_start,
                line_end=line_end,
                snippet=snippet,
            )
        )

    return locations
