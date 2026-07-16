"""⑤ git 조작 — MR-only 안전 가드가 코드로 강제되는 계층.

불변 규칙: 보호 브랜치(develop/main/...)로는 checkout -b 대상도, push 대상도 될 수 없다.
브랜치는 fix/·feature/·refactor/·chore/ prefix만 허용.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..config import is_allowed_branch, is_protected_branch, branch_name_issue

# 인증 URL의 자격을 마스킹(에러/로그/저널로 새는 것 방지):
#  https://user:TOKEN@host  그리고  https://TOKEN@host (토큰=userinfo, GitLab PAT 형식)
_CRED_URL_UP = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<secret>[^@\s]+)@")
_CRED_URL_U = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<secret>[^:/@\s]+)@")


def redact(text: str) -> str:
    """문자열 속 인증 URL의 비밀값을 ***로 치환(user:token@ · token@ 둘 다)."""
    s = _CRED_URL_UP.sub(lambda m: f"{m['scheme']}{m['user']}:***@", str(text))
    return _CRED_URL_U.sub(lambda m: f"{m['scheme']}***@", s)


class GitOpsError(RuntimeError):
    pass


class GitRepo:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not (self.path / ".git").exists():
            raise GitOpsError(f"git 저장소가 아님: {self.path}")

    def _run(self, *args: str, check: bool = True, timeout: int = 300) -> str:
        try:
            result = subprocess.run(
                ["git", *args], cwd=self.path, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
                # 자격 프롬프트로 무기한 blocking되지 않게(웹 데몬 스레드 보호)
                stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise GitOpsError(redact(f"git {' '.join(args)} 타임아웃({timeout}s)")) from None
        if check and result.returncode != 0:
            # args·stderr 모두 마스킹 — 토큰이 저널/SUMMARY/report로 새지 않게
            raise GitOpsError(redact(f"git {' '.join(args)} 실패: {result.stderr.strip()}"))
        return result.stdout

    def current_branch(self) -> str:
        return self._run("rev-parse", "--abbrev-ref", "HEAD").strip()

    def is_clean(self) -> bool:
        return not self._run("status", "--porcelain").strip()

    def create_branch(self, name: str, base_ref: str = "") -> str:
        issue = branch_name_issue(name)
        if issue:
            raise GitOpsError(f"브랜치명 '{name}' 규칙 위반 — {issue}")
        if base_ref:
            self._run("checkout", "-b", name, base_ref)  # 최신 base에서 분기
        else:
            self._run("checkout", "-b", name)
        return name

    def fetch(self, branch: str, remote: str = "origin", token: str = "",
              user: str = "oauth2") -> str:
        """origin/<branch> 최신 가져오기. 토큰 있으면 인증 URL 사용. 반환 origin/<branch> SHA."""
        args = ["fetch", "--quiet", remote, branch]
        if token:
            remote_url = self._run("remote", "get-url", remote).strip()
            if remote_url.startswith("https://"):
                host_path = remote_url.split("://", 1)[1].split("@")[-1]
                args = ["-c", "credential.helper=", "fetch", "--quiet",
                        f"https://{user}:{token}@{host_path}", branch]
        self._run(*args)
        return self._run("rev-parse", "FETCH_HEAD").strip()

    def diff_names(self, ref_a: str, ref_b: str = "") -> list[str]:
        out = self._run("diff", "--name-only", ref_a, *( [ref_b] if ref_b else [] ))
        return [f.strip() for f in out.splitlines() if f.strip()]

    def add_worktree(self, path: str | Path, branch: str, base_ref: str) -> "GitRepo":
        """격리 worktree 생성(동시실행 충돌 방지) — path에 base_ref로부터 branch 체크아웃."""
        issue = branch_name_issue(branch)
        if issue:
            raise GitOpsError(f"브랜치명 '{branch}' 규칙 위반 — {issue}")
        self._run("worktree", "add", "-b", branch, str(path), base_ref or "HEAD")
        return GitRepo(path)

    def remove_worktree(self, path: str | Path) -> None:
        self._run("worktree", "remove", "--force", str(path))

    def checkout(self, name: str) -> None:
        self._run("checkout", name)

    def changed_files(self, base: str = "HEAD") -> list[str]:
        tracked = self._run("diff", "--name-only", base).splitlines()
        untracked = self._run("ls-files", "--others", "--exclude-standard").splitlines()
        return sorted({f.strip() for f in tracked + untracked if f.strip()})

    def diff(self, base: str = "HEAD") -> str:
        return self._run("diff", base)

    def stage_all(self) -> None:
        self._run("add", "-A")

    def staged_files(self, base: str = "HEAD") -> list[str]:
        lines = self._run("diff", "--cached", "--name-only", base).splitlines()
        return sorted({f.strip() for f in lines if f.strip()})

    def staged_diff(self, base: str = "HEAD") -> str:
        return self._run("diff", "--cached", base)

    def commit_all(self, title: str, body: str,
                   author_name: str = "", author_email: str = "") -> str:
        self._run("add", "-A")
        message = f"{title}\n\n{body}" if body else title
        # 저자 지정 시 대상 레포 git config와 무관하게 강제(-c 로 저자·커미터 동시 고정)
        ident: list[str] = []
        if author_name and author_email:
            ident = ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}"]
        self._run(*ident, "commit", "-m", message)
        return self._run("rev-parse", "HEAD").strip()

    def push(self, branch: str, remote: str = "origin",
             token: str = "", user: str = "oauth2") -> None:
        if is_protected_branch(branch):
            raise GitOpsError(f"보호 브랜치 '{branch}' 푸시는 설계상 불가")
        if not is_allowed_branch(branch):
            raise GitOpsError(f"허용 prefix 밖 브랜치 '{branch}' 푸시 거부")
        current = self.current_branch()
        if current != branch:
            raise GitOpsError(f"현재 브랜치({current})와 푸시 대상({branch}) 불일치")
        if token:
            # 저장된 로그인으로 인증 URL 구성 — remote 자격 미설정이어도 push 성공
            remote_url = self._run("remote", "get-url", remote).strip()
            if remote_url.startswith("https://"):
                host_path = remote_url.split("://", 1)[1].split("@")[-1]
                auth_url = f"https://{user}:{token}@{host_path}"
                self._run("-c", "credential.helper=", "push", "-u", auth_url,
                          f"{branch}:{branch}")
                return
        self._run("push", "-u", remote, branch)
