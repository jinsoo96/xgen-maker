"""수정가능(R8) — 사람의 그래프 편집을 오버레이로 영속화.

핵심 설계: 편집을 그래프 json에 직접 쓰면 재빌드/증분 sync에서 유실된다.
→ 편집은 `kg/overlay.json`에 따로 쌓고, build/merge/sync/enrich 후마다 재적용한다.

편집 어휘:
- summary : 요약을 사람 문장으로 교체 (summary_src="human", LLM/결정론이 덮지 않음)
- note    : 작업 메모 (예: "이 모듈은 레거시, 신규 작업은 X로")
- deprecated : 착지 회피 — 검색 점수 큰 페널티 → 루프가 이 노드로 착지하지 않음
- redirect: deprecated일 때 대신 갈 노드
- custom_edges : 사람이 아는 암묵 연결(kind="relates_to" 등) 추가
"""
from __future__ import annotations

import json
from pathlib import Path

from .graph import Graph

DEFAULT_OVERLAY = "kg/overlay.json"
_EDIT_KEYS = ("summary", "note", "deprecated", "redirect", "tags")


def load_overlay(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"node_overrides": {}, "custom_edges": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("node_overrides", {})
    data.setdefault("custom_edges", [])
    return data


def save_overlay(overlay: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overlay, ensure_ascii=False, indent=1), encoding="utf-8")


def apply_overlay(graph: Graph, overlay: dict) -> dict:
    """그래프에 오버레이 적용. 반환 = {applied, missing[]}."""
    applied = 0
    missing: list[str] = []
    for node_id, edits in overlay.get("node_overrides", {}).items():
        node = graph.nodes.get(node_id)
        if node is None:
            missing.append(node_id)
            continue
        for key in _EDIT_KEYS:
            if key in edits:
                node["meta"][key] = edits[key]
        if "summary" in edits:
            node["meta"]["summary_src"] = "human"
        applied += 1
    for edge in overlay.get("custom_edges", []):
        if edge.get("src") in graph.nodes and edge.get("dst") in graph.nodes:
            graph.add_edge(edge["src"], edge["dst"], edge.get("kind", "relates_to"),
                           human=True, note=edge.get("note", ""))
            applied += 1
        else:
            missing.append(f"edge {edge.get('src')} -> {edge.get('dst')}")
    return {"applied": applied, "missing": missing}


def annotate(overlay_path: str | Path, node_id: str, *, summary: str | None = None,
             note: str | None = None, deprecated: bool | None = None,
             redirect: str | None = None, tags: list[str] | None = None) -> dict:
    """오버레이 파일에 노드 편집을 기록(누적)."""
    overlay = load_overlay(overlay_path)
    edits = overlay["node_overrides"].setdefault(node_id, {})
    if summary is not None:
        edits["summary"] = summary
    if note is not None:
        edits["note"] = note
    if deprecated is not None:
        edits["deprecated"] = deprecated
    if redirect is not None:
        edits["redirect"] = redirect
    if tags is not None:
        edits["tags"] = tags
    save_overlay(overlay, overlay_path)
    return edits


def add_custom_edge(overlay_path: str | Path, src: str, dst: str,
                    kind: str = "relates_to", note: str = "") -> None:
    overlay = load_overlay(overlay_path)
    overlay["custom_edges"].append({"src": src, "dst": dst, "kind": kind, "note": note})
    save_overlay(overlay, overlay_path)
