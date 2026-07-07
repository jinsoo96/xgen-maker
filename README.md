# XGEN MAKER

> **쿼리 하나로 코드베이스를 개발한다.** 자연어로 "이 버그 고쳐줘"라고 하면 —
> 코드 지식그래프로 착지점을 찾고 · 항상 최신 브랜치에서 · 코딩 에이전트가 구현하고 ·
> 샌드박스에서 테스트하며 **통과할 때까지 스스로 고치고**(수렴 루프) · **MR 준비**까지 자동으로 한다.
> 배포는 사람이. 관측은 read-only.

의존성 거의 0(Python 표준 라이브러리 중심). 로컬/온프레미스에서 도는 자가 호스팅 개발 자동화 도구.

> ⚠️ **설정 없이는 아무것도 안 된다(의도된 것).** 실제 GitLab/LLM/도메인 정보는 전부 `.env`·`maker.config.json`에만 있고, 이 저장소엔 예시(placeholder)만 있다. 자기 자격/엔드포인트를 채우지 않으면 동작하지 않는다.

---

## 무엇을 하나

```
쿼리 → intent 분류 → KG 착지(코드 어디를 고칠지) → 워크플로우 체인 확장
    → 항상 최신 GitLab 코드로 브랜치 → 코딩 에이전트 구현
    → [수렴 루프] 샌드박스+테스트+품질judge → 실패하면 되먹여 재시도 → 통과까지
    → 배포 렌더 검증(helm) → MR 준비   ◀── 여기까지 자동
사람: MR 리뷰·머지 → 빌드 → 배포 (MAKER는 관측만)
```

- **지식그래프 3평면**: 코드(AST·엔드포인트) + UI/UX(라우트·화면) + 인프라(배포 토폴로지). 항상 최신으로 증분 유지.
- **수렴 루프**: 구현 → 검증 → 실패 시 자가수정 반복(엔진 샌드박스 격리).
- **안전**: 보호 브랜치 불가침, 브랜치 네이밍 규칙, MR-only(배포 안 함), 롤백(`maker undo`), worktree 격리.
- **관측**: 작업 이력·MR·Jenkins/ArgoCD 상태 read-only.
- **표면 3종**: CLI · 웹 대시보드 · MCP(다른 에이전트가 호출). 셋 다 같은 엔진.

---

## 설치 & 로그인

```bash
pip install -e .                 # → 어디서든 `maker` 명령
cp .env.example .env             # .env에 자기 GitLab/LLM/도메인 값 채움 (자동 로드)
cp maker.config.example.json maker.config.json   # 레포 경로·gitlab_projects 매핑 채움

maker login                      # Claude CLI 구독 로그인 감지 → 코딩+판단+요약 전부 이 로그인 (API 키 불필요)
maker login --gitlab-user <이메일> --gitlab-password <비번>   # GitLab (2FA면 --gitlab-token <PAT>)
maker whoami                     # Claude/GitLab 로그인 지속 상태
maker doctor --config maker.config.json          # 자가검증 — 모든 능력이 실제로 되는지
```

자격은 3소스 우선순위: **실제 환경변수 → `.env` → `~/.xgen-maker/auth.json`**. 어느 것이든 있으면 재입력 불필요.

---

## 사용법

### 1) 지식그래프 만들기 (프로젝트당 1회 + 이후 자동 증분)
```bash
maker kg build --repo "core=/path/to/core" --repo "frontend=/path/to/frontend::apps/web/src" --out kg
maker kg merge kg/*.repo.json --out kg/merged.json
maker kg enrich --kg kg/merged.json              # 의미층 요약
maker kg infra --path /path/to/infra-repo        # 인프라(배포 토폴로지) KG (선택)
maker kg dashboard --kg kg/merged.json           # 브라우저로 그래프 탐색
```

### 2) 쿼리 실행 — 3가지 표면 중 하나
```bash
# 웹 대시보드 (브라우저에서 쿼리 + 실시간 로그 + 작업이력·MR·배포상태 탭)
maker web --config maker.config.json --open

# 대화형 터미널
maker chat --config maker.config.json

# 원샷 CLI (실시간 진행 로그)
maker run "온톨로지 그래프 안 바뀌는 버그 고쳐줘" --config maker.config.json
```

