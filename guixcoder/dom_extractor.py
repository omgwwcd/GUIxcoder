"""
cli_dom_extractor.py - 利用chromium-browser CLI提取真实DOM结构

在网站运行后，通过headless浏览器获取实际DOM树，
与代码结构进行对比验证，为VLM和WebVoyager提供更准确的上下文。
"""

import os
import re
import subprocess
import tempfile
import json
import time
from typing import Dict, Optional, List
from pathlib import Path


def get_chrome_cli_path() -> Optional[str]:
    """查找可用的Chrome/Chromium CLI工具"""
    candidates = [
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def extract_dom_via_cli(url: str, output_path: Optional[str] = None) -> Optional[str]:
    """
    使用chromium-headless CLI提取页面DOM结构

    返回JSON格式的DOM树，或None（如果失败）
    """
    chrome_path = get_chrome_cli_path()
    if not chrome_path:
        print("[WARN] 未找到chromium-browser CLI")
        return None

    if output_path is None:
        output_path = tempfile.mktemp(suffix=".json")

    # 使用chromium的headless模式和dump-dom，等待 JS 渲染
    cmd = [
        chrome_path,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--virtual-time-budget=5000",  # 等待 5 秒让 JS 渲染完
        "--dump-dom",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            # 保存DOM
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(result.stdout)
            return result.stdout
        else:
            print(f"[WARN] chromium返回错误: {result.stderr}")
    except subprocess.TimeoutExpired:
        print("[WARN] chromium超时")
    except Exception as e:
        print(f"[WARN] chromium执行异常: {e}")

    return None


def extract_accessibility_tree_via_cli(url: str) -> Optional[str]:
    """
    使用chromium CLI提取无障碍树（更结构化的DOM表示）
    """
    chrome_path = get_chrome_cli_path()
    if not chrome_path:
        return None

    cmd = [
        chrome_path,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--virtual-time-budget=5000",
        "--enable-accessibility-tree",
        "--dump-aria-tree",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception as e:
        print(f"[WARN] ARIA tree提取失败: {e}")

    return None


def get_element_selector_via_cli(url: str, text: str) -> Optional[str]:
    """
    通过文本内容查找对应元素的CSS选择器
    """
    chrome_path = get_chrome_cli_path()
    if not chrome_path:
        return None

    # 创建临时脚本
    script = f"""
    const {text} = arguments[0];
    const elements = document.querySelectorAll('*');
    for (const el of elements) {{
        if (el.textContent.includes({text})) {{
            console.log(getSelector(el));
            break;
        }}
    }}
    function getSelector(el) {{
        if (el.id) return '#' + el.id;
        if (el.className) return el.tagName.toLowerCase() + '.' + el.className.split(' ')[0];
        return el.tagName.toLowerCase();
    }}
    """

    cmd = [
        chrome_path,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        f"--script-evaluation={script}",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        print(f"[WARN] 选择器查找失败: {e}")

    return None


def compare_code_and_dom(
    code_structure: Dict,
    dom_content: str
) -> Dict[str, any]:
    """
    对比代码结构和实际DOM结构，检测差异

    返回:
    {
        "matched": ["组件列表"],
        "missing": ["缺失的组件"],
        "extra": ["多余的组件"],
        "match_rate": 0.0-1.0
    }
    """
    # 简化实现：检查关键元素是否存在于DOM中
    matched = []
    missing = []
    extra = []

    # 从代码结构中提取关键选择器
    code_selectors = set()
    for comp in code_structure.get("components", []):
        if comp.get("selector"):
            code_selectors.add(comp["selector"])

    # 从DOM中提取标签和class
    dom_pattern = r'<([a-z]+)\s+[^>]*class=["\']([^"\']+)["\'][^>]*>'
    dom_classes = set(re.findall(dom_pattern, dom_content))

    # 简化匹配
    for selector in code_selectors:
        if "." in selector:
            tag, cls = selector.split(".", 1)
            if any(cls in dc for _, dc in dom_classes):
                matched.append(selector)
            else:
                missing.append(selector)
        elif selector.startswith("#"):
            if selector[1:] in dom_content:
                matched.append(selector)
            else:
                missing.append(selector)

    match_rate = len(matched) / max(len(code_selectors), 1)

    return {
        "matched": matched,
        "missing": missing,
        "extra": extra,
        "match_rate": match_rate
    }


class CLIDOMExtractor:
    """
    CLI DOM提取器的封装类，提供便捷接口
    """

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.chrome_path = get_chrome_cli_path()
        self._dom_cache: Dict[str, str] = {}

    def extract_for_url(self, url: str, force: bool = False) -> Optional[str]:
        """
        提取指定URL的DOM，支持缓存
        """
        cache_key = url
        if not force and cache_key in self._dom_cache:
            return self._dom_cache[cache_key]

        dom = extract_dom_via_cli(url)
        if dom:
            self._dom_cache[cache_key] = dom
        return dom

    def get_page_insights(self, url: str) -> Dict:
        """
        获取页面洞察信息：元素计数 + 结构化元素列表（tag/class/id/text）
        """
        insights = {
            "dom_available": False,
            "element_count": 0,
            "interactive_count": 0,
            "links": [],
            "elements": [],  # 结构化元素列表，用于查询 CodeGraph
        }

        dom = self.extract_for_url(url)
        if not dom:
            return insights

        insights["dom_available"] = True
        insights["element_count"] = len(re.findall(r'<[a-z]+', dom))

        # 统计可交互元素
        interactive_patterns = [
            r'<button', r'<input', r'<select', r'<textarea',
            r'<a\s+href', r'<area'
        ]
        for pattern in interactive_patterns:
            insights["interactive_count"] += len(re.findall(pattern, dom, re.I))

        # 提取链接
        links = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', dom, re.I)
        insights["links"] = [
            {"href": href, "text": text.strip()[:50]}
            for href, text in links[:15]
        ]

        # 提取结构化元素列表（tag + class + id + text）
        element_pattern = re.compile(
            r'<([a-z][a-z0-9]*)\s*([^>]*)>([^<]{0,100})',
            re.I | re.DOTALL
        )
        for m in element_pattern.finditer(dom):
            tag = m.group(1).lower()
            attrs = m.group(2)
            text = m.group(3).strip()

            class_m = re.search(r'class=["\']([^"\']+)["\']', attrs)
            id_m = re.search(r'\bid=["\']([^"\']+)["\']', attrs)

            classes = class_m.group(1).split() if class_m else []
            elem_id = id_m.group(1) if id_m else ""

            # 保留：有 class/id 的、交互元素、语义元素、有文本的
            has_content = bool(classes or elem_id or text)
            is_semantic = tag in ("button", "input", "form", "select", "textarea", "nav", "header", "main", "footer", "h1", "h2", "h3", "p", "a", "div", "section")
            if has_content or is_semantic:
                insights["elements"].append({
                    "tag": tag,
                    "classes": classes,
                    "id": elem_id,
                    "text": text[:50],
                })

        return insights

    def clear_cache(self):
        """清空缓存"""
        self._dom_cache.clear()


if __name__ == "__main__":
    # 测试
    extractor = CLIDOMExtractor("/tmp/test")
    url = "http://example.com"
    # insights = extractor.get_page_insights(url)
    # print(json.dumps(insights, indent=2))
    print("CLI DOM Extractor available:", extractor.chrome_path is not None)
