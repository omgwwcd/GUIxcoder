"""Rule-based DOM → Code alignment (Phase 1 baseline).

Pipeline:
1. Take a DOM snapshot of the running page.
2. Detect anomalies in the DOM (error messages, missing elements, broken
   renders).
3. For each anomaly, query the CodeGraph to locate the relevant source code.
4. Produce an actionable feedback block that pinpoints file + line + snippet.

This baseline does no training - it relies on keyword/structural matching on
top of a tree-sitter CodeGraph. See :mod:`guixcoder.alignment.model_based`
for the trained alignment model interface (Phase 2).
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ..code_graph import CodeGraph, GraphNode


@dataclass
class DOMAnomaly:
    """一个 DOM 异常"""
    kind: str           # error_message / missing_element / empty_section / broken_render
    description: str    # 人类可读描述
    dom_tag: str = ""
    dom_text: str = ""
    dom_classes: list = None
    dom_id: str = ""


class RuleBasedAligner:
    """Rule-based aligner between DOM anomalies and CodeGraph nodes.

    Kept as a lightweight Phase 1 baseline. Use :class:`ModelBasedAligner`
    once a contrastive alignment model has been trained.
    """


    def fuse(
        self,
        code_graph: Optional[CodeGraph] = None,
        dom_insights: Optional[Dict] = None,
        url_path: str = "/",
    ) -> str:
        if not code_graph:
            return ""

        page_graph = code_graph.filter_by_route(url_path)
        page_name = code_graph.get_page_component_name(url_path) or "unknown"

        if not dom_insights or not dom_insights.get("dom_available"):
            return self._fallback_summary(page_graph, page_name, url_path)

        # Step 1: 检测 DOM 异常
        anomalies = self._detect_anomalies(page_graph, dom_insights)

        if not anomalies:
            return (
                f"**Structural Analysis for '{url_path}' → {page_name}:**\n\n"
                f"No anomalies detected. DOM rendered {dom_insights.get('element_count', 0)} elements, "
                f"{dom_insights.get('interactive_count', 0)} interactive."
            )

        # Step 2: 只拿异常节点查 CodeGraph
        sections = []
        sections.append(f"**Structural Analysis for '{url_path}' → {page_name}:**")
        sections.append(f"Detected {len(anomalies)} anomalies:\n")

        for anomaly in anomalies:
            entry = self._trace_anomaly_to_code(anomaly, page_graph)
            sections.append(entry)

        return "\n".join(sections)

    def _detect_anomalies(self, graph: CodeGraph, dom_insights: Dict) -> List[DOMAnomaly]:
        """
        分析 DOM，识别异常：
        1. 错误信息（红色文本、error 类名、错误关键词）
        2. 代码定义了但 DOM 没渲染的交互元素
        3. DOM 为空或极少元素
        """
        anomalies = []
        elements = dom_insights.get("elements", [])

        # ── 异常1: DOM 中的错误信息 ──
        error_keywords = ["error", "not found", "failed", "invalid", "cannot", "unable", "exception", "404", "500"]
        for elem in elements:
            text = elem.get("text", "").lower()
            classes = elem.get("classes", [])
            # 检查文本内容是否包含错误关键词
            if any(kw in text for kw in error_keywords):
                anomalies.append(DOMAnomaly(
                    kind="error_message",
                    description=f"Error text in DOM: \"{elem['text'][:80]}\"",
                    dom_tag=elem["tag"],
                    dom_text=elem.get("text", ""),
                    dom_classes=classes,
                ))
            # 检查 class 是否包含 error 相关
            if any("error" in c or "alert" in c or "danger" in c for c in classes):
                anomalies.append(DOMAnomaly(
                    kind="error_message",
                    description=f"Error-styled element: <{elem['tag']} class=\"{' '.join(classes)}\">",
                    dom_tag=elem["tag"],
                    dom_classes=classes,
                ))

        # ── 异常2: 代码有但 DOM 没渲染的交互元素 ──
        dom_texts = set()
        for elem in elements:
            if elem.get("text"):
                dom_texts.add(elem["text"].strip().lower())

        interactive_tags = {"button", "input", "select", "textarea", "form"}
        for node in graph.nodes.values():
            if node.kind != "component" or node.name not in interactive_tags:
                continue
            # 用代码片段中的文本去 DOM 里找
            if node.code_snippet:
                # 提取代码中的显示文本（> 和 < 之间的内容）
                import re
                texts_in_code = re.findall(r'>([^<]{2,})<', node.code_snippet)
                for t in texts_in_code:
                    t_clean = t.strip().lower()
                    if t_clean and t_clean not in dom_texts and "{" not in t_clean:
                        anomalies.append(DOMAnomaly(
                            kind="missing_element",
                            description=f"Code has <{node.name}> \"{t.strip()[:40]}\" but not found in DOM",
                            dom_tag=node.name,
                            dom_text=t.strip(),
                        ))
                        break  # 每个代码节点只报一次

        # ── 异常3: DOM 整体异常 ──
        total = dom_insights.get("element_count", 0)
        if total == 0:
            anomalies.append(DOMAnomaly(
                kind="broken_render",
                description="DOM is completely empty - page failed to render",
            ))
        elif total < 5:
            anomalies.append(DOMAnomaly(
                kind="broken_render",
                description=f"DOM has only {total} elements - page may be partially broken",
            ))

        # 去重
        seen = set()
        unique = []
        for a in anomalies:
            key = (a.kind, a.description)
            if key not in seen:
                seen.add(key)
                unique.append(a)

        return unique

    def _trace_anomaly_to_code(self, anomaly: DOMAnomaly, graph: CodeGraph) -> str:
        """
        拿一个异常节点查 CodeGraph，返回异常描述 + 对应代码位置。
        """
        lines = []

        # 异常描述
        icon = {"error_message": "!!", "missing_element": "??", "broken_render": "XX", "empty_section": "  "}.get(anomaly.kind, "  ")
        lines.append(f"[{icon}] {anomaly.description}")

        # 查 CodeGraph
        matches = self._find_related_code(anomaly, graph)

        if matches:
            for node, reason in matches[:3]:
                lines.append(f"    → Code: {node.file_path}:{node.line_start}-{node.line_end}")
                if reason:
                    lines.append(f"      ({reason})")
                # 显示代码片段（前 3 行）
                if node.code_snippet:
                    snippet_lines = node.code_snippet.strip().split("\n")[:3]
                    for sl in snippet_lines:
                        lines.append(f"      | {sl}")
                # 显示依赖关系
                neighbors = graph.get_neighbors(node.id)
                for rel, neighbor in neighbors[:2]:
                    if rel in ("CALLS", "RENDERS", "IMPORTS"):
                        lines.append(f"    → {rel} → {neighbor.name} ({neighbor.file_path}:{neighbor.line_start})")
        else:
            lines.append(f"    → (no matching code found in CodeGraph)")

        return "\n".join(lines)

    def _find_related_code(self, anomaly: DOMAnomaly, graph: CodeGraph) -> List[Tuple[GraphNode, str]]:
        """查找与异常相关的代码节点"""
        results = []

        # 按 class/id 查
        if anomaly.dom_classes:
            for cls in anomaly.dom_classes:
                for node in graph.find_by_class_or_id(class_name=cls):
                    results.append((node, f"matched class '{cls}'"))
        if anomaly.dom_id:
            for node in graph.find_by_class_or_id(element_id=anomaly.dom_id):
                results.append((node, f"matched id '{anomaly.dom_id}'"))

        # 按文本内容查（用关键词片段，兼容动态拼接的文本）
        if anomaly.dom_text and len(anomaly.dom_text) > 3:
            # 提取关键词片段（取前几个有意义的词）
            import re
            words = re.findall(r'[a-zA-Z]{3,}', anomaly.dom_text)
            search_fragments = words[:4]  # 取前 4 个关键词

            candidates = []
            for node in graph.nodes.values():
                if node.kind not in ("component", "function"):
                    continue
                if not node.code_snippet:
                    continue
                matched_words = sum(1 for w in search_fragments if w.lower() in node.code_snippet.lower())
                if matched_words >= min(2, len(search_fragments)):
                    candidates.append((node, matched_words))

            if candidates:
                # 优先选最小的匹配（最精确），而非整个大组件
                best = min(candidates, key=lambda x: len(x[0].code_snippet))
                results.append((best[0], f"code contains keywords from \"{anomaly.dom_text[:30]}\""))

        # 按 tag 查（仅语义标签：nav/main/footer/form，不匹配 p/div 等通用标签）
        if not results and anomaly.dom_tag in ("nav", "main", "footer", "form", "header", "section"):
            for node in graph.nodes.values():
                if node.kind == "component" and node.name == anomaly.dom_tag:
                    results.append((node, f"matched tag <{anomaly.dom_tag}>"))
                    break

        # 去重
        seen = set()
        unique = []
        for node, reason in results:
            if node.id not in seen:
                seen.add(node.id)
                unique.append((node, reason))

        return unique

    def _fallback_summary(self, graph: CodeGraph, page_name: str, url_path: str) -> str:
        funcs = [n for n in graph.nodes.values() if n.kind == "function" and n.name != "<anonymous>"]
        if not funcs:
            return ""
        lines = [f"**Structural Analysis for '{url_path}' → {page_name} (no DOM available):**"]
        for f in funcs[:8]:
            lines.append(f"  - {f.name} ({f.file_path}:{f.line_start})")
        return "\n".join(lines)
