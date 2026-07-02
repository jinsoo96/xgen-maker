# XGEN MAKER

> **"쿼리 하나로 XGEN을 만든다."** — xgen2.0 코드베이스를 지식그래프 자산으로 물화하고,
> 하네스 루프가 그 지도를 소비해 브랜치→구현→검증→**MR 준비**까지 자동화한다.
> 기획 정본: [XGEN-MAKER-PLAN.md](XGEN-MAKER-PLAN.md) · 독립 프로젝트(이 폴더에서 완결) · 의존성 **stdlib 0개**(Python ≥3.10)

## 폴더 구조

```
xgen_maker/
  kg/            A평면 — 지식그래프 (2층: 결정론 뼈대 + 의미층)
    graph.py         노드/엣지 모델, 저장/병합
    extract_python.py   Python AST: 함수/클래스/임포트/FastAPI 엔드포인트/호출/docstring
    extract_typescript.py TS/JS: export/임포트/API 호출(api_call)/JSDoc
    workspaces.py       pnpm 워크스페이스 + tsconfig paths alias 해석 (feature 단위)
    routes_nextjs.py    Next.js App Router → 화면 라우트 (UI/UX KG 골격)
    crossrepo.py        FE api_call ↔ BE endpoint 매칭(resolves_to) — 차별화 지점
    build.py            레포 워커 + 병합 + 증분 갱신(refresh_files) + HEAD 기준점 기록
    sync.py             git 기준 증분 동기화(커밋·워킹트리·삭제·rename) + 자동 훅 설치
    search.py           착지점 검색 + 역방향 영향분석(impact)
    enrich.py           의미층 — 결정론(docstring/구조) + LLM 평문요약 2단 주입
    domains.py          도메인/플로우 뷰(라우트→feature→API호출→엔드포인트) HTML
    tour.py             가이드 투어 — 의존성 순서 읽기 가이드(md)
    dashboard.py        자기완결 단일 HTML 대시보드(오프라인)
  loop/          B평면 — MAKER 루프 (①~⑩)
    intent.py        ② 쿼리 → bug/feature/refactor/question
    git_ops.py       ⑤ 브랜치/커밋/푸시 — 보호브랜치·prefix 가드 코드로 강제
    implement.py     ⑥ 코딩에이전트 호출(기본 claude CLI, agent_cmd로 치환)
    verify.py        ⑦ 스택 프로파일 제안 + Playwright + docker RAM 가드
    judge.py         ⑧ 품질 게이트(LLM judge → 휴리스틱 폴백, 인프라 파일 veto)
    mr.py            ⑨ MR 초안(무엇/왜/원인/접근/영향+KG영향분석) + GitLab API
    journal.py       ⑩ 세션 journal(jsonl + SUMMARY.md) — 작업로그 확인가능
    pipeline.py      오케스트레이터 (plan-only / observe / act 3단 안전모드)
  mcp_server.py  KG를 MCP 툴로 노출(stdio) — kg_search/kg_node/kg_impact/kg_stats
  cli.py, config.py, llm.py
kg/              KG 산출물 (merged.json, dashboard.html)
worklogs/        세션 journal
tests/           unittest 35개
```

## 빠른 시작

```powershell
cd D:\xgen-maker
$env:PYTHONIOENCODING='utf-8'

# 1) KG 빌드 (레포별 → 병합+크로스레포 링크)
python -m xgen_maker kg build --repo "xgen-core=D:\xgen2.0\xgen-core" `
  --repo "xgen-workflow=D:\xgen2.0\xgen-workflow" `
  --repo "xgen-frontend-app=D:\xgen2.0\xgen-frontend::apps/web/src" `
  --repo "xgen-frontend-lib=D:\xgen2.0\xgen-frontend::packages/api-client" --out kg
python -m xgen_maker kg merge kg\*.repo.json --out kg\merged.json

# 2) 의미층 주입 + UI/UX 뷰 (결정론은 항상, LLM은 도달 시)
python -m xgen_maker kg enrich --kg kg\merged.json --config maker.config.json --limit 200
python -m xgen_maker kg domains --kg kg\merged.json --out kg\domain-map.html
python -m xgen_maker kg tour --repo xgen-core --kg kg\merged.json --out kg\TOUR-xgen-core.md

