"""넓힌 표본에서 상수들의 최적점이 그대로인지 본다.

지금 값들은 294건 위에서 골랐다. 처음 보는 MR에서 최적점이 같은 자리에 있으면
조율이 일반화된 것이고, 옮겨 갔으면 그 표본에 맞춘 것이다. 성적이 낮다는 사실만으로는
둘을 못 가른다 — 표본이 더 어려울 수도 있으니까.

여기서 값을 바꾸지 않는다. 어디가 최적인지 보기만 한다.
"""
import json
import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from xgen_maker.kg.graph import Graph
from xgen_maker.kg import rank as rank_mod
from xgen_maker.kg import search as search_mod
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.kg.dense import DenseIndex
from xgen_maker.loop.pipeline import _fuse
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)


def load():
    cases = json.loads((BENCH / "mr_cases_v2.json").read_text(encoding="utf-8"))
    old = {c["title"] for c in
           json.loads((BENCH / "mr_cases.json").read_text(encoding="utf-8"))}
    expansions = json.loads((BENCH / "expansions_v2.json").read_text(encoding="utf-8"))
    routes = json.loads((BENCH / "routes_v2.json").read_text(encoding="utf-8"))
    with np.load(BENCH / "qvectors_v2.npz", allow_pickle=True) as store:
        qvecs = {str(q): v for q, v in zip(store["queries"], store["vectors"])}
    graph = Graph.load(pathlib.Path("kg/merged.json"))
    known = {n.get("path") for n in graph.nodes.values() if n.get("path")}

    # 같은 제목인데 정답이 다른 질의는 원리적으로 못 맞힌다 — 데이터의 성질이다.
    seen = {}
    for case in cases:
        seen.setdefault(case["title"], set()).add(frozenset(case["files"]))
    ambiguous = {t for t, files in seen.items() if len(files) > 1}

    usable = [c for c in cases
              if not BOOK.search(c["title"])
              and c["title"] not in ambiguous
              and len(c["title"]) >= 15
              and (set(c["files"]) & known)
              and expansions.get(c["title"])
              and c["title"] in qvecs
              and c["title"] not in old]
    return graph, usable, expansions, routes, qvecs


def main() -> None:
    graph, cases, expansions, routes, qvecs = load()
    index = DenseIndex("kg/vectors.npz")
    print(f"처음 보는 MR {len(cases)}건 (중복 제목·장부성·초단문 제외)\n", flush=True)

    def measure(material):
        lex = lexicon(graph)
        r1 = r10 = mrr = 0
        for case in cases:
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
        n = len(cases) or 1
        return r1 / n, r10 / n, mrr / n

    def report(label, value, material=12):
        a, b, m = measure(material)
        mark = "  ← 현재 값" if value else ""
        print(f"  {label:28} R@1={a:.3f} R@10={b:.3f} MRR={m:.3f}{mark}", flush=True)

    print("저장소 라우팅 가중")
    for weight in (1.0, 1.15, 1.3, 1.5):
        search_mod._REPO_HINT = weight
        report(f"  가중 {weight}", weight == 1.3)
    search_mod._REPO_HINT = 1.3

    print("\n저장소 크기 보정")
    for alpha in (0.0, 0.1, 0.2):
        rank_mod._REPO_SIZE_DAMP = alpha
        graph.touch()
        report(f"  α {alpha}", alpha == 0.1)
    rank_mod._REPO_SIZE_DAMP = 0.1

    print("\n화면 문구 무게")
    for weight in (1, 2, 3):
        rank_mod._LABEL_WEIGHT = weight
        graph.touch()
        report(f"  무게 {weight}", weight == 2)
    rank_mod._LABEL_WEIGHT = 2
    graph.touch()

    print("\n융합 재료")
    for material in (8, 12, 16, 20):
        report(f"  재료 {material}", material == 12, material)

    print("\nBM25 길이 정규화")
    for b_value in (0.1, 0.2, 0.4):
        rank_mod._B = b_value
        graph.touch()
        report(f"  B {b_value}", b_value == 0.2)
    rank_mod._B = 0.2


if __name__ == "__main__":
    main()
