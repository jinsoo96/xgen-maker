"""Rust 추출 — 함수·타입·엔드포인트를 지식그래프 노드로.

파이썬처럼 AST를 쓸 수 없으니(rustc 없이) 정규식 기반이다. 목표는 완벽한 파싱이 아니라
**착지 가능한 좌표**를 만드는 것 — 이름과 줄 번호가 맞으면 에이전트가 그 자리를 연다.

지원:
- `fn` / `pub fn` / `pub async fn` / `pub(crate) fn`   → function
- `struct` / `enum` / `trait` / `type`                 → class (검색·표시 일관성)
- `impl X` 블록 안의 메서드는 `X::method`로 정규화
- `use a::b::c` → 같은 크레이트(crate::/self::/super::) 참조는 imports 엣지
- axum `.route("/path", get(handler))` → endpoint 노드 + 핸들러로 route_of 엣지

문자열·주석 안의 토큰을 잡지 않도록 줄 단위로 선행 처리한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from .graph import Graph

RUST_EXTS = {".rs"}

_FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?"
                 r"(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)")
_TYPE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait|union)\s+"
                   r"([A-Za-z_][A-Za-z0-9_]*)")
_IMPL = re.compile(r"^\s*impl(?:\s*<[^>]*>)?\s+(?:([A-Za-z_][A-Za-z0-9_:]*)\s+for\s+)?"
                   r"([A-Za-z_][A-Za-z0-9_:]*)")
_USE = re.compile(r"^\s*(?:pub\s+)?use\s+([A-Za-z_][A-Za-z0-9_:{}, \*]*)\s*;")
# axum: .route("/path", get(handler)) / axum::routing::post(mod::handler)
_ROUTE = re.compile(r"\.route\(\s*\"([^\"]+)\"\s*,\s*(.+?)\)\s*(?:\.|;|$)")
_METHOD = re.compile(r"(?:axum::routing::)?(get|post|put|patch|delete|head|options|any)\s*\(\s*"
                     r"([A-Za-z_][A-Za-z0-9_:]*)")


def link_rust_routes(graph: Graph, repo: str) -> int:
    """라우트 → 핸들러 연결(파일 간).

    핸들러는 보통 다른 모듈에 있다(`routes::security::login`). 추출은 파일 단위라
    그 시점엔 대상이 아직 없을 수 있어, 전체 추출이 끝난 뒤 이름으로 잇는다.
    """
    by_name: dict[str, list[str]] = {}
    for nid, n in graph.nodes.items():
        if n["repo"] == repo and n["kind"] == "function":
            by_name.setdefault(n["name"].split("::")[-1], []).append(nid)
    linked = 0
    for nid, n in list(graph.nodes.items()):
        if n["repo"] != repo or n["kind"] != "endpoint":
            continue
        handler = (n.get("meta") or {}).get("handler")
        if not handler:
            continue
        parts = handler.split("::")
        cands = by_name.get(parts[-1]) or []
        if not cands:
            continue
        # 모듈 경로가 파일 경로와 겹치는 후보를 우선(동명 함수 오연결 방지)
        hint = parts[-2] if len(parts) > 1 else ""
        best = next((c for c in cands if hint and hint in graph.nodes[c]["path"]), None)
        target = best or (cands[0] if len(cands) == 1 else None)
        if target:
            graph.add_edge(nid, target, "route_of")
            linked += 1
    return linked


def _strip_comment(line: str) -> str:
    """줄 주석만 제거. 문자열은 남긴다 — 라우트 경로가 문자열 안에 있다."""
    idx = line.find("//")
    return line[:idx] if idx >= 0 else line


def _strip_noise(line: str) -> str:
    """선언 탐지용 — 주석에 더해 문자열 내용까지 비운다(문자열 속 fn 등 오탐 방지)."""
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', _strip_comment(line))


def _brace_depth(line: str) -> int:
    return line.count("{") - line.count("}")


def extract_rust_file(graph: Graph, repo: str, repo_root: Path, rel: str,
                      known_files: set[str]) -> None:
    source = (repo_root / rel).read_text(encoding="utf-8-sig", errors="ignore")
    file_id = f"{repo}:{rel}"
    lines = source.splitlines()
    meta = {"lang": "rust"}
    # 파일 선두 //! 모듈 문서를 요약으로
    doc = [ln.lstrip()[3:].strip() for ln in lines[:12] if ln.lstrip().startswith("//!")]
    if doc:
        meta["doc"] = " ".join(doc)[:200]
    graph.add_node(file_id, "file", Path(rel).name, repo, rel, **meta)

    impl_stack: list[tuple[str, int]] = []  # (타입명, 진입 시점 중괄호 깊이)
    depth = 0
    local_fns: dict[str, str] = {}

    for i, raw in enumerate(lines, start=1):
        line = _strip_noise(raw)

        # impl 블록을 벗어났는지 먼저 정리
        while impl_stack and depth <= impl_stack[-1][1]:
            impl_stack.pop()

        m = _IMPL.match(line)
        if m:
            impl_stack.append((m.group(2).split("::")[-1], depth))
            depth += _brace_depth(line)
            continue

        m = _TYPE.match(line)
        if m:
            kind_raw, name = m.group(1), m.group(2)
            nid = f"{repo}:{rel}#{name}"
            graph.add_node(nid, "class", name, repo, rel, i, rust_kind=kind_raw)
            graph.add_edge(file_id, nid, "contains")
            depth += _brace_depth(line)
            continue

        m = _FN.match(line)
        if m:
            name = m.group(1)
            qual = f"{impl_stack[-1][0]}::{name}" if impl_stack else name
            nid = f"{repo}:{rel}#{qual}"
            graph.add_node(nid, "function", qual, repo, rel, i)
            graph.add_edge(file_id, nid, "contains")
            local_fns[name] = nid
            depth += _brace_depth(line)
            continue

        m = _USE.match(line)
        if m:
            path = m.group(1).strip()
            # 같은 크레이트 참조만 파일 간 엣지로 연결한다(외부 크레이트는 노드가 없다)
            if path.startswith(("crate::", "self::", "super::")):
                mod_path = path.split("::")[1:]
                mod_path = [p for p in mod_path if p and p[0].islower()]
                if mod_path:
                    for cand in (f"src/{'/'.join(mod_path)}.rs",
                                 f"src/{'/'.join(mod_path)}/mod.rs"):
                        if cand in known_files:
                            graph.add_edge(file_id, f"{repo}:{cand}", "imports")
                            break

        # 라우트는 경로가 문자열이라 주석만 제거한 원문에서 찾는다
        for rm in _ROUTE.finditer(_strip_comment(raw)):
            path, handler_part = rm.group(1), rm.group(2)
            hm = _METHOD.search(handler_part)
            if not hm:
                continue
            method, handler = hm.group(1).upper(), hm.group(2)
            ep_id = f"{repo}:{rel}#EP {method} {path}"
            # 키 이름은 파이썬 추출기와 같아야 한다 — crossrepo가 meta['route_path']로 매칭한다.
            # ('path'는 add_node의 위치 인자(파일 경로)와 충돌해서 못 쓴다)
            graph.add_node(ep_id, "endpoint", f"{method} {path}", repo, rel, i,
                           method=method, route_path=path, handler=handler)
            graph.add_edge(file_id, ep_id, "contains")
            target = local_fns.get(handler.split("::")[-1])
            if target:
                graph.add_edge(ep_id, target, "route_of")

        depth += _brace_depth(line)