실행 모드: `plan`(분석·MR초안만, 레포 미접촉) · `observe`(브랜치+커밋+MR초안, 푸시 안 함) · `act`(push + 실제 MR).

### 3) 관측 (read-only)
```bash
maker history          # MAKER 자기 작업 이력
maker mrs              # 내 MR / MAKER가 만든 MR
maker branches --repo frontend    # 브랜치 개요
maker status           # 릴리즈 사다리 + Jenkins + ArgoCD 상태
maker learn --repo core           # 작업 학습 메모리(실수 방지)
```

### 4) 안전·검증
```bash
maker undo --config maker.config.json            # 마지막 브랜치·커밋 되돌림(--yes 실행, --remote 원격까지)
maker sdk                                        # 의존 엔진 버전 드리프트 + 계약 자가검증
maker deploy test --repo core                    # 배포 렌더 검증(helm, tmp 격리)
maker engine register                            # 엔진 stage로 등록(R3)
```

---

## 핵심 개념

| 개념 | 설명 |
|---|---|
| **항상 최신** | 작업 전 `origin/develop` fetch → 최신에서 분기 → 변경분을 KG에 반영. `fetch_latest`(기본 on) |
| **수렴 루프** | 구현 → 샌드박스+테스트+judge → 실패 시 에러 되먹여 재구현, `max_iterations`까지. xgen-sdk 엔진 샌드박스 임포트 |
| **릴리즈 사다리** | `develop → stg → main` (= dev/stg/prd). MR은 develop에, 승격은 순차, main 직접머지 금지 |
| **경계** | 자동은 **MR 준비까지** · 배포·빌드·ArgoCD sync는 **사람 수동** · CI 상태는 **read-only 관측** |
| **학습 메모리** | 실패/성공 교훈을 `learnings/`에 쌓아 다음 작업 프롬프트에 주입(실수 방지) |
| **자가검증** | `maker doctor`(능력 실동작) + `maker sdk`(의존 엔진 계약·드리프트) |

---

## 보안 (공개 저장소 안전 + 인가된 사용자만)

**1) 코드에 자격·엔드포인트·조직 정보를 담지 않는다.** 전부 gitignore된 로컬 파일에만:

- `.env` — 토큰·URL·계정·작업 커밋 저자 (커밋 금지, `.env.example`은 placeholder만)
- `maker.config.json` — 레포 경로·프로젝트 매핑 (커밋 금지, `.example`만)
- `~/.xgen-maker/auth.json` — 로그인 저장 (홈 디렉토리)
- `worklogs/` · `learnings/` · `kg/` — 작업 기록·그래프·산출물 (로컬만)

→ 저장소를 public으로 바꿔도 dev/stg 도메인·계정·MR·인프라 정보가 노출되지 않는다.

**2) 실제 작업(act)은 인가된 사용자만.** 코드는 누구나 받을 수 있지만, 실 인프라
push·MR은 **인가 게이트**를 통과해야 한다(작업 시작 전 fail-fast 차단):

- 유효한 GitLab 토큰 + **대상 프로젝트 Developer+ 멤버십**을 요구 (실 GitLab이 권위).
- `gitlab_url`이 미설정/예시(placeholder)면 act 자동 거부 — 실 대상이 아니면 동작 안 함.
- 자격/엔드포인트를 모르고 멤버십도 없는 외부인은 코드를 받아도 실 작업을 할 수 없다.

**3) 커밋 신원 분리.** 이 도구 자체 코드의 커밋 저자와, 도구가 대상 레포에 남기는
작업 커밋 저자는 다르다. 작업 저자는 `XGEN_MAKER_GIT_AUTHOR_NAME/EMAIL`(`.env`)로만
주입하며 코드에 하드코딩하지 않는다. `plan`/`observe` 모드는 로컬만 다루므로 게이트 없이 탐색 가능.

---

## 개발

```bash
python -m unittest discover -s tests    # 테스트
maker doctor --config maker.config.json # 전체 자가검증
```

의존 엔진(선택): `pip install -e .[harness]`(수렴 샌드박스·엔진 stage) · `.[infra]`(인프라 KG) · `.[ui]`(픽셀 diff).
없어도 코어는 로컬 폴백으로 동작.
