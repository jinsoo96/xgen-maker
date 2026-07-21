"""Python 추출기 — ast 기반. 파일/클래스/함수/임포트/FastAPI 엔드포인트/동일파일 호출.

FastAPI 규약: `X = APIRouter(prefix="...")` 변수의 `@X.get("/path")` 데코레이터를
엔드포인트 노드(meta: method, path)로 승격. prefix는 결합.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .graph import Graph

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
_ROUTER_RE = re.compile(r"(\w+)\s*=\s*APIRouter\s*\(([^)]*)\)", re.S)
_PREFIX_RE = re.compile(r"prefix\s*=\s*['\"]([^'\"]+)['\"]")


def _router_prefixes(source: str) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for match in _ROUTER_RE.finditer(source):
        var, args = match.group(1), match.group(2)
        prefix_match = _PREFIX_RE.search(args)
        prefixes[var] = prefix_match.group(1) if prefix_match else ""
    return prefixes


def _endpoint_of(deco: ast.expr, prefixes: dict[str, str]) -> tuple[str, str] | None:
    """데코레이터가 HTTP 라우트면 (METHOD, full_path) 반환."""
    if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
        return None
    if deco.func.attr not in HTTP_METHODS:
        return None
    owner = deco.func.value
    owner_name = owner.id if isinstance(owner, ast.Name) else None
    if owner_name is None or (owner_name not in prefixes and owner_name not in ("app", "router")):
        return None
    path = ""
    if deco.args and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str):
        path = deco.args[0].value
    prefix = prefixes.get(owner_name, "")
    return deco.func.attr.upper(), (prefix + path) or "/"


def _resolve_import(module: str, repo_root: Path, known_files: set[str]) -> str | None:
    """intra-repo 모듈 문자열 → 상대 파일경로 (best-effort)."""
    base = module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if candidate in known_files:
            return candidate
    return None


def extract_python_file(graph: Graph, repo: str, repo_root: Path, rel: str,
                        known_files: set[str]) -> None:
    # utf-8-sig — BOM이 붙은 파일(Windows 저장본)을 utf-8로 읽으면 선두에 U+FEFF가
    # 남아 ast.parse가 SyntaxError를 낸다. 그러면 아래에서 파일이 통째로 누락된다.
    source = (repo_root / rel).read_text(encoding="utf-8-sig", errors="ignore")
    file_id = f"{repo}:{rel}"
    file_meta = {"lang": "python"}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # 파싱 못 해도 '파일이 존재한다'는 사실은 그래프에 남긴다. 통째로 빼면
        # 이 파일을 import하는 쪽의 엣지가 갈 곳을 잃고(끊긴 엣지), 검색에서도 사라진다.
        file_meta["parse_error"] = True
        graph.add_node(file_id, "file", Path(rel).name, repo, rel, **file_meta)
        return
    module_doc = ast.get_docstring(tree)
    if module_doc:
        file_meta["doc"] = module_doc.strip()[:200]
    graph.add_node(file_id, "file", Path(rel).name, repo, rel, **file_meta)
    prefixes = _router_prefixes(source)
    local_funcs: dict[str, str] = {}

    def add_symbol(node: ast.AST, qualname: str, kind: str, parent_id: str) -> str:
        symbol_id = f"{repo}:{rel}#{qualname}"
        meta = {}
        doc = ast.get_docstring(node) if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else None
        if doc:
            meta["doc"] = doc.strip()[:200]
        graph.add_node(symbol_id, kind, qualname, repo, rel,
                       getattr(node, "lineno", 0), **meta)
        graph.add_edge(parent_id, symbol_id, "contains")
        return symbol_id

    def walk_body(body: list, parent_id: str, prefix: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                class_id = add_symbol(node, prefix + node.name, "class", parent_id)
                walk_body(node.body, class_id, prefix + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = prefix + node.name
                symbol_id = add_symbol(node, qualname, "function", parent_id)
                local_funcs[node.name] = symbol_id
                for deco in node.decorator_list:
                    endpoint = _endpoint_of(deco, prefixes)
                    if endpoint:
                        method, path = endpoint
                        ep_id = f"{repo}:{rel}#EP {method} {path}"
                        graph.add_node(ep_id, "endpoint", f"{method} {path}", repo, rel,
                                       node.lineno, method=method, route_path=path,
                                       handler=qualname)
                        graph.add_edge(file_id, ep_id, "contains")
                        graph.add_edge(ep_id, symbol_id, "calls", role="handler")

    walk_body(tree.body, file_id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_import(alias.name, repo_root, known_files)
                if resolved:
                    graph.add_edge(file_id, f"{repo}:{resolved}", "imports")
        elif isinstance(node, ast.ImportFrom) and node.module:
            # `from pkg import mod` 형태의 서브모듈까지 해석
            candidates = [f"{node.module}.{alias.name}" for alias in node.names]
            candidates.append(node.module)
            for module in candidates:
                resolved = _resolve_import(module, repo_root, known_files)
                if resolved:
                    graph.add_edge(file_id, f"{repo}:{resolved}", "imports")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            callee = local_funcs.get(node.func.id)
            if callee:
                graph.add_edge(file_id, callee, "calls", role="same_file")
