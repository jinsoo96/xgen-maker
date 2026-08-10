"""호출 그래프 링크 — 파일이 부르는 함수를 저장소 함수 노드에 잇는다.

왜 후처리인가: 호출은 파일 간이다. A.ts가 부르는 함수가 B.ts에 정의돼 있으므로,
한 파일을 추출하는 시점엔 대상이 아직 그래프에 없다. 그래서 추출 때는 "부른 이름"만
모아 두고(_pending_calls), 저장소의 모든 함수가 들어온 뒤 이름으로 해소한다.

정밀도 원칙(타입 정보 없는 정규식 추출의 한계 인정):
- 같은 파일에 그 이름의 함수가 있으면 그것으로 잇는다(모호하지 않다).
- 없으면 저장소 안에서 그 이름이 **유일**할 때만 잇는다. 같은 이름이 여럿이면
  타입 없이 못 고르므로 건너뛴다 — 없는 호출을 지어내 영향분석을 더럽히느니 낫다.

이 링크가 없으면(TS·Rust는 실제로 calls 엣지가 0이었다) 중심성(PageRank)도, 체인
확장도, 영향분석("이걸 고치면 누가 깨지나")도 그 언어에서 통째로 무력해진다.
기법 출처(웹 조사): Codebase-Memory 등 코드 KG 빌드의 call-graph 추출.
"""
from __future__ import annotations

import re

from .graph import Graph

# 호출부: 식별자 바로 뒤 여는 괄호. 언어 키워드(제어문·선언)는 호출이 아니므로 제외.
_CALL_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
_NOT_CALLS = frozenset("""
if elif else for while switch case catch return yield def class function fn func
new await async match with as in is and or not import from export public private
static void const let var type interface struct enum impl trait pub mut where use
""".split())


# 주석·문자열은 코드가 아니다. `이름(` 패턴은 산문에도 있어서, 그대로 훑으면
# 없는 호출을 지어낸다(실측: "annotations: MCP annotations (read_only_hint 등)."
# 이라는 도크스트링 한 줄이 Tool.annotations를 부른다는 엣지를 만들었다).
# 그렇게 생긴 엣지는 영향분석·중심성·체인 확장을 조용히 오염시킨다.
# 파싱하지 않는다 — 언어별 파서를 붙이는 대신, 코드가 아닌 구간만 걷어낸다.
_STRIP_RULES = (
    re.compile(r'"""[\s\S]*?"""'),          # 파이썬 도크스트링
    re.compile(r"'''[\s\S]*?'''"),
    re.compile(r"/\*[\s\S]*?\*/"),          # C 계열 블록 주석
    re.compile(r'"(?:\\.|[^"\\\n])*"'),     # 문자열 리터럴(줄을 넘지 않는 것만)
    re.compile(r"'(?:\\.|[^'\\\n])*'"),
    re.compile(r"`(?:\\.|[^`\\])*`"),       # 템플릿 리터럴
    re.compile(r"#[^\n]*"),                 # 파이썬·셸 주석
    re.compile(r"//[^\n]*"),                # C 계열 줄 주석
)
# 보간식(`${...}` · f-string의 `{...}`)은 문자열 안에 있지만 코드다.
# 통째로 지우면 거기 있는 진짜 호출이 사라진다 — `총 ${total.toFixed(2)}원`의
# toFixed, f"결과 {compute(x)}"의 compute. 걷어내기 전에 먼저 건져 둔다.
_INTERPOLATION = re.compile(r"\$?\{([^{}]*)\}")


def strip_noncode(source: str) -> str:
    """주석·문자열을 지운다. 지운 자리는 빈 칸으로 둬 다른 토큰이 붙지 않게.

    문자열 안의 보간식은 살린다 — 그건 문자열이 아니라 그 자리에서 실행되는 코드다.
    """
    kept = " ".join(m.group(1) for m in _INTERPOLATION.finditer(source))
    for rule in _STRIP_RULES:
        source = rule.sub(" ", source)
    return f"{source} {kept}" if kept else source


def scan_call_names(source: str, strip: bool = True) -> set[str]:
    """소스에서 호출된 식별자 이름들(제어문 키워드 제외).

    strip=False는 감사용 — 걷어내기 전후를 비교할 때만 쓴다.
    """
    if strip:
        source = strip_noncode(source)
    return {m.group(1) for m in _CALL_RE.finditer(source)
            if m.group(1) not in _NOT_CALLS}


def record_calls(graph: Graph, file_id: str, source: str) -> None:
    """추출 중 호출 이름을 모아 둔다(후처리 link_calls에서 해소). 빌드 임시 구조 —
    graph.__dict__에 두므로 Graph.save가 직렬화하지 않는다(_bm25 색인과 같은 방식)."""
    names = scan_call_names(source)
    if names:
        graph.__dict__.setdefault("_pending_calls", {})[file_id] = names


def link_calls(graph: Graph, repo: str) -> int:
    """모아 둔 호출 이름을 저장소 함수에 해소해 calls 엣지를 잇는다. 반환 추가 엣지 수.

    같은 파일 우선, 그다음 저장소 내 유일 해소만. 메서드는 짧은 이름(뒤 세그먼트)으로도 맞춘다.
    """
    pending = graph.__dict__.get("_pending_calls")
    if not pending:
        return 0
    by_name: dict[str, list[tuple[str, str]]] = {}   # 짧은이름 → [(파일rel, node_id)]
    for node_id, node in graph.nodes.items():
        if node.get("repo") == repo and node["kind"] in ("function", "class"):
            short = node["name"].split(".")[-1]
            by_name.setdefault(short, []).append((node.get("path", ""), node_id))

    before = len(graph.edges)
    for file_id, names in pending.items():
        rel = file_id.split(":", 1)[1] if ":" in file_id else ""
        for name in names:
            candidates = by_name.get(name)
            if not candidates:
                continue
            same_file = [nid for path, nid in candidates if path == rel and nid != file_id]
            if same_file:
                for nid in same_file:
                    graph.add_edge(file_id, nid, "calls", role="same_file")
            elif len(candidates) == 1:              # 저장소 내 이름이 유일할 때만(오탐 방지)
                target = candidates[0][1]
                if target != file_id:
                    graph.add_edge(file_id, target, "calls", role="cross_file")
    # 이 저장소 몫은 소진 — 병합 그래프에서 저장소별로 반복 호출돼도 중복 처리 안 되게
    for file_id in [fid for fid in pending
                    if fid.split(":", 1)[0] == repo or fid.startswith(repo + ":")]:
        pending.pop(file_id, None)
    if not pending:
        graph.__dict__.pop("_pending_calls", None)
    return len(graph.edges) - before
