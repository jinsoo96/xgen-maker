"""의미층(semantic layer) 주입 — 2층 구조의 윗층.

1단 결정론: docstring(meta.doc) 우선, 없으면 구조 기반 템플릿 요약. 비용 0, 전 노드.
2단 LLM: 우선순위 노드(라우트→엔드포인트→feature→핵심 파일)에 평문 한국어 요약을
         배치 생성해 meta.summary를 대체. 재실행 시 이미 LLM 요약된 노드는 스킵(재개 가능).
"""
from __future__ import annotations

from pathlib import Path

from .. import llm
from .graph import Graph

_LLM_KIND_PRIORITY = ("route", "endpoint", "feature", "file")

_SUMMARY_SYSTEM = (
    "You are a senior engineer documenting a codebase. "
    "Summarize the given code unit in 1-2 Korean sentences: what it does and its role. "
    'Reply JSON only: {"summary": "..."}')


def _contained(graph: Graph, node_id: str) -> list[dict]:
    out = []
    for direction, edge in graph.neighbors(node_id):
        if direction == "out" and edge["kind"] == "contains":
            child = graph.nodes.get(edge["dst"])
            if child:
                out.append(child)
    return out


def deterministic_summary(graph: Graph, node: dict) -> str:
    doc = node["meta"].get("doc", "")
    if doc:
        return doc
    kind = node["kind"]
    if kind == "file":
        children = _contained(graph, node["id"])
        classes = [c["name"] for c in children if c["kind"] == "class"][:4]
        functions = [c["name"] for c in children if c["kind"] == "function"][:5]
        endpoints = [c["name"] for c in children if c["kind"] == "endpoint"][:4]
        parts = []
        if endpoints:
            parts.append("엔드포인트 " + ", ".join(endpoints))
        if classes:
            parts.append("클래스 " + ", ".join(classes))
        if functions:
            parts.append("함수 " + ", ".join(functions))
        lang = node["meta"].get("lang", "")
        return f"{lang} 파일 — " + (" · ".join(parts) if parts else "심볼 없음")
    if kind == "endpoint":
        return (f"{node['meta'].get('method', '')} {node['meta'].get('route_path', '')} — "
                f"핸들러 {node['meta'].get('handler', '?')} ({node['path']})")
    if kind == "route":
        return f"화면 라우트 {node['name']} — {node['path']}"
    if kind == "feature":
        members = len(_contained(graph, node["id"]))
        return f"프론트 feature 패키지 {node['name']} — 파일 {members}개"
    if kind == "api_call":
        return f"프론트 API 호출 {node['name']} ({node['path']})"
    if kind in ("class", "function"):
        return f"{kind} {node['name']} — {node['path']}:{node['line']}"
    return node["name"]


def enrich_deterministic(graph: Graph) -> int:
    """summary가 없는 모든 노드에 결정론 요약 주입. 반환 = 채운 노드 수.

    ⚠️ 검색에는 해롭다. 실측(실제 머지된 MR 265건, 전체 노드에 주입):
      요약 없음   R@1 0.419 · R@10 0.800 · MRR 0.542
      결정론 요약  R@1 0.415 · R@10 0.774 · MRR 0.523   (분할 A·B·D 모두 하락)
    요약이 검색 점수에 들어가는데(rank._META_KEYS), 여기서 만드는 문장은 대부분
    상투구다("config 파일 — 심볼 없음", "function f — a.py:12"). 같은 말이 수만
    노드에 붙으면 그 말들의 변별력이 사라지고, 노드 문서만 길어져 길이 정규화로
    실제 신호가 깎인다. 사람이 읽을 목록을 만들 때만 쓰고, 검색용으로는 쓰지 말 것.
    """
    filled = 0
    for node in graph.nodes.values():
        if node["meta"].get("summary"):
            continue
        node["meta"]["summary"] = deterministic_summary(graph, node)
        node["meta"]["summary_src"] = "deterministic"
        filled += 1
    return filled


def _code_head(node: dict, repos: dict[str, str], lines: int = 50) -> str:
    repo_path = repos.get(node["repo"])
    if not repo_path or not node["path"]:
        return ""
    file_path = Path(repo_path) / node["path"]
    if not file_path.is_file():
        return ""
    try:
        # utf-8-sig — BOM이 첫 줄 선두에 섞여 요약 품질을 떨어뜨리지 않게
        return "\n".join(file_path.read_text(encoding="utf-8-sig", errors="ignore")
                         .splitlines()[:lines])
    except OSError:
        return ""


