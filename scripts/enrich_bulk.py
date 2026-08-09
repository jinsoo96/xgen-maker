"""의미층 대량 주입 — 병렬로 채우고 중간마다 저장(재개 가능).

요약은 검색 점수에 들어간다(_META_KEYS). 그런데 지금 그래프에는 LLM 요약이 0개다.
프롬프트가 셸에 먹히고, 임시 디렉토리 정리 실패가 응답을 버리는 두 결함 때문에
이 단계가 사실상 돌지 못했다. 둘을 고쳤으니 실제로 채워 보고 효과를 잰다.
"""
import sys, time, pathlib
from xgen_maker.config import MakerConfig
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.enrich import enrich_llm

def main() -> None:
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    step = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    cfg = MakerConfig.from_file("maker.config.json")
    path = pathlib.Path(cfg.kg_path)
    graph = Graph.load(path)
    filled = 0
    while filled < total:
        t0 = time.perf_counter()
        stats = enrich_llm(graph, cfg.llm_base, cfg.llm_model, cfg.repos,
                           limit=min(step, total - filled), timeout=90, workers=6)
        graph.save(path)
        filled += stats["llm_done"]
        rate = stats["llm_done"] / max(time.perf_counter() - t0, 1) * 60
        print(f"  누적 {filled}/{total} · 이번 {stats['llm_done']}건 실패 {stats['llm_failed']} "
              f"· {rate:.0f}건/분 · 남은대상 {stats['remaining']}", flush=True)
        if stats.get("aborted") or stats["llm_done"] == 0:
            print("  중단:", stats.get("aborted", "진전 없음")); break
    print(f"완료 — 채운 요약 {filled}건")

if __name__ == "__main__":
    main()
