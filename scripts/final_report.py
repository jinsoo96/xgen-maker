"""정본 표본으로 최종 수치를 낸다 — README에 적을 숫자는 여기서만 나온다."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bench import Benchmark, describe
from xgen_maker.kg.companions import companion_files
from xgen_maker.kg.search import retrieve_chain


def main() -> None:
    bench = Benchmark()
    print(describe(bench), flush=True)
    recent = [c for c in bench.fresh if (c.get("merged_at") or "") >= "2026-06"]
    a, b, m = bench.score(bench.fresh)
    print(f"\n처음 보는 MR {len(bench.fresh)}건  R@1={a:.3f} R@10={b:.3f} MRR={m:.3f}",
          flush=True)
    a2, b2, m2 = bench.score(recent)
    print(f"  2026-06 이후 {len(recent)}건  R@1={a2:.3f} R@10={b2:.3f} MRR={m2:.3f}",
          flush=True)
    cov = full = 0.0
    for case in bench.fresh:
        hits = bench.land(case["title"])
        got = {(h.get("path") or "") for h in hits}
        got |= {x["path"] for x in companion_files(bench.graph, hits)}
        query = f"{case['title']} {bench.expansions.get(case['title'], '')}".strip()
        got |= {(n.get("path") or "") for n in
                retrieve_chain(bench.graph, query, k=6, hops=2)["chain"]}
        hit = len(got & set(case["files"])) / len(case["files"])
        cov += hit
        full += hit == 1.0
    n = len(bench.fresh) or 1
    print(f"  변경파일 덮음  평균 {cov/n:.1%} · 전부 덮음 {full/n:.1%}")


if __name__ == "__main__":
    main()
