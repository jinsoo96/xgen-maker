"""저장소 크기 보정을 어디에 맞출지 정한다.

기존 표본에서는 이득(R@1 +0.023)인데 홀드아웃에서는 손해(-0.009)다. 원인을 보니
표본 구성이었다 — 기존 표본은 정답이 작은 저장소에 몰려 있고(게이트웨이 14%·모델
13%, 노드 비중은 1%·3.6%), 홀드아웃은 정답 분포가 노드 비중을 그대로 따른다.
즉 그 상수는 '작은 저장소가 정답인 표본'에 맞춰져 있었다.

최근 구간에서도 같은지 갈라 본다. 최근에도 손해면 표본 치우침이 원인이고,
최근에만 이득이면 시기가 원인이다. 그 구분에 따라 값을 정한다.
"""
import collections
import json
import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from xgen_maker.kg.graph import Graph
from xgen_maker.kg import rank as rank_mod
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.kg.dense import DenseIndex
from xgen_maker.loop.pipeline import _fuse, _LEXICAL_MATERIAL
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)


def main() -> None:
    old = {c["title"] for c in
           json.loads((BENCH / "mr_cases.json").read_text(encoding="utf-8"))}
    cases = json.loads((BENCH / "mr_cases_v2.json").read_text(encoding="utf-8"))
    expansions = json.loads((BENCH / "expansions_v2.json").read_text(encoding="utf-8"))
    routes = json.loads((BENCH / "routes_v2.json").read_text(encoding="utf-8"))
    with np.load(BENCH / "qvectors_v2.npz", allow_pickle=True) as store:
        qvecs = {str(q): v for q, v in zip(store["queries"], store["vectors"])}

    graph = Graph.load(pathlib.Path("kg/merged.json"))
    index = DenseIndex("kg/vectors.npz")
    material = _LEXICAL_MATERIAL
    known = {n.get("path") for n in graph.nodes.values() if n.get("path")}
    variants = collections.defaultdict(set)
    for case in cases:
        variants[case["title"]].add(frozenset(case["files"]))
    ambiguous = {t for t, files in variants.items() if len(files) > 1}

    fresh = [c for c in cases
             if c["title"] not in old and not BOOK.search(c["title"])
             and c["title"] not in ambiguous and len(c["title"]) >= 15
             and (set(c["files"]) & known)
             and expansions.get(c["title"]) and c["title"] in qvecs]
    recent = [c for c in fresh if (c.get("merged_at") or "") >= "2026-07"]
    older = [c for c in fresh if (c.get("merged_at") or "") < "2026-07"]

    def score(subset):
        lex = lexicon(graph)
        r1 = r10 = mrr = 0
        for case in subset:
            query = case["title"]
            hint = routes.get(query, "")
            keywords = expansions.get(query, "")
            merged = f"{keywords} {bridge_terms(lex, query)}".strip() or query
            hits = _fuse(ksearch(graph, query, k=24, hint_repo=hint),
                         ksearch(graph, merged, k=24, hint_repo=hint),
                         k=material, head=1)
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
            fused = _fuse(hits[:material], dense, k=10, head=0)
            rank = next((i for i, h in enumerate(fused, 1)
                         if (h.get("path") or "") in case["files"]), None)
            if rank:
                r10 += 1
                mrr += 1 / rank
                if rank == 1:
                    r1 += 1
        n = len(subset) or 1
        return r1 / n, r10 / n, mrr / n

    print(f"홀드아웃 전체 {len(fresh)}건 (최근 {len(recent)} · 이전 {len(older)})",
          flush=True)
    for alpha in (0.0, 0.05, 0.1, 0.2):
        rank_mod._REPO_SIZE_DAMP = alpha
        graph.touch()
        a1, b1, c1 = score(recent)
        a2, b2, c2 = score(older)
        print(f"  α {alpha:<5} 최근 R@1={a1:.3f} R@10={b1:.3f} MRR={c1:.3f}"
              f"  |  이전 R@1={a2:.3f} R@10={b2:.3f} MRR={c2:.3f}", flush=True)
    rank_mod._REPO_SIZE_DAMP = 0.1


if __name__ == "__main__":
    main()
