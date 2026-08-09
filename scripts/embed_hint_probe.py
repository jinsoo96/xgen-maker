"""문서 없는 파일에 '임베딩 전용 힌트'를 채우면 의미 검색이 나아지는지 표본으로 잰다.

BM25에 넣으면 해로웠다(R@10 0.796→0.781). 그런데 임베딩은 산문을 잘 읽는다 —
정답 파일에 문서가 있으면 성공률 90.0%, 없으면 70.7%. 층을 갈라 임베딩에만
주면 그 손해 없이 이득만 취할 수 있는지 본다.
"""
import json, pathlib, sys, time
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from xgen_maker.config import MakerConfig
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.enrich import enrich_llm
from xgen_maker.kg.dense import node_text, _digest, embed_texts

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 300
OUT = pathlib.Path("bench/hint_vectors.npz")


def main() -> None:
    cfg = MakerConfig.from_file("maker.config.json")
    graph = Graph.load(pathlib.Path(cfg.kg_path))
    # 문서도 요약도 없는 파일만 — 이미 산문이 있는 노드는 손댈 이유가 없다
    bare = [n for n in graph.nodes.values()
            if n["kind"] == "file" and not (n["meta"].get("doc") or n["meta"].get("summary"))]
    print(f"문서 없는 파일 {len(bare)}개 중 {LIMIT}개에 힌트를 채운다", flush=True)
    for node in graph.nodes.values():
        node["meta"].pop("_skip", None)
    # enrich_llm은 kind 우선순위로 고르므로, 대상만 남긴 임시 그래프를 쓴다
    sub = Graph()
    sub.nodes = {n["id"]: n for n in bare[:LIMIT]}
    sub.edges = []
    t0 = time.perf_counter()
    stats = enrich_llm(sub, cfg.llm_base, cfg.llm_model, cfg.repos,
                       limit=LIMIT, timeout=90, workers=6, field="embed_hint")
    print(f"힌트 생성: {stats} · {time.perf_counter()-t0:.0f}초", flush=True)

    filled = [n for n in sub.nodes.values() if n["meta"].get("embed_hint")]
    texts = [node_text(n) for n in filled]
    vectors, ids = [], []
    for start in range(0, len(texts), 128):
        got = embed_texts(cfg.embed_base or "http://127.0.0.1:12341/v1",
                          cfg.embed_model or "Qwen/Qwen3-Embedding-8B",
                          texts[start:start + 128])
        if got is None:
            print("임베딩 실패 — 중단"); break
        vectors.extend(got)
        ids.extend(n["id"] for n in filled[start:start + 128])
    np.savez_compressed(OUT, ids=np.array(ids, dtype=object),
                        vectors=np.array(vectors, dtype=np.float16),
                        hints=json.dumps({n["id"]: n["meta"]["embed_hint"]
                                          for n in filled}, ensure_ascii=False))
    print(f"완료 — 힌트 {len(filled)}개 · 벡터 {len(ids)}개 → {OUT}")


if __name__ == "__main__":
    main()
