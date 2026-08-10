"""층화 표본이 전체와 같은 판단을 내리는지 확인한다.

빠르다고 쓸 수 있는 게 아니다. 상수를 훑었을 때 최적점이 전체와 같은 자리를
가리켜야 쓸 수 있다. 다르면 그 표본으로 정한 값은 전체에서 최적이 아니다.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bench import Benchmark, describe
from xgen_maker.kg import rank as rank_mod


def main() -> None:
    bench = Benchmark()
    print(describe(bench), flush=True)
    sample = bench.stratified(600)
    print(f"층화 표본 {len(sample)}건\n", flush=True)

    print("BM25 길이 정규화 — 전체 vs 층화")
    for value in (0.0, 0.1, 0.2):
        rank_mod._B = value
        bench.graph.touch()
        a1, b1, m1 = bench.score(bench.fresh)
        a2, b2, m2 = bench.score(sample)
        print(f"  B {value}  전체 R@10={b1:.3f} MRR={m1:.3f}"
              f"  |  층화 R@10={b2:.3f} MRR={m2:.3f}", flush=True)
    rank_mod._B = 0.0
    bench.graph.touch()


if __name__ == "__main__":
    main()
