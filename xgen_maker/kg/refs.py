"""파일이 '참조하는' 식별자 — 정의하지 않았지만 그 파일이 다루는 이름들.

그래프는 지금까지 파일이 **정의한** 것만 색인했다(함수·클래스 이름, 도크스트링).
그런데 사람은 그 파일이 **다루는** 이름으로 찾는다 — "VectorDBContextV2 노출 바꿔줘"는
그 이름을 정의한 곳이 아니라, 그 이름이 **적혀 있는** 카탈로그 파일을 가리킨다.
실측: 그 카탈로그 파일은 문자 그대로 그 이름을 담고 있는데 검색에 전혀 안 잡혔다.

정의부는 이미 심볼 노드로 잡히므로, 여기서는 그 파일에 등장하되 그 파일이 정의하지
않은 이름만 모은다. 흔한 말은 변별력이 없으므로 식별자다운 것만 남긴다.
"""
from __future__ import annotations

import re

# 식별자다운 것: 밑줄이 있거나 대소문자가 섞인, 충분히 긴 이름
_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{4,}\b")
_MIN_LEN = 6
# 한 파일에서 이만큼까지만. 상한이 낮으면 정작 찾는 이름이 잘려 나가고, 높으면 참조가
# 수백 개인 파일이 색인을 지배한다. 실제 머지된 MR 256건으로 훑어 정했다 —
# 200: R@10 0.738 · 400: R@10 0.746 · 800: R@10 0.750(동률에 색인만 커짐).
_MAX_REFS = 400


def _is_identifier_like(name: str) -> bool:
    if len(name) < _MIN_LEN:
        return False
    if "_" in name:
        return True
    lowered = name.lower()
    return name != lowered and name != name.upper()   # camelCase / PascalCase


# 화면에 보이는 말 — 코드 안의 한글. 사용자는 UI에 적힌 낱말로 요청한다
# ("다운로드 센터 목록 정렬해줘"). 그 낱말은 식별자가 아니라 문자열·주석에 있어서
# 위 규칙(ASCII 식별자)으로는 하나도 안 잡혔다. 실측: 프론트 파일의 67%가 한글을
# 담고 있고, 안 잡히던 질의의 정답 파일에는 그 질의의 낱말이 문자 그대로 있었다.
_LABEL = re.compile(r"[가-힣]{2,}")
# 한 파일에서 이만큼까지만. 실제 머지된 MR 265건으로 훑어 정했다 —
# 0(끔) R@1 0.491 R@10 0.857 · 40 0.506/0.849 · 80 0.506/0.864 · 120 0.498/0.860 ·
# 200 0.513/0.868 · 400 0.525/0.868 ← 채택 · 800 0.521/0.868(다시 내려감).
_MAX_LABELS = 400


def collect_labels(source: str) -> list[str]:
    """이 파일이 사람에게 보여 주는 말. 자주 나올수록 그 파일의 주제에 가깝다.

    흔한 말(관리·목록 같은)을 걸러내지 않는다 — 어느 파일에나 있는 말은 BM25가
    IDF로 알아서 눌러 준다. 여기서 손으로 불용어를 정하면 도메인 낱말까지 날아간다.
    """
    seen: dict[str, int] = {}
    for match in _LABEL.finditer(source):
        word = match.group(0)
        seen[word] = seen.get(word, 0) + 1
    return sorted(seen, key=lambda w: (-seen[w], -len(w), w))[:_MAX_LABELS]


def collect_refs(source: str, defined: set[str]) -> list[str]:
    """이 파일이 다루지만 정의하지는 않은 식별자.

    defined에는 그 파일이 정의한 이름(함수·클래스)을 넣는다 — 정의부는 이미 심볼
    노드로 색인되므로 여기서 또 세면 그 파일만 두 번 유리해진다.
    """
    seen: dict[str, int] = {}
    for match in _TOKEN.finditer(source):
        name = match.group(0)
        if name in defined or not _is_identifier_like(name):
            continue
        seen[name] = seen.get(name, 0) + 1
    # 자주 등장할수록 그 파일의 주제에 가깝다. 동점이면 긴 이름이 더 구체적이다.
    ranked = sorted(seen, key=lambda n: (-seen[n], -len(n), n))
    return ranked[:_MAX_REFS]
