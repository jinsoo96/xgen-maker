"""남은 실패를 들여다본다 — 고칠 수 있는 것과 원리적으로 못 맞히는 것을 가른다.

지표만 보면 "N건 남았다"로 끝나지만, 그 N건이 같은 종류가 아니다. 질의에 애초에
단서가 없는 것과, 단서가 있는데 못 찾는 것은 다른 일이다. 후자만 고칠 수 있다.

표본 정의와 착지는 bench.py(정본)를 쓴다 — 여기서 다시 정의하면 두 수치가 어긋난다.
"""
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bench import Benchmark, BENCH, describe
from xgen_maker.kg.rank import tokenize, node_terms


def main() -> None:
    bench = Benchmark()
    print(describe(bench), flush=True)
    scope = bench.stratified(int(sys.argv[1]) if len(sys.argv) > 1 else 600)
    print(f"층화 표본 {len(scope)}건으로 실패를 살펴본다\n", flush=True)

    path_repo = {}
    for node in bench.graph.nodes.values():
        if node.get("path"):
            path_repo.setdefault(node["path"], node.get("repo"))

    rows = []
    for case in scope:
        query = case["title"]
        if any((h.get("path") or "") in case["files"] for h in bench.land(query)):
            continue
        deep = bench.land(query, 300)
        rank = next((i for i, h in enumerate(deep, 1)
                     if (h.get("path") or "") in case["files"]), None)
        words = set(tokenize(query) + tokenize(bench.expansions.get(query, "")))
        target = set()
        for node in bench.graph.nodes.values():
            if (node.get("path") or "") in case["files"]:
                target |= set(node_terms(node))
        target_repos = {path_repo.get(f) for f in case["files"]} - {None}
        rows.append({"title": query, "files": sorted(case["files"]),
                     "repo": case.get("repo", ""), "merged_at": case.get("merged_at", ""),
                     "rank": rank, "shared": len(words & target),
                     "hint": bench.routes.get(query, ""),
                     "hint_ok": bench.routes.get(query, "") in target_repos})

    kinds = collections.Counter()
    for row in rows:
        first = row["files"][0]
        kinds["프론트" if first.startswith(("features/", "apps/", "packages/"))
              else "설정·매니페스트" if first.endswith((".toml", ".yaml", ".yml", ".json", ".md"))
              else "백엔드" if first.startswith(("controller/", "service/", "backend/",
                                              "src/", "editor/"))
              else "기타"] += 1
    nowhere = [r for r in rows if not r["rank"]]
    print(f"실패 {len(rows)}/{len(scope)}건 ({len(rows)/max(len(scope),1):.1%}) · {dict(kinds)}")
    print(f"  300위 안에도 없음 {len(nowhere)}건 · 레포 힌트 맞음 "
          f"{sum(1 for r in rows if r['hint_ok'])}건")
    print("\n단서가 가장 적은 것들:")
    for row in sorted(rows, key=lambda r: r["shared"])[:8]:
        print(f"  공유어{row['shared']:2} [{row['rank'] or '-'}위] {row['title'][:46]}")
        print(f"        → {row['files'][0][:56]}")
    (BENCH / "failures.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
