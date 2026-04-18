"""
code_graph.py - 基于 tree-sitter 的代码知识图谱

从源码构建内存图：
  节点 = 文件、组件、函数、import
  边 = CONTAINS / CALLS / RENDERS / IMPORTS / USES_STYLE

用途：DOM 元素出问题时，通过 className/id 匹配到图节点，
取出该节点及上下游的代码片段作为反馈上下文。
"""

import os
import re
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from tree_sitter import Language, Parser, Node as TSNode

import tree_sitter_javascript as tsjs
import tree_sitter_html as tshtml
import tree_sitter_css as tscss

# ── 语言初始化 ──
JS_LANG = Language(tsjs.language())
HTML_LANG = Language(tshtml.language())
CSS_LANG = Language(tscss.language())


# ── 数据模型 ──

@dataclass
class GraphNode:
    """图中的一个节点"""
    id: str                     # 唯一标识，如 "src/App.jsx::SearchForm"
    kind: str                   # module / component / function / import
    name: str
    file_path: str
    line_start: int = 0
    line_end: int = 0
    code_snippet: str = ""      # 源码片段
    properties: Dict = field(default_factory=dict)  # className, id 等


@dataclass
class GraphEdge:
    """图中的一条边"""
    source: str     # node id
    relation: str   # CONTAINS / CALLS / RENDERS / IMPORTS / USES_STYLE
    target: str     # node id


class CodeGraph:
    """内存代码知识图谱"""

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: List[GraphEdge] = []

    def add_node(self, node: GraphNode):
        self.nodes[node.id] = node

    def add_edge(self, source: str, relation: str, target: str):
        self.edges.append(GraphEdge(source, relation, target))

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def get_neighbors(self, node_id: str, relation: Optional[str] = None) -> List[Tuple[str, GraphNode]]:
        """获取某节点的所有邻居（出边+入边）"""
        result = []
        for e in self.edges:
            if relation and e.relation != relation:
                continue
            if e.source == node_id and e.target in self.nodes:
                result.append((e.relation, self.nodes[e.target]))
            elif e.target == node_id and e.source in self.nodes:
                result.append((e.relation, self.nodes[e.source]))
        return result

    def find_by_class_or_id(self, class_name: str = "", element_id: str = "") -> List[GraphNode]:
        """通过 className 或 id 查找组件节点"""
        results = []
        for node in self.nodes.values():
            if node.kind != "component":
                continue
            props = node.properties
            if class_name and class_name in props.get("class_names", []):
                results.append(node)
            if element_id and element_id == props.get("id", ""):
                results.append(node)
        return results

    def get_context_for_node(self, node_id: str, depth: int = 1) -> str:
        """
        获取某节点及其上下游的完整上下文。
        这是给 LLM 看的反馈文本。
        """
        node = self.get_node(node_id)
        if not node:
            return ""

        lines = []
        lines.append(f"[{node.kind}] {node.name} ({node.file_path}:{node.line_start}-{node.line_end})")
        if node.code_snippet:
            lines.append(f"```\n{node.code_snippet}\n```")

        if depth > 0:
            neighbors = self.get_neighbors(node_id)
            for rel, neighbor in neighbors:
                lines.append(f"  ── {rel} ── [{neighbor.kind}] {neighbor.name} ({neighbor.file_path}:{neighbor.line_start})")
                if neighbor.code_snippet and len(neighbor.code_snippet) < 500:
                    lines.append(f"  ```\n  {neighbor.code_snippet}\n  ```")

        return "\n".join(lines)

    def get_routes(self) -> Dict[str, str]:
        """
        提取路由映射：path → 组件名
        从 <Route path="/" element={<HomePage />} /> 这类 JSX 中提取
        """
        routes = {}
        for node in self.nodes.values():
            if node.kind == "component" and node.name == "Route":
                snippet = node.code_snippet
                path_m = re.search(r'path=["\']([^"\']+)["\']', snippet)
                elem_m = re.search(r'element=\{<(\w+)', snippet)
                if path_m and elem_m:
                    routes[path_m.group(1)] = elem_m.group(1)
        return routes

    def get_page_component_name(self, url_path: str) -> Optional[str]:
        """根据 URL path 返回对应的页面组件名"""
        routes = self.get_routes()
        # 精确匹配
        if url_path in routes:
            return routes[url_path]
        # 去掉 query string 再匹配
        clean = url_path.split("?")[0]
        if clean in routes:
            return routes[clean]
        return None

    def filter_by_route(self, url_path: str) -> "CodeGraph":
        """
        返回只包含当前路由页面相关节点的子图。
        策略：按文件路径过滤——页面组件所在文件 + App/main 等共享文件。
        """
        page_comp = self.get_page_component_name(url_path)
        if not page_comp:
            return self

        # 找页面组件所在文件
        page_files = set()
        for node in self.nodes.values():
            if node.name == page_comp and node.kind in ("component", "function"):
                page_files.add(node.file_path)

        # 页面文件 import 的数据/工具文件也包含（不含其他页面）
        page_only = set(page_files)
        for e in self.edges:
            if e.relation == "IMPORTS" and e.source in page_only:
                target = self.get_node(e.target)
                if target and target.name.startswith("./"):
                    for n in self.nodes.values():
                        if n.kind == "module" and target.name.lstrip("./") in n.file_path:
                            # 排除其他页面组件文件
                            if "/pages/" not in n.file_path or page_comp.lower() in n.file_path.lower():
                                page_files.add(n.file_path)

        # 共享文件：App、main、index.html、CSS
        for node in self.nodes.values():
            if node.kind == "module":
                name = node.name.lower()
                if any(k in name for k in ("app", "main", "index", ".css")):
                    page_files.add(node.file_path)

        sub = CodeGraph()
        for node in self.nodes.values():
            if node.file_path in page_files:
                sub.add_node(node)
        for e in self.edges:
            if e.source in sub.nodes and e.target in sub.nodes:
                sub.add_edge(e.source, e.relation, e.target)

        return sub

    def summary(self) -> str:
        """图的统计摘要"""
        kinds = {}
        for n in self.nodes.values():
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        rels = {}
        for e in self.edges:
            rels[e.relation] = rels.get(e.relation, 0) + 1
        return (
            f"Nodes: {len(self.nodes)} ({', '.join(f'{k}={v}' for k, v in kinds.items())})\n"
            f"Edges: {len(self.edges)} ({', '.join(f'{k}={v}' for k, v in rels.items())})"
        )


