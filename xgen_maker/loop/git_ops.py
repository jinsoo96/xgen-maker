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
# URL 밖에 있는 토큰도 가린다. 자격이 늘 URL 형태로만 새지는 않는다 —
# 헤더 문자열("PRIVATE-TOKEN: glpat-…"), 설정 덤프, 에러 본문에도 섞인다.
# 발급처가 정한 접두사로 알아본다(값의 모양을 추측하지 않는다).
_BARE_TOKEN = re.compile(r"\b(glpat-|gldt-|ghp_|gho_|ghu_|ghs_|github_pat_|sk-|xoxb-)[A-Za-z0-9_-]{8,}")


def redact(text: str) -> str:
    """문자열 속 비밀값을 ***로 치환 — 인증 URL과, URL 밖의 토큰 모양 둘 다."""
    s = _CRED_URL_UP.sub(lambda m: f"{m['scheme']}{m['user']}:***@", str(text))
    s = _CRED_URL_U.sub(lambda m: f"{m['scheme']}***@", s)
    return _BARE_TOKEN.sub(lambda m: f"{m.group(1)}***", s)


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

    def resolve_target_branch(self, preferred: str) -> str:
        """이 저장소에서 실제로 쓸 통합 브랜치.

        저장소마다 기본 브랜치가 다르다(develop 쓰는 곳, main 쓰는 곳). 설정 하나를
        모든 저장소에 강요하면, 그 브랜치가 없는 저장소에서 최신 받기가 실패하고
        MR 초안이 존재하지도 않는 브랜치를 대상으로 적는다 — 사람이 그대로 MR을
        올리면 대상이 틀린다. 지식그래프가 origin/HEAD로 폴백하는 것과 같은 규칙.
        """
        if preferred and self._run("rev-parse", "--verify", "--quiet",
                                   f"origin/{preferred}^{{commit}}",
                                   check=False).strip():
            return preferred
        head = self._run("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD",
                         check=False).strip()
        if head:
            return head.rsplit("/", 1)[-1]
        return preferred

    def create_branch(self, name: str, base_ref: str = "") -> str:
        issue = branch_name_issue(name)
        if issue:
            raise GitOpsError(f"브랜치명 '{name}' 규칙 위반 — {issue}")
        if base_ref:
            self._run("checkout", "-b", name, base_ref)  # 최신 base에서 분기
        else:
            self._run("checkout", "-b", name)
        return name

    def token_host_matches(self, token_host: str, remote: str = "origin") -> bool:
        """이 저장소의 원격이 그 토큰이 속한 호스트인가.

        확인 없이 토큰을 붙이면, 다른 호스트에 있는 저장소로 **시크릿이 전송된다**
        (실측: GitLab 토큰이 github.com 원격으로 나갔고 인증도 당연히 실패했다).
        인증 실패는 눈에 띄지만 토큰이 남의 서버에 남는 건 안 보인다.
        """
        if not token_host:
            return False
        url = self._run("remote", "get-url", remote, check=False).strip()
        if not url:
            return False
        host = url.split("://", 1)[-1].split("@")[-1].split("/", 1)[0].lower()
        want = token_host.split("://", 1)[-1].split("/", 1)[0].lower()
        return bool(host) and bool(want) and host == want

    def fetch(self, branch: str, remote: str = "origin", token: str = "",
              user: str = "oauth2", token_host: str = "") -> str:
        """origin/<branch> 최신 가져오기. 반환 origin/<branch> SHA.

        토큰은 그 토큰이 속한 호스트의 원격일 때만 쓴다. 아니면 원격에 이미 붙어 있는
        자격(또는 credential helper)으로 그냥 받는다 — 대개 그쪽이 맞는 자격이다.
        """
        args = ["fetch", "--quiet", remote, branch]
        if token and self.token_host_matches(token_host, remote):
            remote_url = self._run("remote", "get-url", remote).strip()
            if remote_url.startswith("https://"):
                host_path = remote_url.split("://", 1)[1].split("@")[-1]
                args = ["-c", "credential.helper=", "fetch", "--quiet",
                        f"https://{user}:{token}@{host_path}", branch]
        self._run(*args)
        return self._run("rev-parse", "FETCH_HEAD").strip()

    def _run_paths(self, *args: str) -> list[str]:
        r"""경로 목록을 NUL 구분으로 받는다.

        git은 ASCII 밖 경로를 C 스타일로 감싼다("\354\240\225...").
        줄 단위로 읽으면 그게 그대로 경로가 되어, stage_all이 실재하지 않는 파일을
        add 하려 든다 — 에이전트가 고친 한글 이름 파일이 커밋에서 통째로 빠진다.
        -z를 주면 git이 감싸지 않는다.
        """
        return [field for field in self._run(*args, "-z").split(chr(0)) if field]

    def diff_names(self, ref_a: str, ref_b: str = "") -> list[str]:
        return self._run_paths("diff", "--name-only", ref_a,
                               *([ref_b] if ref_b else []))

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

    # 검증(테스트 실행)이 스스로 만들어 내는 부산물. 요청과 무관하고, MR에 들어가면
    # 리뷰어가 먼저 보는 것이 바이트코드가 된다. 레포에 .gitignore가 있으면 git이
    # 알아서 걸러 주지만, 없는 레포에서는 우리가 만든 쓰레기를 우리가 커밋하게 된다.
    _OWN_BUILD_ARTIFACTS = ("__pycache__/", ".pytest_cache/")

    @classmethod
    def _is_own_artifact(cls, rel: str) -> bool:
        path = rel.replace("\\", "/")
        return (path.endswith((".pyc", ".pyo"))
                or any(part in path for part in cls._OWN_BUILD_ARTIFACTS))

    def changed_files(self, base: str = "HEAD") -> list[str]:
        tracked = self._run_paths("diff", "--name-only", base)
        untracked = self._run_paths("ls-files", "--others", "--exclude-standard")
        return sorted({f for f in tracked + untracked if not self._is_own_artifact(f)})

    def diff(self, base: str = "HEAD") -> str:
        return self._run("diff", base)

    def stage_all(self) -> None:
        """변경으로 인정한 파일만 스테이징한다.

        `add -A`로 워킹트리를 통째로 담으면, 요청과 무관하게 굴러다니던 파일과
        검증이 만들어 낸 부산물까지 MR에 들어간다 — 화면에는 "N개 파일 수정"이라
        떠 있는데 실제 커밋은 그보다 많아진다(실측: 바이트코드 2개가 딸려 들어갔다).
        """
        paths = self.changed_files()
        if not paths:
            return
        self._run("add", "--", *paths)

    def staged_files(self, base: str = "HEAD") -> list[str]:
        return sorted(set(self._run_paths("diff", "--cached", "--name-only", base)))

    def staged_diff(self, base: str = "HEAD") -> str:
        return self._run("diff", "--cached", base)

    def commit_all(self, title: str, body: str,
                   author_name: str = "", author_email: str = "") -> str:
        self.stage_all()          # 인정한 변경만 — 워킹트리를 통째로 쓸어담지 않는다
        message = f"{title}\n\n{body}" if body else title
        # 저자 지정 시 대상 레포 git config와 무관하게 강제(-c 로 저자·커미터 동시 고정)
        ident: list[str] = []
        if author_name and author_email:
            ident = ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}"]
        self._run(*ident, "commit", "-m", message)
        return self._run("rev-parse", "HEAD").strip()

    def push(self, branch: str, remote: str = "origin",
             token: str = "", user: str = "oauth2", token_host: str = "") -> None:
        if is_protected_branch(branch):
            raise GitOpsError(f"보호 브랜치 '{branch}' 푸시는 설계상 불가")
        if not is_allowed_branch(branch):
            raise GitOpsError(f"허용 prefix 밖 브랜치 '{branch}' 푸시 거부")
        current = self.current_branch()
        if current != branch:
            raise GitOpsError(f"현재 브랜치({current})와 푸시 대상({branch}) 불일치")
        # 토큰은 그 토큰이 속한 호스트에서만 쓴다 — 아니면 남의 서버로 시크릿이 나간다
        if token and self.token_host_matches(token_host, remote):
            # 저장된 로그인으로 인증 URL 구성 — remote 자격 미설정이어도 push 성공
            remote_url = self._run("remote", "get-url", remote).strip()
            if remote_url.startswith("https://"):
                host_path = remote_url.split("://", 1)[1].split("@")[-1]
                auth_url = f"https://{user}:{token}@{host_path}"
                self._run("-c", "credential.helper=", "push", "-u", auth_url,
                          f"{branch}:{branch}")
                return
        self._run("push", "-u", remote, branch)
