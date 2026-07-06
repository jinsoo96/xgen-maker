""".env 로더 — 의존성 0. maker 실행 시 .env를 자동 로드해 자격을 환경변수로 주입.

탐색 순서: XGEN_MAKER_ENV(명시) → cwd/.env → 프로젝트 루트(.env). 이미 설정된 환경변수는 덮지 않음
(env override 우선). KEY=VALUE, 따옴표·주석(#)·export 접두 허용.

읽는 키: XGEN_MAKER_GITLAB_TOKEN, ANTHROPIC_API_KEY, XGEN_MAKER_LLM_KEY,
        XGEN_MAKER_LLM_BASE, XGEN_MAKER_LLM_MODEL, XGEN_MAKER_DEPLOY_LIVE 등.
"""
from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 인라인 주석 제거(따옴표 밖일 때만)
        if value and value[0] not in ("'", '"') and " #" in value:
            value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def find_env() -> Path | None:
    explicit = os.environ.get("XGEN_MAKER_ENV")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    candidates = [Path.cwd() / ".env",
                  Path(__file__).resolve().parent.parent / ".env"]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_env(path: str | Path | None = None, override: bool = False) -> dict:
    """반환 {loaded, path, keys}. 이미 있는 env는 기본 보존(override=False)."""
    global _LOADED
    env_path = Path(path) if path else find_env()
    if env_path is None or not env_path.is_file():
        return {"loaded": False, "path": None, "keys": []}
    try:
        values = _parse(env_path.read_text(encoding="utf-8"))
    except OSError:
        return {"loaded": False, "path": str(env_path), "keys": []}
    applied = []
    for key, value in values.items():
        if override or not os.environ.get(key):
            os.environ[key] = value
            applied.append(key)
    _LOADED = True
    return {"loaded": True, "path": str(env_path), "keys": applied}


def ensure_loaded() -> None:
    """한 번만 로드(멱등) — CLI 진입점에서 호출."""
    global _LOADED
    if not _LOADED:
        load_env()
