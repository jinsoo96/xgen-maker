"""인증/프로바이더 — "Claude 로그인 하나로 전부" (openxgen provider 셋업 참고).

MAKER의 LLM(판단·요약·intent)과 코딩 에이전트를 하나의 provider로 통합한다.
provider 3종:
- claude_cli (권장·기본): claude CLI 구독 로그인 그대로. **API 키 불필요.**
  LLM = `claude -p`, 코딩 에이전트 = claude CLI. 로그인 하나로 코딩+판단+요약 전부.
- anthropic: ANTHROPIC API 키. LLM = Messages API, 코딩 에이전트 = claude CLI.
- vllm: OpenAI-호환 엔드포인트(무료 H200 등). LLM만, 코딩은 claude CLI.

저장: ~/.xgen-maker/auth.json (키는 이 파일에만, 레포에는 안 올림).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

AUTH_DIR = Path.home() / ".xgen-maker"
AUTH_FILE = AUTH_DIR / "auth.json"

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_VLLM_BASE = "http://127.0.0.1:10051/v1"
DEFAULT_VLLM_MODEL = "Qwen3.6-27B"


def claude_command(args: list[str]) -> list[str] | None:
    """claude CLI 호출 명령 생성. Windows .cmd/.ps1 심은 cmd /c 경유(CreateProcess 직접실행 불가)."""
    exe = shutil.which("claude")
    if not exe:
        return None
    if exe.lower().endswith((".cmd", ".bat", ".ps1")):
        base = exe[:-4] + ".cmd" if exe.lower().endswith(".ps1") else exe
        return ["cmd", "/c", base, *args]
    return [exe, *args]


@dataclass
class Auth:
    provider: str = "claude_cli"           # claude_cli | anthropic | vllm
    model: str = ""                        # 비면 provider 기본값
    api_key: str = ""                      # anthropic/vllm 용 (claude_cli는 불필요)
    base: str = ""                         # vllm 용
    gitlab_url: str = "https://gitlab.example.com"
    gitlab_user: str = ""                  # 표시용(이메일/username)
    gitlab_token: str = ""                 # push·MR 지속 인증 (한 번 저장하면 재입력 불필요)

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        return {"claude_cli": "cli", "anthropic": DEFAULT_ANTHROPIC_MODEL,
                "vllm": DEFAULT_VLLM_MODEL}.get(self.provider, "cli")

    def resolved_base(self) -> str:
        if self.provider == "claude_cli":
            return "claude_cli"
        if self.provider == "anthropic":
            return "anthropic"
        return self.base or DEFAULT_VLLM_BASE


def claude_cli_status() -> dict:
    """claude CLI 존재 + 인증 여부(단발 완성 성공으로 판정)."""
    command = claude_command(["-p", "reply with: ok"])
    if command is None:
        return {"available": False, "authenticated": False, "reason": "claude CLI 미설치"}
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=90)
    except (subprocess.TimeoutExpired, OSError) as error:
        return {"available": True, "authenticated": False, "reason": str(error)}
    ok = result.returncode == 0 and bool((result.stdout or "").strip())
    return {"available": True, "authenticated": ok,
            "reason": "" if ok else (result.stderr or "")[-200:]}


def load_auth() -> Auth:
    """저장된 auth. 없으면 claude_cli 기본. 환경변수가 있으면 그걸 우선 반영."""
    data = {}
    if AUTH_FILE.exists():
        try:
            data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    auth = Auth(**{k: v for k, v in data.items() if k in Auth.__dataclass_fields__})
    # 환경변수 오버라이드 (CI/일회성)
    if os.environ.get("ANTHROPIC_API_KEY") and auth.provider == "claude_cli" and not AUTH_FILE.exists():
        pass  # claude_cli가 이미 있으면 유지 — 키가 있어도 로그인 우선
    return auth


def save_auth(auth: Auth) -> Path:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(asdict(auth), ensure_ascii=False, indent=1),
                         encoding="utf-8")
    try:
        os.chmod(AUTH_FILE, 0o600)  # 키 파일 권한 축소(가능한 OS에서)
    except OSError:
        pass
    return AUTH_FILE


def apply_to_env(auth: Auth) -> None:
    """LLM 클라이언트가 읽는 환경변수로 반영(프로세스 한정)."""
    if auth.provider == "anthropic" and auth.api_key:
        os.environ["ANTHROPIC_API_KEY"] = auth.api_key
    elif auth.provider == "vllm" and auth.api_key:
        os.environ["XGEN_MAKER_LLM_KEY"] = auth.api_key
    if auth.gitlab_token and not os.environ.get("XGEN_MAKER_GITLAB_TOKEN"):
        os.environ["XGEN_MAKER_GITLAB_TOKEN"] = auth.gitlab_token


# ---- GitLab 로그인 ----

def gitlab_verify_token(url: str, token: str, timeout: int = 20) -> dict:
    """토큰 유효성 — GET /user. 반환 {ok, user, id} 또는 {ok: False, reason}."""
    import json
    import urllib.request
    import urllib.error
    request = urllib.request.Request(url.rstrip("/") + "/api/v4/user",
                                     headers={"PRIVATE-TOKEN": token})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {"ok": True, "user": data.get("username"), "id": data.get("id")}
    except urllib.error.HTTPError as error:
        return {"ok": False, "reason": f"HTTP {error.code}"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return {"ok": False, "reason": str(error)[:80]}


def gitlab_login_password(url: str, user: str, password: str, timeout: int = 25) -> dict:
    """이메일/비번 → OAuth ROPC 토큰. 2FA/정책이면 실패(→토큰 안내). 반환 {ok, token|reason}."""
    import json
    import urllib.request
    import urllib.error
    body = json.dumps({"grant_type": "password", "username": user,
                       "password": password}).encode("utf-8")
    request = urllib.request.Request(url.rstrip("/") + "/oauth/token", data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {"ok": True, "token": data.get("access_token", "")}
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error", "")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "reason": f"HTTP {error.code} {detail} "
                "(2FA·비번그랜트 비활성이면 PAT 사용)"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return {"ok": False, "reason": str(error)[:80]}


def resolve_gitlab_token() -> str:
    """env 우선, 없으면 저장된 로그인."""
    return os.environ.get("XGEN_MAKER_GITLAB_TOKEN", "") or load_auth().gitlab_token
