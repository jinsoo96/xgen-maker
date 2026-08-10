"""벤치마크 정본 — 표본 정의와 착지 계산을 한 곳에 둔다.

지금까지 스크립트마다 제외 기준과 착지 코드를 베껴 썼다. 그러면 한 곳을 고칠 때
다른 곳이 낡고, 두 수치가 왜 다른지 아무도 모르게 된다. 여기가 유일한 정본이다.

층화 표본도 여기서 만든다. 전체 2,412건으로 상수 하나를 훑으면 설정당 3~4분이라
한 바퀴에 한 시간이 넘는다. 저장소·시기별 비율을 지킨 600건이면 훨씬 빠르고,
결론이 전체와 어긋나는지 확인할 수 있다(check_stratified 참조).
"""
from __future__ import annotations

import collections
import json
import pathlib
import random
import re

import numpy as np

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.kg.dense import DenseIndex
from xgen_maker.loop.pipeline import _fuse, _LEXICAL_MATERIAL
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
# 제목에 의도가 없는 MR — 릴리즈 트레인·브랜치 병합·되돌리기.
# ⚠️ 넓게 잡으면 멀쩡한 요청을 버린다. `release[:/ ]`로 두었더니 "release note 문구
# 수정"까지 제외됐다 — 그건 의도가 분명한 작업이고, 벤치마크에서 빼면 어려운 사례만
# 골라 버리는 셈이 된다. 대괄호 표기와 콜론 접두사, 환경 간 승격 표기만 본다.
BOOKKEEPING = re.compile(
    r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release:\s)"
    r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
# 이보다 짧은 제목은 요청이라 부를 수 없다("Main", "Stage").
MIN_TITLE = 15


class Benchmark:
    """머지된 MR로 만든 표본. 제외 사유를 세어 두고 물으면 답한다."""

    def __init__(self, graph: Graph | None = None):
        self.cases_all = json.loads((BENCH / "mr_cases_v2.json").read_text(encoding="utf-8"))
        self.old_titles = {c["title"] for c in
                           json.loads((BENCH / "mr_cases.json").read_text(encoding="utf-8"))}
        self.expansions = json.loads((BENCH / "expansions_v2.json").read_text(encoding="utf-8"))
        self.routes = json.loads((BENCH / "routes_v2.json").read_text(encoding="utf-8"))
        with np.load(BENCH / "qvectors_v2.npz", allow_pickle=True) as store:
            self.qvecs = {str(q): v for q, v in zip(store["queries"], store["vectors"])}
        self.graph = graph if graph is not None else Graph.load(pathlib.Path("kg/merged.json"))
        self.index = DenseIndex("kg/vectors.npz")
        self._known = {n.get("path") for n in self.graph.nodes.values() if n.get("path")}
        self.excluded = collections.Counter()
        self.cases = [c for c in self.cases_all if self._keep(c)]
        self.fresh = [c for c in self.cases if c["title"] not in self.old_titles]

    def _keep(self, case: dict) -> bool:
        title = case["title"]
        if BOOKKEEPING.search(title):
            self.excluded["장부성 MR"] += 1
            return False
        if len(title) < MIN_TITLE:
            self.excluded["제목이 너무 짧음"] += 1
            return False
        if title in self._ambiguous():
            self.excluded["같은 제목 다른 정답"] += 1
            return False
        if not (set(case["files"]) & self._known):
            self.excluded["그래프에 없는 파일"] += 1
            return False
        if not self.expansions.get(title) or title not in self.qvecs:
            self.excluded["확장어·벡터 미생성"] += 1
            return False
        return True

    def _ambiguous(self) -> set[str]:
        cached = getattr(self, "_amb", None)
        if cached is None:
            variants: dict[str, set] = {}
            for case in self.cases_all:
                variants.setdefault(case["title"], set()).add(frozenset(case["files"]))
            cached = self._amb = {t for t, v in variants.items() if len(v) > 1}
        return cached

    # ---- 착지 ----

    def land(self, query: str, k: int = 10) -> list[dict]:
        lex = lexicon(self.graph)
        material = _LEXICAL_MATERIAL
        hint = self.routes.get(query, "")
        keywords = self.expansions.get(query, "")
        merged = f"{keywords} {bridge_terms(lex, query)}".strip() or query
        hits = _fuse(ksearch(self.graph, query, k=24, hint_repo=hint),
                     ksearch(self.graph, merged, k=24, hint_repo=hint),
                     k=material, head=1)
        anchors = find_anchors(self.graph, query, keywords)
        if anchors:
            within = rank_within(aexp(self.graph, anchors), query, keywords, k=24)
            if within:
                hits = _fuse(within, hits, k=material)
        vector = np.asarray(self.qvecs[query], dtype=np.float32)
        vector /= np.linalg.norm(vector) + 1e-9
        scores = self.index._matrix @ vector
        top = np.argsort(-scores)[:material]
        dense = [{"score": float(scores[i]), **self.graph.nodes[self.index.ids[i]]}
                 for i in top if self.index.ids[i] in self.graph.nodes]
        return _fuse(hits[:material], dense, k=k, head=0)

    def score(self, subset) -> tuple[float, float, float]:
        r1 = r10 = mrr = 0
        for case in subset:
            hits = self.land(case["title"])
            rank = next((i for i, h in enumerate(hits, 1)
                         if (h.get("path") or "") in case["files"]), None)
            if rank:
                r10 += 1
                mrr += 1 / rank
                if rank == 1:
                    r1 += 1
        n = len(subset) or 1
        return r1 / n, r10 / n, mrr / n

    # ---- 층화 표본 ----

    def stratified(self, size: int = 600, seed: int = 1) -> list[dict]:
        """저장소·시기 비율을 지킨 부분 표본. 상수를 훑을 때 쓴다.

        무작위로 자르면 큰 저장소가 통째로 표본을 지배하고, 시기 구성이 달라져
        전체와 다른 결론이 나온다. 비율을 지키면 훨씬 적은 건수로 같은 판단을 한다.
        """
        buckets: dict[tuple[str, str], list[dict]] = {}
        for case in self.fresh:
            key = (case.get("repo", "?"), (case.get("merged_at") or "")[:7])
            buckets.setdefault(key, []).append(case)
        rng = random.Random(seed)
        picked: list[dict] = []
        total = len(self.fresh) or 1
        for key, group in sorted(buckets.items()):
            take = max(1, round(size * len(group) / total))
            picked.extend(rng.sample(group, min(take, len(group))))
        return picked


def describe(bench: "Benchmark") -> str:
    return (f"수집 {len(bench.cases_all)}건 → 쓸 수 있는 것 {len(bench.cases)}건 "
            f"(처음 보는 MR {len(bench.fresh)}건) · 제외 {dict(bench.excluded)}")
