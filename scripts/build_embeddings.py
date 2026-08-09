"""노드 임베딩 색인 만들기 — 사내 vLLM(내부망) 경유.

어휘 검색은 천장에 닿았다. 남은 실패는 "관리자 SQL 콘솔의 role·user 컬럼 오탐 제거"
→ sql_safety.py처럼 말이 겹치지 않는 의미 문제다. 그건 어휘로는 못 가른다.

외부로 코드를 보내지 않는다 — 사내 vLLM(점프호스트 뒤)이라 내부망 안에서 끝난다.
재개 가능하게 중간마다 저장한다(터널이 끊겨도 이어서).
"""
import json
import pathlib
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from xgen_maker.kg.graph import Graph

BASE = "http://127.0.0.1:12341/v1"
MODEL = "Qwen/Qwen3-Embedding-8B"
BATCH = 128
OUT = pathlib.Path(__file__).resolve().parent.parent / "bench" / "node_vectors.npz"
# 컨테이너 노드는 좌표가 아니다 — 착지 대상이 아니므로 벡터를 만들 이유가 없다.
SKIP_KINDS = {"repo"}


def node_text(node: dict) -> str:
    """임베딩에 넣을 표현. 사람이 그 코드를 설명하듯 적는다."""
    parts = [f"{node.get('kind', '')} {node.get('name', '')}",
             f"{node.get('repo', '')}/{node.get('path', '')}"]
    meta = node.get("meta") or {}
    for key in ("summary", "doc"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip()[:400])
            break
    return " — ".join(p for p in parts if p.strip())


def embed(texts: list[str], retries: int = 3) -> list[list[float]]:
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                BASE + "/embeddings", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read())
            return [row["embedding"] for row in
                    sorted(data["data"], key=lambda d: d["index"])]
        except Exception as error:            # noqa: BLE001 — 터널 끊김·과부하 모두 재시도
            last = error
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"임베딩 실패: {last}")


def main() -> None:
    kg = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "kg/merged.json")
    graph = Graph.load(kg)
    ids, texts = [], []
    for node_id, node in graph.nodes.items():
        if node.get("kind") in SKIP_KINDS:
            continue
        ids.append(node_id)
        texts.append(node_text(node))

    done_ids, done_vecs = [], []
    if OUT.exists():
        cached = np.load(OUT, allow_pickle=True)
        done_ids = list(cached["ids"])
        done_vecs = list(cached["vectors"])
        print(f"이어서 — 이미 {len(done_ids)}개", flush=True)
    have = set(done_ids)
    todo = [(i, t) for i, t in zip(ids, texts) if i not in have]
    print(f"전체 {len(ids)} · 남음 {len(todo)}", flush=True)

    started = time.perf_counter()
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        vectors = embed([t for _, t in chunk])
        done_ids.extend(i for i, _ in chunk)
        done_vecs.extend(vectors)
        if (start // BATCH) % 10 == 0 or start + BATCH >= len(todo):
            np.savez_compressed(OUT, ids=np.array(done_ids, dtype=object),
                                vectors=np.array(done_vecs, dtype=np.float16))
            elapsed = time.perf_counter() - started
            rate = (start + len(chunk)) / max(elapsed, 1)
            print(f"  {len(done_ids)}/{len(ids)} · {rate:.0f}건/초", flush=True)
    np.savez_compressed(OUT, ids=np.array(done_ids, dtype=object),
                        vectors=np.array(done_vecs, dtype=np.float16))
    print(f"완료 — {len(done_ids)}개 → {OUT}")


if __name__ == "__main__":
    main()
