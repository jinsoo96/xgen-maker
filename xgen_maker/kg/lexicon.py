"""이 코드베이스가 쓰는 한글↔코드 어휘 대응 — 그래프에서 배운다.

사람은 "전사문 정제"라고 묻고 코드는 `stt`·`transcribe`라고 쓴다. 이 간극을 LLM의
일반 영어로 메우면 어긋난다("음성 인식"→speech recognition을 냈는데 코드는 audio다).

그런데 그래프 자체가 병렬 코퍼스다 — 한 노드 안에 한글 요약(docstring)과 영문
식별자(이름·경로)가 함께 있다. 그 공기(co-occurrence)를 세면 이 코드베이스가 그
개념을 실제로 뭐라 부르는지 나온다. 사전을 손으로 적지 않는다 — 코드가 바뀌면
사전도 따라 바뀐다.

실측(그래프에서 뽑은 것):
  전사 → transcript · transcribe · stt · audio
  삭제 → delete · purge · remove · invalidate
  스키마 → schema · postgresql · informix
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from .rank import tokenize

# 이보다 적게 함께 나온 짝은 우연이다.
_MIN_PAIRS = 3
# 한글어 하나에 코드어를 여럿 붙이면 질의가 묽어진다 — 실측으로 1개가 가장 좋았다
# (1개 R@1 0.423, 2개 0.397, 5개 0.321).
_PER_WORD = 1


def _is_korean(token: str) -> bool:
    return any("가" <= ch <= "힣" for ch in token)


def build_lexicon(nodes: list[dict]) -> dict[str, list[str]]:
    """노드의 한글 설명과 영문 식별자가 함께 나온 빈도로 대응을 만든다.

    반환 {한글어: [코드어…]} — 연관이 강한 순.
    """
    ko_total: Counter = Counter()
    en_total: Counter = Counter()
    pairs: Counter = Counter()
    seen = 0
    for node in nodes:
        meta = node.get("meta") or {}
        korean = {t for t in tokenize(" ".join(str(meta.get(k, "")) for k in ("summary", "doc")))
                  if _is_korean(t)}
        english = {t for t in tokenize(f"{node.get('name', '')} {node.get('path', '')}")
                   if t.isascii() and len(t) > 2}
        if not korean or not english:
            continue
        seen += 1
        for k in korean:
            ko_total[k] += 1
        for e in english:
            en_total[e] += 1
        for k in korean:
            for e in english:
                pairs[(k, e)] += 1
    if not seen:
        return {}

    scored: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (korean_word, code_word), count in pairs.items():
        if count < _MIN_PAIRS:
            continue
        # 점별 상호정보량 — 그냥 자주 붙은 것과 '특별히' 붙은 것을 가른다.
        pmi = math.log((count / seen)
                       / ((ko_total[korean_word] / seen) * (en_total[code_word] / seen)))
        if pmi > 0:
            scored[korean_word].append((pmi, code_word))
    return {k: [w for _, w in sorted(v, reverse=True)] for k, v in scored.items()}


# 조사를 떼고(접두사로) 찾는 것도 재 봤지만 이득이 없었다 — 짧은 어간이 엉뚱한 말에
# 걸려 오히려 나빠졌고("정제를"→string, "이유를"→int), 4자 이상으로 막으면 완전일치와
# 같아졌다(R@1 2자 0.410 · 3자 0.410 · 4자 0.423 = 완전일치 0.423). 그래서 안 넣는다.


def bridge_terms(lexicon: dict[str, list[str]], query: str,
                 per_word: int = _PER_WORD) -> str:
    """질의의 한글어를 이 코드베이스가 쓰는 말로 바꿔 붙인다(원문은 그대로 둔다)."""
    if not lexicon:
        return ""
    added: list[str] = []
    for token in tokenize(query):
        if _is_korean(token):
            added.extend(lexicon.get(token, [])[:per_word])
    return " ".join(dict.fromkeys(added))