def _llm_context(graph: Graph, node: dict, repos: dict[str, str]) -> str:
    children = _contained(graph, node["id"])
    symbols = ", ".join(c["name"] for c in children[:15])
    head = _code_head(node, repos)
    return (f"[kind] {node['kind']}\n[name] {node['name']}\n"
            f"[path] {node['repo']}:{node['path']}\n"
            f"[doc] {node['meta'].get('doc', '')}\n[contains] {symbols}\n"
            + (f"[code head]\n{head}" if head else ""))


def _degree_index(graph: Graph) -> dict[str, int]:
    degree: dict[str, int] = {}
    for edge in graph.edges:
        degree[edge["src"]] = degree.get(edge["src"], 0) + 1
        degree[edge["dst"]] = degree.get(edge["dst"], 0) + 1
    return degree


def enrich_llm(graph: Graph, base: str, model: str, repos: dict[str, str],
               limit: int = 200, timeout: int = 45,
               kinds: tuple[str, ...] = _LLM_KIND_PRIORITY,
               chat_fn=None, workers: int = 6, on_progress=None) -> dict:
    """LLM 요약 배치 주입. 반환 stats. chat_fn은 테스트 치환용(기본 llm.json_chat).

    한 번에 여러 건을 부른다. 요약은 서로 독립이라 줄 세울 이유가 없는데, 순차로는
    노드당 20초씩 걸려 의미층이 사실상 못 채워졌다(실측: 순차 3건/분 → 6워커 38건/분).
    워커를 더 늘려도 처리량은 안 늘어난다(10워커도 39건/분) — CLI 쪽이 한계다.

    ⚠️ 검색을 위해 돌리지 말 것. "의미를 더하면 검색이 좋아진다"는 직관과 반대다.
    실제 머지된 MR 265건, 중심성 상위 800개 노드에 실제로 채워 재 본 결과:
      요약 전  R@1 0.404 · R@10 0.796 · MRR 0.531
      요약 후  R@1 0.400 · R@10 0.781 · MRR 0.511   (분할 A·B·D 하락)
    부분 커버리지 탓이 아니다 — 요약을 받은 파일만 따로 봐도 나빠졌다
    (0.914 → 0.877). 파일 노드는 이미 '그 파일이 다루는 식별자'(refs)라는 날카로운
    신호를 갖고 있는데, 300자 산문이 그걸 희석하고 문서를 길게 만들어 길이 정규화로
    점수를 깎는다. 사람이 읽는 대시보드·투어·설명에는 값이 있으니 그 용도로만 쓴다.
    """
    import concurrent.futures as futures

    chat = chat_fn or llm.json_chat
    degree = _degree_index(graph)
    targets = [n for n in graph.nodes.values()
               if n["kind"] in kinds and n["meta"].get("summary_src") != "llm"]
    targets.sort(key=lambda n: (kinds.index(n["kind"]), -degree.get(n["id"], 0)))
    batch = targets[:limit]

    def summarize(node: dict):
        return node, chat(base, model, [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": _llm_context(graph, node, repos)}],
            max_tokens=200, timeout=timeout)

    done = failed = 0
    if not batch:
        return {"targets": len(targets), "llm_done": 0, "llm_failed": 0, "remaining": 0}
    # 첫 건은 혼자 돌려 본다. 엔드포인트가 죽었으면 여기서 멈춘다 — 전부 붙여 놓고
    # limit만큼 실패를 쌓을 이유가 없다(순차 시절의 '3연속 실패 중단'과 같은 목적).
    first, answer = summarize(batch[0])
    if not (answer and isinstance(answer.get("summary"), str) and answer["summary"].strip()):
        return {"targets": len(targets), "llm_done": 0, "llm_failed": 1,
                "remaining": len(targets), "aborted": "첫 호출 실패 — 엔드포인트 확인"}
    first["meta"]["summary"] = answer["summary"].strip()[:300]
    first["meta"]["summary_src"] = "llm"
    done = 1

    with futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for node, answer in pool.map(summarize, batch[1:]):
            if answer and isinstance(answer.get("summary"), str) and answer["summary"].strip():
                node["meta"]["summary"] = answer["summary"].strip()[:300]
                node["meta"]["summary_src"] = "llm"
                done += 1
            else:
                failed += 1
            if on_progress is not None:
                on_progress(done, failed, len(batch))
    if done:
        graph.touch()          # 요약은 검색 점수에 들어간다 — 색인을 다시 세워야 한다
    return {"targets": len(targets), "llm_done": done, "llm_failed": failed,
            "remaining": max(0, len(targets) - done - failed)}
