"""저장소 프로필 — "이 저장소는 무엇을 하는 곳인가"를 그래프에서 뽑는다.

검색은 노드 하나하나는 잘 보는데 "어느 저장소 일인가"는 잘 못 고른다. 실측(실제
머지된 MR 265건): 검색 1위의 저장소가 맞는 비율 52.1%. 그런데 그 실패는 어휘로는
못 고친다 — 저장소 단위 포괄도 가중·CORI 선택·결과 다양화 모두 나빠졌다. 저장소
어휘를 통째로 합치면 관계없는 코드가 신호를 덮기 때문이다.

남은 길은 의미다. LLM에게 물으면 47.9%를 맞히는데, 중요한 건 정확도가 아니라
**서로 다르게 틀린다**는 것이다 — LLM만 맞힘 19.6% · 검색만 맞힘 23.8% ·
둘 다 맞힘 28.3%. 그래서 고르지 않고 가중만 준다.

LLM이 판단하려면 저장소 이름만으론 부족하다(이름은 무엇이 들어 있는지 말해주지
않는다). 그래프가 이미 아는 것 — 어떤 경로에 코드가 모여 있고 어떤 심볼이
대표적인가 — 을 그대로 넘긴다. 목록을 손으로 적지 않으므로 저장소가 늘어도
손댈 것이 없다.
"""
from __future__ import annotations

from collections import Counter

from .graph import Graph

_DIRS = 6
_NAMES = 6
_CODE_KINDS = ("function", "class", "endpoint", "route")


def repo_profiles(graph: Graph) -> dict[str, dict]:
    """저장소 → {nodes, dirs, names}. 경로는 두 단계까지만(그 아래는 잡음)."""
    collected: dict[str, dict] = {}
    for node in graph.nodes.values():
        repo, path = node.get("repo"), (node.get("path") or "")
        if not repo or not path or node.get("kind") == "repo":
            continue
        entry = collected.setdefault(repo, {"dirs": Counter(), "names": Counter(),
                                            "nodes": 0})
        entry["nodes"] += 1
        parts = path.replace("\\", "/").split("/")
        if len(parts) > 1:
            entry["dirs"]["/".join(parts[:2]) if len(parts) > 2 else parts[0]] += 1
        if node.get("kind") in _CODE_KINDS:
            name = node.get("name") or ""
            if len(name) > 3:
                entry["names"][name] += 1
    return {repo: {"nodes": e["nodes"],
                   "dirs": [d for d, _ in e["dirs"].most_common(_DIRS)],
                   "names": [n for n, _ in e["names"].most_common(_NAMES)]}
            for repo, e in sorted(collected.items())}


def profile_block(graph: Graph) -> str:
    """프롬프트에 넣을 한 덩어리. 저장소가 하나뿐이면 고를 것이 없으므로 비운다."""
    profiles = repo_profiles(graph)
    if len(profiles) < 2:
        return ""
    lines = []
    for repo, info in profiles.items():
        line = f"- {repo}: paths {', '.join(info['dirs'])}"
        if info["names"]:
            line += f" / symbols {', '.join(info['names'])}"
        lines.append(line)
    return "\n".join(lines)
