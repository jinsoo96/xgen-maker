"""의미 검색 — 말이 겹치지 않는 요청을 위한 층.

어휘 검색(BM25)은 질의의 낱말이 코드에 있어야 찾는다. 그런데 실제 요청은 그렇지
않다 — "관리자 SQL 콘솔의 role·user 컬럼 오탐 제거"의 답은 sql_safety.py인데,
질의의 어떤 낱말도 그 파일에 없다. role·user는 오히려 엉뚱한 컨트롤러를 끌어온다.

임베딩은 그 자리를 메운다. 대신 정밀도는 어휘가 낫다 — 그래서 고르지 않고 융합한다.

**코드를 외부로 보내지 않는다.** 사내 vLLM(OpenAI 호환) 주소를 설정에서 받고,
비어 있거나 닿지 않으면 이 층을 통째로 건너뛴다. 의미 검색이 없어도 착지는 된다.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

from .graph import Graph

# 컨테이너 노드는 착지 좌표가 아니다 — 벡터를 만들 이유가 없다.
_SKIP_KINDS = frozenset({"repo"})
_BATCH = 128
_DOC_CHARS = 400
# Qwen3-Embedding 계열의 표준 사용법: 문서는 원문 그대로, 질의만 "무엇을 찾는지"를
# 앞에 적는다. 실측(실제 머지된 MR): 지시문 없이 R@1 0.447 → 붙이면 0.518.
_INSTRUCT = ("Instruct: 개발 요청을 읽고 고쳐야 할 소스 코드 위치를 찾는다\n"
             "Query: {query}")


def node_text(node: dict) -> str:
    """임베딩에 넣을 표현 — 사람이 그 코드를 한 줄로 설명하듯."""
    parts = [f"{node.get('kind', '')} {node.get('name', '')}",
             f"{node.get('repo', '')}/{node.get('path', '')}"]
    meta = node.get("meta") or {}
    for key in ("summary", "doc"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip()[:_DOC_CHARS])
            break
    return " — ".join(p for p in parts if p.strip())


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def embed_texts(base: str, model: str, texts: list[str], timeout: int = 180) -> list | None:
    """OpenAI 호환 임베딩 호출. 실패하면 None — 호출자는 이 층을 건너뛴다."""
    if not base or not texts:
        return None
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    request = urllib.request.Request(base.rstrip("/") + "/embeddings", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError, KeyError):
        return None
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != len(texts):
        return None
    return [row["embedding"] for row in sorted(rows, key=lambda r: r.get("index", 0))]


class DenseIndex:
    """노드 벡터 저장소. numpy가 없거나 파일이 없으면 조용히 비활성이다."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.ids: list[str] = []
        self.digests: dict[str, str] = {}
        self._matrix = None
        self._load()

    # ---- 저장/적재 ----

    def _load(self) -> None:
        try:
            import numpy as np
        except ImportError:
            return
        if not self.path.exists():
            return
        try:
            store = np.load(self.path, allow_pickle=True)
            self.ids = [str(i) for i in store["ids"]]
            self.digests = dict(json.loads(str(store["digests"])))
            matrix = store["vectors"].astype("float32")
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        self._matrix = matrix / (norms + 1e-9)

    def save(self, ids: list[str], vectors, digests: dict[str, str]) -> None:
        import numpy as np
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.path, ids=np.array(ids, dtype=object),
                            vectors=np.asarray(vectors, dtype=np.float16),
                            digests=json.dumps(digests, ensure_ascii=False))

    @property
    def ready(self) -> bool:
        return self._matrix is not None and len(self.ids) > 0

    # ---- 질의 ----

    def search(self, graph: Graph, query: str, base: str, model: str,
               k: int = 10) -> list[dict]:
        if not self.ready or not base:
            return []
        vector = embed_texts(base, model, [_INSTRUCT.format(query=query)], timeout=30)
        if not vector:
            return []
        import numpy as np
        needle = np.asarray(vector[0], dtype="float32")
        needle /= (np.linalg.norm(needle) + 1e-9)
        scores = self._matrix @ needle
        take = min(k, len(scores))
        top = np.argpartition(-scores, take - 1)[:take]
        top = top[np.argsort(-scores[top])]
        hits = []
        for index in top:
            node = graph.nodes.get(self.ids[index])
            if node is not None:
                hits.append({"score": round(float(scores[index]), 4), **node})
        return hits


def build(graph: Graph, path: str | Path, base: str, model: str,
          on_progress=None) -> dict:
    """바뀐 노드만 다시 임베딩한다 — 그래프가 커도 갱신은 변경분만.

    노드 텍스트의 해시를 함께 저장해, 이름·경로·문서가 그대로면 건너뛴다.
    사라진 노드의 벡터는 버린다(안 버리면 없는 파일이 검색에 계속 뜬다).
    """
    try:
        import numpy as np
    except ImportError:
        return {"skipped": "numpy 없음"}
    if not base:
        return {"skipped": "임베딩 주소 미설정"}

    index = DenseIndex(path)
    wanted = {node_id: node_text(node) for node_id, node in graph.nodes.items()
              if node.get("kind") not in _SKIP_KINDS}
    keep_ids, keep_vectors = [], []
    if index.ready:
        for position, node_id in enumerate(index.ids):
            text = wanted.get(node_id)
            if text is not None and index.digests.get(node_id) == _digest(text):
                keep_ids.append(node_id)
                keep_vectors.append(index._matrix[position])
    fresh = [(nid, text) for nid, text in wanted.items()
             if nid not in set(keep_ids)]

    added = 0
    for start in range(0, len(fresh), _BATCH):
        chunk = fresh[start:start + _BATCH]
        vectors = embed_texts(base, model, [t for _, t in chunk])
        if vectors is None:
            break                      # 엔드포인트가 죽었다 — 여기까지만 저장한다
        keep_ids.extend(nid for nid, _ in chunk)
        keep_vectors.extend(vectors)
        added += len(chunk)
        if on_progress is not None:
            on_progress(added, len(fresh))

    digests = {nid: _digest(wanted[nid]) for nid in keep_ids if nid in wanted}
    if keep_ids:
        index.save(keep_ids, np.asarray(keep_vectors, dtype="float32"), digests)
    return {"total": len(wanted), "reused": len(keep_ids) - added, "added": added,
            "dropped": max(0, len(index.ids) - (len(keep_ids) - added))}
