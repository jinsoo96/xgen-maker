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
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

AUTH_DIR = Path.home() / ".xgen-maker"
AUTH_FILE = AUTH_DIR / "auth.json"

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
# 실 엔드포인트는 .env(XGEN_MAKER_LLM_BASE/MODEL)로만 — 공개 시 노출 방지
DEFAULT_VLLM_BASE = os.environ.get("XGEN_MAKER_LLM_BASE", "http://localhost:8000/v1")
DEFAULT_VLLM_MODEL = os.environ.get("XGEN_MAKER_LLM_MODEL", "local-model")


def resolve_shim(shim: str | Path) -> Path | None:
    """npm .cmd 심이 실제로 부르는 실행 파일. 없으면 None.

    cmd를 거치는 순간 인자가 셸 문법으로 해석된다. 줄바꿈에서 잘리고, `|`는 파이프가
    되고, `&`·`>`도 마찬가지다. 에러가 나면 차라리 낫다 — 실제로는 그럴듯한 실패로
    조용히 끝난다(실측: 의도 분류 프롬프트의 "bug|feature|refactor|question" 때문에
    cmd가 'feature'를 명령으로 실행하려 해 종료코드 255. 그 LLM 보정은 줄곧 죽어
    있었고, 애매한 변경 요청이 전부 '질문'으로 빠져 답만 하고 끝났다).

    심이 부르는 실제 exe를 직접 실행하면 셸이 개입하지 않는다. 심 내용에서 경로를
    읽어 낸다 — 규약을 가정해 경로를 지어내면 npm 레이아웃이 바뀔 때 조용히 깨진다.
    """
    shim = Path(shim)
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for match in re.finditer(r'"%dp0%\\?([^"]+\.exe)"', text, re.I):
        target = shim.parent / match.group(1)
        if target.is_file():
            return target
    return None


def claude_command(args: list[str]) -> list[str] | None:
    """claude CLI 호출 명령 생성. 심이면 실제 exe를 찾아 셸을 건너뛴다."""
    exe = shutil.which("claude")
    if not exe:
        return None
    if exe.lower().endswith((".cmd", ".bat", ".ps1")):
        base = exe[:-4] + ".cmd" if exe.lower().endswith(".ps1") else exe
        real = resolve_shim(base)
        if real is not None:
            return [str(real), *args]
        return ["cmd", "/c", base, *args]
    return [exe, *args]


@dataclass
class Auth:
    provider: str = "claude_cli"           # claude_cli | anthropic | vllm
    model: str = ""                        # 비면 provider 기본값
    api_key: str = ""                      # anthropic/vllm 용 (claude_cli는 불필요)
    base: str = ""                         # vllm 용
    gitlab_url: str = os.environ.get("XGEN_MAKER_GITLAB_URL", "https://gitlab.example.com")
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
    # 환경변수가 있으면 그것이 정본이다. 이걸 안 보면 저장소가 둘로 갈린다 —
    # config는 env를 먼저 보는데 load_auth는 파일만 봐서, save_auth가 파일의 옛 값으로
    # .env를 덮어써 방금 넣은 토큰이 조용히 되돌아간다(실측: 실제로 되돌아갔다).
    for field, key in (("gitlab_token", "XGEN_MAKER_GITLAB_TOKEN"),
                       ("gitlab_url", "XGEN_MAKER_GITLAB_URL")):
        value = os.environ.get(key, "")
        if value:
            setattr(auth, field, value)
    # 환경변수 오버라이드 (CI/일회성)
    if os.environ.get("ANTHROPIC_API_KEY") and auth.provider == "claude_cli" and not AUTH_FILE.exists():
        pass  # claude_cli가 이미 있으면 유지 — 키가 있어도 로그인 우선
    return auth


def save_auth(auth: Auth, write_env_file: bool = True) -> Path:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(asdict(auth), ensure_ascii=False, indent=1),
                         encoding="utf-8")
    try:
        os.chmod(AUTH_FILE, 0o600)  # 키 파일 권한 축소(가능한 OS에서)
    except OSError:
        pass
    # 자격을 .env에도 자동 반영 — 다음 실행부터 재입력이 필요 없다(요구사항).
    # 값이 있는 것만 쓴다(빈 값은 write_env가 해당 키를 지운다).
    if write_env_file:
        try:
            from .dotenv import write_env
            write_env({
                "XGEN_MAKER_GITLAB_URL": auth.gitlab_url,
                "XGEN_MAKER_GITLAB_TOKEN": auth.gitlab_token,
                "ANTHROPIC_API_KEY": auth.api_key if auth.provider == "anthropic" else "",
                "XGEN_MAKER_LLM_KEY": auth.api_key if auth.provider == "vllm" else "",
                "XGEN_MAKER_LLM_BASE": auth.base if auth.provider == "vllm" else "",
            })
        except Exception:  # noqa: BLE001 — .env 쓰기 실패가 로그인 자체를 막지 않게
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
