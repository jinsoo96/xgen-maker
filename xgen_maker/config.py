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
ALLOWED_BRANCH_PREFIXES = ("fix/", "feature/", "refactor/", "chore/")
INFRA_PATTERNS = re.compile(
    r"(^|/)(docker-compose[^/]*\.ya?ml|Dockerfile[^/]*|\.gitlab-ci\.ya?ml|helm/|infra/|k8s/|\.github/workflows/)",
    re.I)

DEFAULT_LLM_BASE = "http://127.0.0.1:10051/v1"
DEFAULT_LLM_MODEL = "Qwen3.6-27B"


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
    gitlab_url: str = "https://gitlab.example.com"
    gitlab_projects: dict[str, str] = field(default_factory=dict)  # repo명 → "xgen2.0/xgen-workflow"
    target_branch: str = "develop"
    enable_verify: bool = False                            # 로컬 스택+Playwright 검증 (리소스 가드로 기본 off)
    preview_base: str = ""
    check_timeout: int = 600                               # 자동 테스트(checks) 타임아웃
    max_iterations: int = 3                                # 수렴 루프 최대 재시도(통과까지)
    verbose: bool = True                                   # 진행 로그 실시간 출력
    deploy_mode: str = "off"                               # off | dry_run | live (live=이중 인터록)
    deploy_env: str = "dev"
    worklogs_dir: str = "worklogs"

    @property
    def gitlab_token(self) -> str:
        return os.environ.get("XGEN_MAKER_GITLAB_TOKEN", "")

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


def infra_files(paths: list[str]) -> list[str]:
    return [p for p in paths if INFRA_PATTERNS.search(p.replace("\\", "/"))]
