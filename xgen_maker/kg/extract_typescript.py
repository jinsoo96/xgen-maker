"""TypeScript/JavaScript 추출기 — 정규식 기반(heuristic 수준임을 명시).

추출: export 심볼, 상대 import(파일 해석), API 호출(fetch/axios/api client → api_call 노드).
템플릿 리터럴 `${...}` 세그먼트는 와일드카드 `*`로 정규화해 crossrepo 매칭에 사용.
"""
from __future__ import annotations

import re
from pathlib import Path

from .graph import Graph

_EXPORT_RE = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?(function|class|const|interface|type)\s+([A-Za-z_$][\w$]*)",
    re.M)
_IMPORT_RE = re.compile(r"import\s+(?:[\w{}\s,*$]+\s+from\s+)?['\"]([^'\"]+)['\"]")
_API_CALL_RE = re.compile(
    r"(?:\b(?:api|apiClient|axios|http|client)\.(get|post|put|delete|patch)|\bfetch)\s*\(\s*[`'\"]([^`'\"]+)[`'\"]",
    re.I)
_TS_EXTS = (".ts", ".tsx", ".js", ".jsx")


def _resolve_relative(spec: str, rel: str, known_files: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    base = (Path(rel).parent / spec).as_posix()
    parts: list[str] = []
    for part in base.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part not in (".", ""):
            parts.append(part)
    base = "/".join(parts)
    candidates = [base + ext for ext in _TS_EXTS] + [f"{base}/index{ext}" for ext in _TS_EXTS]
    if Path(base).suffix:
        candidates.insert(0, base)
    for candidate in candidates:
        if candidate in known_files:
            return candidate
    return None


def normalize_url(url: str) -> str:
    """`${id}` / :id / 쿼리스트링 → 와일드카드 정규화된 경로."""
    url = url.split("?")[0]
    url = re.sub(r"\$\{[^}]*\}", "*", url)
    segments = [seg for seg in url.split("/") if seg]
    out = []
    for seg in segments:
        if seg.startswith(":") or "*" in seg or "{" in seg or "[" in seg:
            out.append("*")
        else:
            out.append(seg)
    return "/" + "/".join(out)


_DOC_RE = re.compile(r"^\s*/\*\*(.*?)\*/", re.S)


def _leading_doc(source: str) -> str:
    match = _DOC_RE.match(source)
    if not match:
        return ""
    text = re.sub(r"^\s*\*\s?", "", match.group(1), flags=re.M).strip()
    return text[:200]


def extract_ts_file(graph: Graph, repo: str, repo_root: Path, rel: str,
                    known_files: set[str], resolver=None) -> None:
    source = (repo_root / rel).read_text(encoding="utf-8", errors="ignore")
    file_id = f"{repo}:{rel}"
    meta = {"lang": "typescript"}
    doc = _leading_doc(source)
    if doc:
        meta["doc"] = doc
    graph.add_node(file_id, "file", Path(rel).name, repo, rel, **meta)

    for match in _EXPORT_RE.finditer(source):
        kind_raw, name = match.group(1), match.group(2)
        kind = "class" if kind_raw == "class" else "function"
        line = source.count("\n", 0, match.start()) + 1
        symbol_id = f"{repo}:{rel}#{name}"
        graph.add_node(symbol_id, kind, name, repo, rel, line, export=kind_raw)
        graph.add_edge(file_id, symbol_id, "contains")

    for match in _IMPORT_RE.finditer(source):
        spec = match.group(1)
        if resolver is not None:
            hit = resolver.resolve(spec, rel)
            if hit is None:
                continue
            kind, target = hit
            if kind == "file":
                graph.add_edge(file_id, f"{repo}:{target}", "imports")
            else:  # feature = 워크스페이스 패키지
                feature_id = f"{repo}:feature:{target}"
                graph.add_node(feature_id, "feature", target, repo, "", package=target)
                graph.add_edge(file_id, feature_id, "imports")
        else:
            resolved = _resolve_relative(spec, rel, known_files)
            if resolved:
                graph.add_edge(file_id, f"{repo}:{resolved}", "imports")

    for match in _API_CALL_RE.finditer(source):
        method = (match.group(1) or "GET").upper()
        url = match.group(2)
        if not url.startswith(("/", "http", "`", "$")) and "/" not in url:
            continue
        norm = normalize_url(url)
        line = source.count("\n", 0, match.start()) + 1
        call_id = f"{repo}:{rel}#CALL {method} {norm}"
        graph.add_node(call_id, "api_call", f"{method} {norm}", repo, rel, line,
                       method=method, url=url, norm_path=norm)
        graph.add_edge(file_id, call_id, "contains")