# ── 解析器 ──

def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _snippet(source: bytes, node: TSNode, max_lines: int = 20) -> str:
    """提取 AST 节点对应的源码片段"""
    start = node.start_byte
    end = node.end_byte
    text = source[start:end].decode("utf-8", errors="ignore")
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"  ... ({len(lines) - max_lines} more lines)"]
    return "\n".join(lines)


def _parse_jsx(source: bytes, file_path: str, graph: CodeGraph):
    """解析 JSX/JS/TS 文件，提取组件、函数、import 关系"""
    parser = Parser(JS_LANG)
    tree = parser.parse(source)
    root = tree.root_node

    module_id = file_path
    graph.add_node(GraphNode(
        id=module_id, kind="module", name=os.path.basename(file_path),
        file_path=file_path, line_start=1,
        line_end=source.count(b"\n") + 1,
    ))

    # 遍历 AST
    _walk_js_node(root, source, file_path, module_id, graph, set())


def _walk_js_node(node: TSNode, source: bytes, file_path: str,
                  module_id: str, graph: CodeGraph, visited: Set[int]):
    """递归遍历 JS/JSX AST"""
    if id(node) in visited:
        return
    visited.add(id(node))

    # ── import 声明 ──
    if node.type == "import_statement":
        source_node = node.child_by_field_name("source")
        if source_node:
            import_from = source_node.text.decode().strip("'\"")
            imp_id = f"{file_path}::import::{import_from}"
            graph.add_node(GraphNode(
                id=imp_id, kind="import", name=import_from,
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                code_snippet=_snippet(source, node, 3),
            ))
            graph.add_edge(module_id, "IMPORTS", imp_id)

    # ── 函数声明 / 箭头函数 ──
    elif node.type in ("function_declaration", "arrow_function", "function"):
        name_node = node.child_by_field_name("name")
        func_name = name_node.text.decode() if name_node else "<anonymous>"

        # 对于 const Foo = () => ... 的形式
        if func_name == "<anonymous>" and node.parent and node.parent.type == "variable_declarator":
            decl_name = node.parent.child_by_field_name("name")
            if decl_name:
                func_name = decl_name.text.decode()

        func_id = f"{file_path}::{func_name}"
        snippet = _snippet(source, node)

        # 判断是否是 React 组件（首字母大写 + 返回 JSX）
        is_component = func_name[0:1].isupper() and _contains_jsx(node)
        kind = "component" if is_component else "function"

        # 提取 className 和 id
        class_names = _extract_class_names(node, source)
        element_id = _extract_element_id(node, source)

        graph.add_node(GraphNode(
            id=func_id, kind=kind, name=func_name,
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            code_snippet=snippet,
            properties={"class_names": class_names, "id": element_id},
        ))
        graph.add_edge(module_id, "CONTAINS", func_id)

        # 提取函数体中的调用关系
        _extract_calls(node, source, file_path, func_id, graph)

        # 提取 JSX 中渲染的子组件
        _extract_renders(node, source, file_path, func_id, graph)

    # ── JSX 元素（顶层，非函数内的）──
    elif node.type in ("jsx_element", "jsx_self_closing_element"):
        _process_jsx_element(node, source, file_path, module_id, graph)

    # 递归子节点
    for child in node.children:
        _walk_js_node(child, source, file_path, module_id, graph, visited)


