"""최종 측정 — 의미층 전/후 · 라우팅 프롬프트 두 종을 같은 코드로 나란히 잰다."""
import json, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sweep256 as S
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.loop.pipeline import _fuse
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
REAL = [c for c in S.CASES if not BOOK.search(c["q"])]
SPLITS = {"A": REAL[0::2], "B": REAL[1::2], "C": REAL[:132], "D": REAL[132:]}
BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"


def routes(name):
    path = BENCH / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def run(graph_path, route_file, label):
    graph = Graph.load(pathlib.Path(graph_path))
    table = routes(route_file) if route_file else {}
    summaries = sum(1 for n in graph.nodes.values()
                    if (n.get("meta") or {}).get("summary_src") == "llm")
    lex = lexicon(graph)

    def land(query):
        hint = table.get(query, "")
        kw = S.kw_of(query)
        merged = f"{kw} {bridge_terms(lex, query)}".strip() or query
        hits = _fuse(ksearch(graph, query, k=24, hint_repo=hint),
                     ksearch(graph, merged, k=24, hint_repo=hint), k=10, head=1)
        anchors = find_anchors(graph, query, kw)
        if anchors:
            within = rank_within(aexp(graph, anchors), query, kw, k=24)
            if within:
                hits = _fuse(within, hits, k=10)
        return hits

    def score(cases):
        r1 = r10 = mrr = 0
        for case in cases:
            hits = land(case["q"])
            rank = next((i for i, h in enumerate(hits, 1)
                         if (h.get("path") or "") in case["files"]), None)
            if rank:
                r10 += 1
                mrr += 1 / rank
                if rank == 1:
                    r1 += 1
        n = len(cases)
        return r1 / n, r10 / n, mrr / n

    a, b, m = score(REAL)
    parts = "  ".join(f"{k}:{score(v)[1]:.3f}" for k, v in SPLITS.items())
    print(f"  {label:24}(요약 {summaries:4}) R@1={a:.3f} R@10={b:.3f} MRR={m:.3f} | 분할 {parts}",
          flush=True)


if __name__ == "__main__":
    print(f"기능질의 {len(REAL)}건")
    run("kg/merged.pre-enrich.json", "", "의미층X · 라우팅X")
    run("kg/merged.pre-enrich.json", "mr_repo_routes.json", "의미층X · 라우팅(독립)")
    run("kg/merged.pre-enrich.json", "mr_shipped_routes.json", "의미층X · 라우팅(배포)")
    run("kg/merged.json", "mr_shipped_routes.json", "의미층O · 라우팅(배포)")
