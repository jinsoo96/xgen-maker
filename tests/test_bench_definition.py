"""벤치마크 정의가 흔들리면 모든 수치가 흔들린다 — 그 정의를 못박는다.

스크립트마다 제외 기준을 베껴 쓰면, 한 곳을 고칠 때 다른 곳이 낡고 두 수치가 왜
다른지 아무도 모르게 된다. scripts/bench.py가 유일한 정본이어야 한다.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

BENCH_DIR = pathlib.Path(__file__).resolve().parents[1] / "bench"


class BenchmarkDefinitionTest(unittest.TestCase):
    def test_bookkeeping_titles_are_excluded(self):
        from bench import BOOKKEEPING
        for title in ("[release] develop → stage 머지",
                      "Merge branch 'fix/a' into 'develop'",
                      "Revert \"Merge branch 'x'\"",
                      "develop → stg 동기화"):
            self.assertTrue(BOOKKEEPING.search(title), f"제외돼야 한다: {title}")

    def test_real_requests_are_kept(self):
        from bench import BOOKKEEPING
        for title in ("로그인 실패 시 401 반환", "release note 문구 수정",
                      "fix(harness): 무한 continue 차단"):
            self.assertFalse(BOOKKEEPING.search(title), f"남아야 한다: {title}")

    def test_exclusions_are_counted_not_hidden(self):
        """왜 몇 건이 빠졌는지 말할 수 없으면 남은 수치도 못 믿는다."""
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "scripts" / "bench.py").read_text(encoding="utf-8")
        for reason in ("장부성 MR", "같은 제목 다른 정답", "그래프에 없는 파일",
                       "제목이 너무 짧음", "확장어·벡터 미생성"):
            self.assertIn(reason, source)

    def test_stratified_keeps_the_mix(self):
        """무작위로 자르면 큰 저장소가 표본을 지배한다 — 비율을 지켜야 같은 판단이 나온다."""
        if not (BENCH_DIR / "mr_cases_v2.json").exists():
            self.skipTest("벤치마크 데이터 없음")
        import collections
        from bench import Benchmark
        bench = Benchmark()
        sample = bench.stratified(600)
        self.assertGreater(len(sample), 400)
        def mix(cases):
            total = len(cases) or 1
            return {r: c / total for r, c in
                    collections.Counter(x.get("repo", "?") for x in cases).items()}
        full, part = mix(bench.fresh), mix(sample)
        for repo, share in full.items():
            if share < 0.05:
                continue
            self.assertLess(abs(part.get(repo, 0) - share), 0.05,
                            f"{repo} 비율이 어긋났다: 전체 {share:.0%} vs 표본 "
                            f"{part.get(repo, 0):.0%}")

    def test_sample_is_reproducible(self):
        if not (BENCH_DIR / "mr_cases_v2.json").exists():
            self.skipTest("벤치마크 데이터 없음")
        from bench import Benchmark
        bench = Benchmark()
        first = [c["title"] for c in bench.stratified(300, seed=7)]
        second = [c["title"] for c in bench.stratified(300, seed=7)]
        self.assertEqual(first, second, "씨앗이 같은데 표본이 달라지면 비교가 안 된다")


if __name__ == "__main__":
    unittest.main()
