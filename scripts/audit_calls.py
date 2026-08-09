"""호출 엣지의 유령을 센다 — 주석·문자열 속 산문에서 온 것들.

호출 스캐너는 `이름(` 패턴을 찾는데, 그 패턴은 주석과 도크스트링에도 있다:
  annotations: MCP annotations (read_only_hint 등).   ← 도크스트링
  # 반환 dict 에서 꺼낼 key (없으면 반환값 전체 사용)      ← 주석
이렇게 만들어진 calls 엣지는 없는 호출이다. 영향분석("이걸 고치면 누가 깨지나")과
중심성, 체인 확장이 그만큼 오염된다.
"""
import random
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import eval_mr_search as E
from xgen_maker.kg.source import open_source
from xgen_maker.kg.calls import scan_call_names, strip_noncode

random.seed(3)


def main() -> None:
    sources = {}
    for spec in E.g.meta.get("sources", []):
        if spec.get("root"):
            sources[spec["repo"]] = open_source(spec["root"], spec.get("ref") or "")
    files = [(n["repo"], n["path"]) for n in E.g.nodes.values()
             if n.get("kind") == "file"
             and (n.get("path") or "").endswith((".py", ".ts", ".tsx", ".rs"))]
    sample = random.sample(files, min(300, len(files)))

    raw_total = clean_total = 0
    ghosts = []
    for repo, rel in sample:
        source = sources.get(repo)
        if source is None:
            continue
        try:
            text = source.read_text(rel)
        except OSError:
            continue
        raw = scan_call_names(text, strip=False)
        clean = scan_call_names(text)
        raw_total += len(raw)
        clean_total += len(clean)
        extra = raw - clean
        if extra and len(ghosts) < 5:
            ghosts.append((rel, sorted(extra)[:6]))

    print(f"표본 파일 {len(sample)}개")
    print(f"  원문 스캔 {raw_total}개 → 주석·문자열 제거 후 {clean_total}개")
    print(f"  산문에서 온 유령 이름 {raw_total - clean_total}개 "
          f"({(raw_total - clean_total) / max(raw_total, 1):.1%})")
    for rel, names in ghosts:
        print(f"  · {rel[:46]} → {names}")


if __name__ == "__main__":
    main()
