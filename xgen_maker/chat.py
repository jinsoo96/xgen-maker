"""maker chat — 대화형 터미널 (openxgen 채팅 UX 참고).

한 세션에서 KG를 메모리에 한 번만 로드하고 여러 쿼리를 연속 처리한다(반복 로드 없음).
자연어 쿼리는 MAKER 루프로, 슬래시 명령은 즉시 처리.

명령:
  <자연어>            현재 모드로 루프 실행 (기본 plan)
  /mode plan|observe|act   실행 모드 전환
  /search <질의>      KG 검색만
  /impact <노드id>    영향분석
  /stats              그래프 통계
  /repos              레포 목록
  /config <경로>      다른 config 로드
  /help  /quit
"""
from __future__ import annotations

import sys
from pathlib import Path

from .config import MakerConfig
from .kg.graph import Graph
from .kg.search import search, impact
from .loop.pipeline import MakerLoop

BANNER = r"""
  ___ ___ __ _ _ _____ ___
 |   |   |  ' | |_   _| _ \   XGEN MAKER · chat
 |_|_|_|_|_|_,_| |_| |___/   쿼리 하나로 개발 자동화
"""


def _load(config_path: str | None) -> tuple[MakerConfig, Graph]:
    config = MakerConfig.from_file(config_path) if config_path else MakerConfig()
    graph = Graph.load(config.kg_path)
    from .kg.overlay import load_overlay, apply_overlay
    overlay = load_overlay(Path(config.kg_path).parent / "overlay.json")
    if overlay["node_overrides"] or overlay["custom_edges"]:
        apply_overlay(graph, overlay)
    return config, graph


def _print_hits(hits: list[dict]) -> None:
    if not hits:
        print("  (일치 없음)")
        return
    for hit in hits:
        line = f"  {hit.get('score', ''):>6}  [{hit['kind']}] {hit['name']}"
        print(line.rstrip())
        print(f"          {hit['repo']}:{hit['path']}" +
              (f":{hit['line']}" if hit.get("line") else ""))


def run_chat(config_path: str | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(BANNER)
    try:
        config, graph = _load(config_path)
    except (FileNotFoundError, OSError) as error:
        print(f"KG 로드 실패: {error}\n먼저 `maker kg build`+`merge`로 그래프를 만드세요.")
        return
    repos = sorted({n["repo"] for n in graph.nodes.values()})
    print(f"  KG: {len(graph.nodes):,} 노드 · {len(repos)} 레포 · 모드 [{config.mode}"
          f"{'/plan' if not config.allow_write else ''}]")
    print("  명령: /help  ·  종료: /quit\n")

    while True:
        try:
            line = input("maker❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            break
        if line == "/help":
            print(__doc__)
            continue
        if line == "/stats":
            stats = graph.stats()
            print(f"  {stats['nodes']:,} 노드 / {stats['edges']:,} 엣지")
            print(f"  노드: {stats['nodes_by_kind']}")
            continue
        if line == "/repos":
            print("  " + ", ".join(repos))
            continue
        if line.startswith("/mode"):
            parts = line.split()
            if len(parts) == 2 and parts[1] in ("plan", "observe", "act"):
                if parts[1] == "plan":
                    config.allow_write = False
                else:
                    config.allow_write = True
                    config.mode = parts[1]
                print(f"  모드 → {parts[1]}")
            else:
                print("  사용: /mode plan|observe|act")
            continue
        if line.startswith("/search "):
            _print_hits(search(graph, line[8:], k=8))
            continue
        if line.startswith("/impact "):
            results = impact(graph, line[8:].strip(), depth=3)
            if not results:
                print("  (영향 노드 없음 / 노드 미존재)")
            for node in results[:20]:
                print(f"  d={node['distance']} [{node['kind']}] {node['name']}"
                      f"  {node['repo']}:{node['path']}")
            continue
        if line.startswith("/config "):
            try:
                config, graph = _load(line[8:].strip())
                repos = sorted({n["repo"] for n in graph.nodes.values()})
                print(f"  로드됨: {len(graph.nodes):,} 노드")
            except (FileNotFoundError, OSError) as error:
                print(f"  config 로드 실패: {error}")
            continue
        if line.startswith("/"):
            print(f"  알 수 없는 명령: {line} (/help)")
            continue

        # 자연어 → 루프 실행 (그래프는 메모리 재사용)
        loop = MakerLoop(config, graph=graph)
        report = loop.run(line)
        outcome = report.get("outcome", "?")
        print(f"\n  → {outcome}", end="")
        if report.get("branch"):
            print(f" · 브랜치 {report['branch']}", end="")
        if report.get("mr_draft"):
            print(f" · MR초안 {report['mr_draft']}", end="")
        if report.get("mr", {}).get("url"):
            print(f" · MR {report['mr']['url']}", end="")
        if report.get("answer"):
            print("\n" + report["answer"], end="")
        print("\n")
