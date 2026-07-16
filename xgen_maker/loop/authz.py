"""인가 게이트 — 인가된 xgen 작업자만 실제 작업(act)이 되도록 강제.

public 저장소이므로 코드는 누구나 받지만, 실제 인프라 작업(push·MR)은:
  1) 유효한 GitLab 토큰
  2) 대상 프로젝트에 대한 Developer+ 멤버십
  3) placeholder가 아닌 실제 대상(gitlab_url·projects 매핑)
을 모두 요구한다. 자격/엔드포인트는 .env로만 주입되고(레포에 없음), 멤버십은
실 GitLab이 권위를 가진다. 게이트는 작업 시작 전 fail-fast로 미인가를 차단한다.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

DEVELOPER = 30  # GitLab access_level: 30=Developer, 40=Maintainer, 50=Owner
# 미설정/예시 대상 감지 — 실 인프라가 아니면 act 거부
_PLACEHOLDER = ("example.com", "your-company", "gitlab.example", "localhost", "")


def _api(url: str, path: str, token: str, timeout: int = 20):
    request = urllib.request.Request(url.rstrip("/") + "/api/v4" + path,
                                     headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def is_placeholder_target(gitlab_url: str) -> bool:
    u = (gitlab_url or "").strip().lower()
    return (not u) or any(p and p in u for p in _PLACEHOLDER)


def _origin_url(repo_path) -> str:
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(repo_path),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except (OSError, ValueError):
        return ""


def origin_project_path(origin: str) -> str:
    """origin URL에서 'group/repo' 경로만 정규화 추출.

    https://user:tok@host/grp/repo.git · git@host:grp/repo.git · ssh://host/grp/repo 모두 지원.
    부분문자열 비교는 fork('grp/repo-fork')를 통과시키므로 정확 비교용 경로를 뽑는다.
    """
    o = (origin or "").strip().rstrip("/")
    if o.lower().endswith(".git"):
        o = o[:-4]
    if "://" in o:                       # scheme://[creds@]host/path
        o = o.split("://", 1)[1]
        o = o.split("@")[-1]             # 자격 제거
        o = o.split("/", 1)[1] if "/" in o else ""
    elif ":" in o:                       # scp 형식 git@host:grp/repo
        o = o.split(":", 1)[1]
    return o.strip("/").lower()


def authorize(config, repo: str, min_level: int = DEVELOPER,
              timeout: int = 20, repo_path=None) -> dict:
    """act(실 push/MR) 전 인가 확인. 반환 {ok, user?, level?, project?, reason?}.

    거부 사유: 토큰 없음 · placeholder 대상 · 프로젝트 매핑 없음 ·
              토큰 무효 · 멤버 아님 · 접근레벨 부족 · 로컬 origin이 인가 프로젝트와 불일치.
    """
    token = config.gitlab_token
    if not token:
        return {"ok": False, "reason":
                "GitLab 토큰 없음 — 인가된 xgen 작업자만 act 가능(maker login)"}
    url = config.gitlab_url
    if is_placeholder_target(url):
        return {"ok": False, "reason":
                f"gitlab_url이 미설정/예시({url!r}) — 실 대상 아님, act 거부"}
    project = (config.gitlab_projects or {}).get(repo)
    if not project:
        return {"ok": False, "reason":
                f"'{repo}' → gitlab_projects 매핑 없음 — 대상 프로젝트 미지정"}
    # 로컬 클론의 origin이 인가 대상 프로젝트를 실제로 가리키는지(다른 레포·fork로 push 방지)
    # 숫자 project id는 URL에 안 나타나므로 경로 비교를 건너뛴다(GitLab 권한이 최종 방어).
    if repo_path is not None and not str(project).isdigit():
        origin = _origin_url(repo_path)
        norm = str(project).strip("/").lower()
        if origin and origin_project_path(origin) != norm:
            return {"ok": False, "project": project, "reason":
                    f"로컬 origin({origin[:60]})이 인가 프로젝트 '{project}'와 불일치 — push 대상 오염 의심"}

    # 1) 토큰 유효성 + 사용자 식별
    try:
        me = _api(url, "/user", token, timeout)
    except urllib.error.HTTPError as error:
        return {"ok": False, "reason": f"토큰 무효(HTTP {error.code})"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return {"ok": False, "reason": f"GitLab 접속 실패: {str(error)[:60]}"}
    uid, uname = me.get("id"), me.get("username")

    # 2) 대상 프로젝트 멤버십(그룹 상속 포함) + 접근레벨
    enc = urllib.parse.quote(str(project), safe="")
    try:
        member = _api(url, f"/projects/{enc}/members/all/{uid}", token, timeout)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"ok": False, "user": uname, "project": project, "reason":
                    f"'{uname}'는 {project} 멤버 아님 — 인가된 작업자만 act 가능"}
        return {"ok": False, "user": uname, "project": project,
                "reason": f"멤버십 확인 실패(HTTP {error.code})"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return {"ok": False, "user": uname, "project": project,
                "reason": f"멤버십 확인 실패: {str(error)[:60]}"}

    level = int(member.get("access_level", 0))
    if level < min_level:
        return {"ok": False, "user": uname, "project": project, "level": level,
                "reason": f"'{uname}' 접근레벨 {level} < Developer({min_level}) — 쓰기 권한 없음"}
    return {"ok": True, "user": uname, "project": project, "level": level}
