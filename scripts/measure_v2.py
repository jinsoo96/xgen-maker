"""넓힌 표본으로 잰다 — 그리고 '처음 보는 MR'만 따로 잰다.

지금 상수들은 294건 위에서 골랐다. 그 표본에 없던 MR에서도 같은 성적이 나오면
조율이 일반화된 것이고, 떨어지면 과적합이다. 그 구분이 이 측정의 목적이다.
새 사례에는 손을 대지 않는다 — 여기서 다시 조율하면 홀드아웃이 아니게 된다.
"""
import json
import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.kg.dense import DenseIndex
from xgen_maker.loop.pipeline import _fuse, _LEXICAL_MATERIAL
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)


def main() -> None:
    cases = json.loads((BENCH / "mr_cases_v2.json").read_text(encoding="utf-8"))
    old_titles = {c["title"] for c in
                  json.loads((BENCH / "mr_cases.json").read_text(encoding="utf-8"))}
    expansions = json.loads((BENCH / "expansions_v2.json").read_text(encoding="utf-8"))
    routes = json.loads((BENCH / "routes_v2.json").read_text(encoding="utf-8"))
    with np.load(BENCH / "qvectors_v2.npz", allow_pickle=True) as store:
        qvecs = {q: v for q, v in zip(store["queries"], store["vectors"])}

    graph = Graph.load(pathlib.Path("kg/merged.json"))
    index = DenseIndex("kg/vectors.npz")
    lex = lexicon(graph)
    material = _LEXICAL_MATERIAL
    known = {n.get("path") for n in graph.nodes.values() if n.get("path")}

    usable, skipped = [], 0
    for case in cases:
        title = case["title"]
        if BOOK.search(title) or title not in expansions or title not in qvecs:
            skipped += 1
            continue
        # 그래프에 없는 파일만 고친 MR은 착지할 좌표 자체가 없다 — 검색 문제가 아니다.
        if not (set(case["files"]) & known):
            skipped += 1
            continue
        usable.append(case)

    def land(query, k=10):
        hint = routes.get(query, "")
        keywords = expansions.get(query, "")
        merged = f"{keywords} {bridge_terms(lex, query)}".strip() or query
        hits = _fuse(ksearch(graph, query, k=24, hint_repo=hint),
                     ksearch(graph, merged, k=24, hint_repo=hint), k=material, head=1)
        anchors = find_anchors(graph, query, keywords)
        if anchors:
            within = rank_within(aexp(graph, anchors), query, keywords, k=24)
            if within:
                hits = _fuse(within, hits, k=material)
        vector = np.asarray(qvecs[query], dtype=np.float32)
        vector /= np.linalg.norm(vector) + 1e-9
        scores = index._matrix @ vector
        top = np.argsort(-scores)[:material]
        dense = [{"score": float(scores[i]), **graph.nodes[index.ids[i]]}
                 for i in top if index.ids[i] in graph.nodes]
        return _fuse(hits[:material], dense, k=k, head=0)

    def score(subset):
        r1 = r10 = mrr = 0
        for case in subset:
            hits = land(case["title"])
            rank = next((i for i, h in enumerate(hits, 1)
                         if (h.get("path") or "") in case["files"]), None)
            if rank:
                r10 += 1
                mrr += 1 / rank
                if rank == 1:
                    r1 += 1
        n = len(subset) or 1
        return r1 / n, r10 / n, mrr / n

    fresh = [c for c in usable if c["title"] not in old_titles]
    seen = [c for c in usable if c["title"] in old_titles]
    print(f"수집 {len(cases)}건 · 쓸 수 있는 것 {len(usable)}건 (제외 {skipped})")
    print(f"  처음 보는 MR {len(fresh)}건 · 기존 표본과 겹치는 것 {len(seen)}건\n")
    for label, subset in (("전체", usable), ("처음 보는 MR(홀드아웃)", fresh),
                          ("기존 표본", seen)):
        if not subset:
            continue
        a, b, m = score(subset)
        print(f"  {label:22} {len(subset):4}건  R@1={a:.3f} R@10={b:.3f} MRR={m:.3f}")


if __name__ == "__main__":
    main()