def _contains_jsx(node: TSNode) -> bool:
    """检查节点内是否包含 JSX"""
    if node.type.startswith("jsx_"):
        return True
    for child in node.children:
        if _contains_jsx(child):
            return True
    return False


def _extract_class_names(node: TSNode, source: bytes) -> List[str]:
    """从 JSX 中提取所有 className 值"""
    classes = []
    text = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
    for m in re.finditer(r'className=["\']([^"\']+)["\']', text):
        classes.extend(m.group(1).split())
    return classes


def _extract_element_id(node: TSNode, source: bytes) -> str:
    """从 JSX 中提取 id 属性"""
    text = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
    m = re.search(r'\bid=["\']([^"\']+)["\']', text)
    return m.group(1) if m else ""


def _extract_calls(node: TSNode, source: bytes, file_path: str,
                   caller_id: str, graph: CodeGraph):
    """提取函数调用关系"""
    if node.type == "call_expression":
        func_node = node.child_by_field_name("function")
        if func_node:
            call_name = func_node.text.decode()
            # 只记录简单函数名调用（非方法调用如 console.log）
            if "." not in call_name and call_name[0:1].islower():
                target_id = f"{file_path}::{call_name}"
                graph.add_edge(caller_id, "CALLS", target_id)

    for child in node.children:
        _extract_calls(child, source, file_path, caller_id, graph)


def _extract_renders(node: TSNode, source: bytes, file_path: str,
                     parent_id: str, graph: CodeGraph):
    """提取组件渲染的子组件（JSX 中的大写标签）"""
    if node.type in ("jsx_element", "jsx_self_closing_element"):
        open_tag = node.child_by_field_name("open_tag") or node
        tag_name_node = None
        for child in open_tag.children:
            if child.type in ("identifier", "jsx_identifier"):
                tag_name_node = child
                break
            elif child.type == "member_expression":
                tag_name_node = child
                break

        if tag_name_node:
            tag_name = tag_name_node.text.decode()
            if tag_name[0:1].isupper():
                child_id = f"{file_path}::{tag_name}"
                graph.add_edge(parent_id, "RENDERS", child_id)

    for child in node.children:
        _extract_renders(child, source, file_path, parent_id, graph)


def _process_jsx_element(node: TSNode, source: bytes, file_path: str,
                         parent_id: str, graph: CodeGraph):
    """处理 JSX 元素节点，提取为组件"""
    open_tag = node.child_by_field_name("open_tag") or node
    tag_name = ""
    for child in open_tag.children:
        if child.type in ("identifier", "jsx_identifier"):
            tag_name = child.text.decode()
            break

    if not tag_name:
        return

    class_names = _extract_class_names(node, source)
    element_id = _extract_element_id(node, source)

    comp_id = f"{file_path}::jsx::{tag_name}:{node.start_point[0]}"
    graph.add_node(GraphNode(
        id=comp_id, kind="component", name=tag_name,
        file_path=file_path,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        code_snippet=_snippet(source, node, 10),
        properties={"class_names": class_names, "id": element_id},
    ))
    graph.add_edge(parent_id, "CONTAINS", comp_id)


def _parse_html(source: bytes, file_path: str, graph: CodeGraph):
    """解析 HTML 文件"""
    parser = Parser(HTML_LANG)
    tree = parser.parse(source)

    module_id = file_path
    graph.add_node(GraphNode(
        id=module_id, kind="module", name=os.path.basename(file_path),
        file_path=file_path, line_start=1,
        line_end=source.count(b"\n") + 1,
    ))

    _walk_html_node(tree.root_node, source, file_path, module_id, graph)


def _walk_html_node(node: TSNode, source: bytes, file_path: str,
                    parent_id: str, graph: CodeGraph):
    """递归遍历 HTML AST"""
    if node.type == "element":
        start_tag = node.child_by_field_name("start_tag") or node
        tag_name = ""
        for child in start_tag.children:
            if child.type == "tag_name":
                tag_name = child.text.decode()
                break

        if tag_name:
            class_names = []
            element_id = ""
            for attr in start_tag.children:
                if attr.type == "attribute":
                    name_node = attr.child_by_field_name("name")
                    value_node = attr.child_by_field_name("value")
                    if name_node and value_node:
                        attr_name = name_node.text.decode()
                        attr_value = value_node.text.decode().strip("'\"")
                        if attr_name == "class":
                            class_names = attr_value.split()
                        elif attr_name == "id":
                            element_id = attr_value

            comp_id = f"{file_path}::{tag_name}:{node.start_point[0]}"
            graph.add_node(GraphNode(
                id=comp_id, kind="component", name=tag_name,
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                code_snippet=_snippet(source, node, 10),
                properties={"class_names": class_names, "id": element_id},
            ))
            graph.add_edge(parent_id, "CONTAINS", comp_id)
            parent_id = comp_id

    for child in node.children:
        _walk_html_node(child, source, file_path, parent_id, graph)