# 3) 확인가능 — 대시보드/도메인맵 (브라우저로 열기)
python -m xgen_maker kg dashboard --kg kg\merged.json --out kg\dashboard.html

# 4) 검색·영향분석
python -m xgen_maker kg search "ontology graph" --kg kg\merged.json
python -m xgen_maker kg impact "xgen-core:main.py" --kg kg\merged.json

# 5) MAKER 루프 (기본 = plan-only: 실레포 미접촉, MR 초안까지)
python -m xgen_maker run "ontology graph 조회 API 버그 고쳐줘" --config maker.config.json

# 6) KG를 MCP 툴로 (Claude Code/하네스 ToolSource 연결)
python -m xgen_maker mcp --kg kg\merged.json

# 7) 증분 동기화 — 코드가 바뀌면 KG가 따라온다
python -m xgen_maker kg sync --kg kg\merged.json          # 수동/스크립트: 변경 파일만 재추출
python -m xgen_maker kg hook install --repo-path D:\xgen2.0\xgen-core --kg D:\xgen-maker\kg\merged.json
#   → post-commit/post-merge/post-checkout 훅이 커밋·풀·브랜치전환마다 sync 자동 실행 (UA --auto-update 대응)
```

## KG 신선도 3중 트리거

| 트리거 | 시점 | 메커니즘 |
|---|---|---|
| MAKER 루프 | 루프가 MR 준비 직후 (⑩) | `refresh_files` — 루프가 만든 변경 파일 정밀 반영 |
| `kg sync` | 수동/CI/스케줄 | 빌드 시 기록된 레포별 HEAD ↔ 현재 HEAD diff + 워킹트리 변경만 재추출 |
| git 훅 | 커밋·머지·체크아웃 | `kg hook install` — 자동 sync (opt-in, 기존 훅 있으면 건드리지 않음) |

기준 HEAD가 소실되면(rebase 등) 조용히 틀리지 않고 `full_rebuild_needed`를 보고한다.

## 안전 3단 모드

| 모드 | 하는 것 | 설정 |
|---|---|---|
| **plan-only** (기본) | 착지분석+영향분석+MR초안. 실레포 미접촉 | `allow_write: false` |
| **observe** | 로컬 브랜치+구현+judge+커밋+MR초안. 푸시 없음 | `allow_write: true, mode: observe` |
| **act** | + 기능브랜치 푸시 + GitLab MR 생성. **머지는 사람** | `mode: act` + `XGEN_MAKER_GITLAB_TOKEN` |

코드로 강제되는 불변: 보호 브랜치(develop/main/stg…) checkout·push 불가 ·
브랜치 prefix(fix/·feature/·refactor/·chore/) 강제 · 인프라 파일 변경 judge veto ·
docker 스택 추가 기동 시 RAM 가드.

## Claude Code MCP 등록 예

```json
{"mcpServers": {"xgen-maker-kg": {
  "command": "python",
  "args": ["-m", "xgen_maker", "mcp", "--kg", "D:\\xgen-maker\\kg\\merged.json"],
  "cwd": "D:\\xgen-maker"}}}
```

## 테스트

```powershell
python -m unittest discover -s tests -v   # 35 tests
```

## 현재 상태 (2026-07-02 실검증)

- 실레포 5스코프 KG(features 172개 워크스페이스 포함): **13,943 노드 / 22,367 엣지** —
  엔드포인트 848 · 화면 라우트 41 · feature 227 · FE API호출 156 · imports 4,763(alias 해석 후)
- 크로스레포 `resolves_to` 링크 **63개** (리터럴 앵커링 매칭 — 오탐 방어 규칙 적용)
- **의미층**: 결정론 요약 13,943 노드 전체 주입(docstring 우선) · LLM 요약은 vLLM 도달 시 `kg enrich`로 증분 주입(재개 가능)
- **UI/UX 뷰**: 도메인 20개 · domain-map.html(라우트→feature→API호출→엔드포인트 플로우) · TOUR-*.md(의존성 읽기 순서)
- MCP 왕복·plan-only 루프 E2E 검증 완료. 테스트 48개 통과.
- Understand-Anything 실측 비교: `_ua-eval/UA-COMPARISON.md` (동일 슬라이스 정량/정성 비교)
