"""MAKER 설정 — 안전 가드 상수와 실행 설정.

설계 원칙(기획서 §3.2): MR-only(보호 브랜치 불가침), observe/act 2모드,
기능 코드만(인프라 파일 veto), allow_write=False 기본(실레포 무단 변경 방지).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROTECTED_BRANCHES = {"main", "master", "develop", "stg", "staging", "release", "production"}
# 팀 브랜치 네이밍 규칙: feature/{설명} · fix/{설명} · refactor/{설명} · hotfix/{설명}
ALLOWED_BRANCH_PREFIXES = ("feature/", "fix/", "refactor/", "hotfix/", "chore/")
# 규칙: 브랜치명은 작업 내용을 명확하게. js·251205 등 의미 불명 이름 금지.
_MEANINGLESS_SLUG = re.compile(r"^(js|ts|py|tmp|test|temp|wip|\d+|[a-z]{1,2}\d*)$", re.I)
INFRA_PATTERNS = re.compile(
    r"(^|/)(docker-compose[^/]*\.ya?ml|Dockerfile[^/]*|\.gitlab-ci\.ya?ml|helm/|infra/|k8s/|\.github/workflows/)",
    re.I)

# 기본값은 로컬/제네릭 — 실제 엔드포인트는 .env(XGEN_MAKER_LLM_BASE)로만 주입(공개 시 노출 방지)
DEFAULT_LLM_BASE = os.environ.get("XGEN_MAKER_LLM_BASE", "http://localhost:8000/v1")
DEFAULT_LLM_MODEL = os.environ.get("XGEN_MAKER_LLM_MODEL", "gpt-4o-mini")


@dataclass
class MakerConfig:
    repos: dict[str, str] = field(default_factory=dict)   # repo명 → 로컬 경로
    kg_path: str = "kg/merged.json"
    mode: str = "observe"                                  # observe=푸시/MR 미실행, act=푸시+MR
    allow_write: bool = False                              # False면 브랜치/구현도 계획만(dry)
    theta: float = 0.7                                     # judge 게이트 임계값
    agent_cmd: str | None = None                           # "{prompt_path}" 치환, None=claude CLI
    agent_timeout: int = 1800
    llm_enabled: bool = True
    llm_base: str = field(default_factory=lambda: os.environ.get("XGEN_MAKER_LLM_BASE", DEFAULT_LLM_BASE))
    llm_model: str = field(default_factory=lambda: os.environ.get("XGEN_MAKER_LLM_MODEL", DEFAULT_LLM_MODEL))
    gitlab_url: str = field(default_factory=lambda: os.environ.get(
        "XGEN_MAKER_GITLAB_URL", "https://gitlab.example.com"))  # 실 호스트는 .env로
    gitlab_projects: dict[str, str] = field(default_factory=dict)  # repo명 → "group/repo" (config로 주입)
    target_branch: str = "develop"
    fetch_latest: bool = True                              # 작업 전 origin/target 최신 fetch + KG 갱신
    isolate_worktree: bool = False                         # tmp git worktree 격리(동시실행 충돌 방지)
    release_stages: list = field(default_factory=lambda: [  # develop→stg→main 릴리즈 사다리
        {"branch": "develop", "env": "dev", "role": "개발 통합"},
        {"branch": "stg", "env": "stg", "role": "스테이징 검증"},
        {"branch": "main", "env": "prd", "role": "운영 배포"},
    ])
    enable_verify: bool = False                            # 로컬 스택+Playwright 검증 (리소스 가드로 기본 off)
    enable_ui_verify: bool = False                         # UI/UX 검증(라우트 스냅샷+픽셀diff+비전판정)
    ui_converge: bool = False                              # UI 문제를 수렴 retry 신호로(브랜치 렌더 프리뷰 필요)
    preview_base: str = ""
    check_timeout: int = 600                               # 자동 테스트(checks) 타임아웃
    max_iterations: int = 3                                # 수렴 루프 최대 재시도(통과까지)
    verbose: bool = True                                   # 진행 로그 실시간 출력
    deploy_mode: str = "off"                               # off | dry_run | live (live=이중 인터록)
    deploy_env: str = "dev"
    enable_deploy_test: bool = False                       # MR 전 배포 렌더 검증(T1, 상사님 tmp 방식)
    infra_path: str = field(default_factory=lambda: os.environ.get(
        "XGEN_MAKER_INFRA_PATH", ""))                     # Helm 차트 위치(config/.env로 주입)
    worklogs_dir: str = "worklogs"
    learnings_dir: str = "learnings"                       # 작업 학습 메모리(실수 방지 참고)
    # MAKER가 작업 대상(GitLab) 레포에 커밋할 때 강제할 저자 — 조직 정보라 env로만 주입(소스 미포함)
    git_author_name: str = field(default_factory=lambda: os.environ.get("XGEN_MAKER_GIT_AUTHOR_NAME", ""))
    git_author_email: str = field(default_factory=lambda: os.environ.get("XGEN_MAKER_GIT_AUTHOR_EMAIL", ""))

    @property
    def gitlab_token(self) -> str:
        env = os.environ.get("XGEN_MAKER_GITLAB_TOKEN", "")
        if env:
            return env
        try:
            from .auth import load_auth
            return load_auth().gitlab_token
        except ImportError:
            return ""

    @classmethod
    def from_file(cls, path: str | Path) -> "MakerConfig":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        config = cls(**{k: v for k, v in data.items() if k in known})
        # 상대 경로는 config 파일 위치 기준으로 해석 — 어느 cwd에서든 maker 실행 가능
        base = path.resolve().parent
        if not Path(config.kg_path).is_absolute():
            config.kg_path = str(base / config.kg_path)
        if not Path(config.worklogs_dir).is_absolute():
            config.worklogs_dir = str(base / config.worklogs_dir)
        if not Path(config.learnings_dir).is_absolute():
            config.learnings_dir = str(base / config.learnings_dir)
        # config에 provider가 명시되지 않았으면 저장된 로그인(auth)을 따른다
        if "llm_base" not in data or "llm_model" not in data:
            config.apply_auth()
        return config

    def apply_auth(self) -> None:
        """저장된 maker login(auth)을 LLM/에이전트 설정에 반영."""
        try:
            from .auth import load_auth, apply_to_env
        except ImportError:
            return
        auth = load_auth()
        apply_to_env(auth)
        self.llm_base = auth.resolved_base()
        self.llm_model = auth.resolved_model()


def is_protected_branch(name: str) -> bool:
    return name.strip().lower() in PROTECTED_BRANCHES


def is_allowed_branch(name: str) -> bool:
    return name.startswith(ALLOWED_BRANCH_PREFIXES) and not is_protected_branch(name)


def branch_name_issue(name: str) -> str | None:
    """팀 네이밍 규칙 위반 사유 반환(없으면 None).

    규칙: feature/fix/refactor/hotfix/{설명} · 설명은 작업 내용 명확히 · js·251205 등 의미불명 금지.
    """
    if not name.startswith(ALLOWED_BRANCH_PREFIXES):
        return (f"prefix는 {'/·'.join(p.rstrip('/') for p in ALLOWED_BRANCH_PREFIXES)}/ 중 하나여야 함")
    if is_protected_branch(name):
        return "보호 브랜치명 사용 불가"
    slug = name.split("/", 1)[1] if "/" in name else ""
    if not slug:
        return "설명이 비어 있음 — 작업 내용을 명확히"
    if len(slug) < 4:
        return f"설명 '{slug}'가 너무 짧음 — 작업 내용을 명확히"
    if _MEANINGLESS_SLUG.match(slug):
        return f"의미 불명 이름 '{slug}' 금지 — 작업 내용을 명확히 (예: feature/ontology-v4)"
    return None


def suggest_branch(prefix: str, keywords: list[str]) -> str:
    """키워드로 의미있는 브랜치 슬러그 생성 (한글/특수문자 정리)."""
    import re as _re
    words = []
    for kw in keywords:
        w = _re.sub(r"[^a-zA-Z0-9]+", "-", str(kw).lower()).strip("-")
        if w and not _MEANINGLESS_SLUG.match(w):
            words.append(w)
    slug = "-".join(words)[:50].strip("-") or "change"
    return prefix + slug


def infra_files(paths: list[str]) -> list[str]:
    return [p for p in paths if INFRA_PATTERNS.search(p.replace("\\", "/"))]