def _parse_css(source: bytes, file_path: str, graph: CodeGraph):
    """解析 CSS 文件，提取选择器"""
    parser = Parser(CSS_LANG)
    tree = parser.parse(source)

    module_id = file_path
    graph.add_node(GraphNode(
        id=module_id, kind="module", name=os.path.basename(file_path),
        file_path=file_path, line_start=1,
        line_end=source.count(b"\n") + 1,
    ))

    for child in tree.root_node.children:
        if child.type == "rule_set":
            # 提取选择器
            for sel_child in child.children:
                if sel_child.type == "selectors":
                    sel_text = sel_child.text.decode()
                    sel_id = f"{file_path}::css::{sel_text}:{child.start_point[0]}"
                    graph.add_node(GraphNode(
                        id=sel_id, kind="style", name=sel_text,
                        file_path=file_path,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        code_snippet=_snippet(source, child, 10),
                    ))
                    graph.add_edge(module_id, "CONTAINS", sel_id)


# ── 公开接口 ──

def build_code_graph(workspace_dir: str) -> CodeGraph:
    """
    扫描 workspace，用 tree-sitter 解析所有文件，构建 CodeGraph。
    替代原来的 extract_workspace_structure()。
    """
    graph = CodeGraph()

    for root, _, files in os.walk(workspace_dir):
        if "node_modules" in root or "dist" in root or ".git" in root:
            continue
        for filename in files:
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, workspace_dir)

            try:
                source = _read_file(filepath)
                if filename.endswith((".jsx", ".tsx", ".js", ".ts")):
                    _parse_jsx(source, rel_path, graph)
                elif filename.endswith(".html"):
                    _parse_html(source, rel_path, graph)
                elif filename.endswith(".css"):
                    _parse_css(source, rel_path, graph)
            except Exception as e:
                print(f"[CodeGraph] 解析 {rel_path} 失败: {e}")

    return graph


def graph_to_context(graph: CodeGraph) -> str:
    """将 CodeGraph 转为 LLM 可读的文本上下文"""
    lines = ["## Code Graph Summary:", graph.summary(), ""]

    # 列出组件
    components = [n for n in graph.nodes.values() if n.kind == "component"]
    if components:
        lines.append(f"Components ({len(components)}):")
        for c in components[:15]:
            cls = ", ".join(c.properties.get("class_names", []))
            eid = c.properties.get("id", "")
            loc = f"{c.file_path}:{c.line_start}"
            extra = []
            if cls:
                extra.append(f'class="{cls}"')
            if eid:
                extra.append(f'id="{eid}"')
            lines.append(f"  - {c.name} ({loc}) {' '.join(extra)}")
        lines.append("")

    # 列出函数
    functions = [n for n in graph.nodes.values() if n.kind == "function"]
    if functions:
        lines.append(f"Functions ({len(functions)}):")
        for f in functions[:10]:
            lines.append(f"  - {f.name} ({f.file_path}:{f.line_start})")
        lines.append("")

    # 列出 import 关系
    imports = [e for e in graph.edges if e.relation == "IMPORTS"]
    if imports:
        lines.append(f"Import relations ({len(imports)}):")
        for e in imports[:10]:
            target = graph.get_node(e.target)
            if target:
                lines.append(f"  - {e.source} → {target.name}")

    return "\n".join(lines)


def get_error_context(graph: CodeGraph, class_name: str = "",
                      element_id: str = "", component_name: str = "") -> str:
    """
    给定一个 DOM 元素的标识，返回相关代码上下文。
    用于反馈：DOM 有问题 → 定位代码 → 给 LLM 完整上下文。
    """
    # 先按 className/id 查找
    matches = graph.find_by_class_or_id(class_name, element_id)

    # 也按组件名查找
    if component_name:
        for node in graph.nodes.values():
            if node.name.lower() == component_name.lower() and node not in matches:
                matches.append(node)

    if not matches:
        return f"No code found for class='{class_name}' id='{element_id}' name='{component_name}'"

    parts = []
    for node in matches[:3]:  # 最多 3 个匹配
        parts.append(graph.get_context_for_node(node.id, depth=1))

    return "\n\n".join(parts)
