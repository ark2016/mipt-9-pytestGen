"""The PR-comment helper of the GitHub Action (scripts/pr_comment.py)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pr_comment.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pr_comment", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["pr_comment"] = module
    spec.loader.exec_module(module)
    return module


pc = _load()


# --------------------------------------------------------------------------- selection


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("src/**/*.py", "src/a.py", True),
        ("src/**/*.py", "src/pkg/sub/a.py", True),
        ("src/**/*.py", "srcx/a.py", False),
        ("src/**/*.py", "lib/src/a.py", False),
        ("src/*.py", "src/pkg/a.py", False),
        ("src/*.py", "src/a.py", True),
        ("**/*.py", "a.py", True),
        ("**/*.py", "x/y/a.py", True),
        ("app/?.py", "app/a.py", True),
        ("app/?.py", "app/ab.py", False),
        ("pkg/**", "pkg/deep/er/a.py", True),
        ("a+b/*.py", "a+b/c.py", True),
    ],
)
def test_glob_to_regex(pattern: str, path: str, expected: bool) -> None:
    assert bool(pc.glob_to_regex(pattern).fullmatch(path)) is expected


def test_split_patterns_accepts_newlines_and_commas() -> None:
    assert pc.split_patterns("src/**/*.py,\n lib/*.py \n\n") == ["src/**/*.py", "lib/*.py"]
    assert pc.split_patterns("  ") == []


@pytest.mark.parametrize(
    ("path", "excluded"),
    [
        ("src/pkg/__init__.py", True),
        ("src/pkg/conftest.py", True),
        ("src/tests/helpers.py", True),
        ("src/pkg/test/util.py", True),
        ("src/pkg/test_mod.py", True),
        ("src/pkg/mod_test.py", True),
        ("src/pkg/mod.py", False),
        ("src/pkg/contest.py", False),
        ("src/testing_utils.py", False),
    ],
)
def test_is_excluded(path: str, excluded: bool) -> None:
    assert pc.is_excluded(path) is excluded


def test_filter_files_applies_glob_exclusions_and_cap() -> None:
    changed = [
        "src/b.py",
        "src/a.py",
        "src/a.py",  # duplicates collapse
        "src/pkg/__init__.py",
        "src/pkg/tests/test_x.py",
        "src/readme.md",
        "docs/conf.py",
        "./src/c.py",
        "src\\d.py",
        "src/e.py",
    ]
    selected, over = pc.filter_files(changed, ["src/**/*.py"], 3)
    assert selected == ["src/a.py", "src/b.py", "src/c.py"]
    assert over == ["src/d.py", "src/e.py"]


def test_filter_files_without_matches_or_with_zero_cap() -> None:
    assert pc.filter_files(["README.md"], ["src/**/*.py"], 5) == ([], [])
    assert pc.filter_files(["src/a.py"], ["src/**/*.py"], 0) == ([], ["src/a.py"])


def test_slug_is_flat_and_keeps_full_path() -> None:
    assert pc.slug_for("src/shop/pricing.py") == "src__shop__pricing"
    assert pc.slug_for("src/pytestgen/cli.py") == "src__pytestgen__cli"
    assert pc.slug_for("tool.py") == "tool"
    assert pc.slug_for("a/b_c.py") != pc.slug_for("a_b/c.py")
    assert pc.slug_for("src/pkg/x.py") != pc.slug_for("pkg/x.py")


# --------------------------------------------------------------------------- PR context


def _event(tmp_path: Path, pull_request: dict[str, Any] | None) -> str:
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": pull_request} if pull_request else {}))
    return str(path)


def _pr(head_repo: str | None, number: int = 7) -> dict[str, Any]:
    return {
        "number": number,
        "base": {"sha": "abc123", "repo": {"full_name": "me/proj"}},
        "head": {"repo": {"full_name": head_repo} if head_repo else None},
    }


def test_context_same_repo(tmp_path: Path) -> None:
    ctx = pc.load_pr_context(_event(tmp_path, _pr("me/proj")))
    assert (ctx.number, ctx.base_sha, ctx.is_fork) == (7, "abc123", False)


def test_context_fork_and_deleted_fork(tmp_path: Path) -> None:
    assert pc.load_pr_context(_event(tmp_path, _pr("other/proj"))).is_fork
    assert pc.load_pr_context(_event(tmp_path, _pr(None))).is_fork


def test_context_without_event(tmp_path: Path) -> None:
    for event_path in (None, str(tmp_path / "missing.json"), _event(tmp_path, None)):
        ctx = pc.load_pr_context(event_path)
        assert (ctx.number, ctx.base_sha, ctx.is_fork) == (None, "", False)


# --------------------------------------------------------------------------- git


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_changed_files_against_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }.items():
        monkeypatch.setenv(key, value)

    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)  # noqa: S607
        return done.stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src" / "old.py").write_text("x = 1\n")
    (repo / "src" / "gone.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (repo / "src" / "new.py").write_text("y = 1\n")
    (repo / "src" / "old.py").write_text("x = 2\n")
    (repo / "src" / "gone.py").unlink()
    git("add", "-A")
    git("commit", "-q", "-m", "change")
    assert sorted(pc.changed_files(base)) == ["src/new.py", "src/old.py"]


def test_changed_files_explains_a_shallow_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 128, b"", b"fatal: bad revision")

    monkeypatch.setattr(pc, "_git", failing)
    with pytest.raises(RuntimeError, match="fetch-depth: 0"):
        pc.changed_files("deadbeef")


def test_changed_files_falls_back_to_fetching_the_base(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(args))
        if "deadbeef...HEAD" in args:
            return subprocess.CompletedProcess(args, 128, b"", b"no merge base")
        return subprocess.CompletedProcess(args, 0, b"src/a.py\0src/b.py\0", b"")

    monkeypatch.setattr(pc, "_git", fake)
    assert pc.changed_files("deadbeef") == ["src/a.py", "src/b.py"]
    assert calls[1][0] == "fetch"
    assert calls[2][-2:] == ["deadbeef", "HEAD"]


# --------------------------------------------------------------------------- generation


def test_child_env_drops_empty_pytestgen_values() -> None:
    env = pc.child_env(
        {
            "PYTESTGEN_LLM_API_KEY": "",
            "PYTESTGEN_LLM_BASE_URL": "http://x/v1",
            "PYTESTGEN_LLM_MODEL": "  ",
            "PATH": "/bin",
        }
    )
    assert "PYTESTGEN_LLM_API_KEY" not in env
    assert "PYTESTGEN_LLM_MODEL" not in env
    assert env["PYTESTGEN_LLM_BASE_URL"] == "http://x/v1"
    assert env["PATH"] == "/bin"
    assert env["PYTESTGEN_LOG_FORMAT"] == "console"


def test_child_env_keeps_an_explicit_log_format() -> None:
    assert pc.child_env({"PYTESTGEN_LOG_FORMAT": "json"})["PYTESTGEN_LOG_FORMAT"] == "json"


def test_tail_text_strips_colours_and_bounds_size() -> None:
    text = "\n".join(f"\x1b[31mline {i}\x1b[0m" for i in range(100))
    tail = pc.tail_text(text)
    assert "\x1b" not in tail
    assert tail.splitlines()[-1] == "line 99"
    assert len(tail.splitlines()) == pc.ERROR_TAIL_LINES
    assert len(pc.tail_text("x" * 10_000)) == pc.ERROR_TAIL_CHARS


def _json_report(tmp_path: Path, **overrides: Any) -> Path:
    data: dict[str, Any] = {
        "module": "shop.pricing",
        "kept_tests": 9,
        "model": "qwen",
        "delta": {
            "before": {"line_percent": 40.0, "branch_percent": 20.0},
            "after": {"line_percent": 85.5, "branch_percent": 70.0},
        },
    }
    data.update(overrides)
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data))
    return path


def test_read_json_report(tmp_path: Path) -> None:
    got = pc.read_json_report(_json_report(tmp_path))
    assert got == {
        "line_before": 40.0,
        "line_after": 85.5,
        "branch_before": 20.0,
        "branch_after": 70.0,
        "kept_tests": 9,
        "model": "qwen",
    }


def test_read_json_report_tolerates_garbage(tmp_path: Path) -> None:
    assert pc.read_json_report(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert pc.read_json_report(bad) == {}
    assert pc.read_json_report(_json_report(tmp_path, delta={})) == {}


class FakeRun:
    """Stands in for ``subprocess.run`` when pytestgen is invoked."""

    def __init__(
        self, tmp_path: Path, *, returncode: int = 0, stderr: str = "", write: bool = True
    ) -> None:
        self.tmp_path = tmp_path
        self.returncode = returncode
        self.stderr = stderr
        self.write = write
        self.command: list[str] = []

    def __call__(self, command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.command = command
        if self.write:
            md = Path(command[command.index("--report-md") + 1])
            md.write_text("### pytestgen: shop.pricing\n\nbody\n")
            Path(command[command.index("--report-json") + 1]).write_text(
                _json_report(self.tmp_path).read_text()
            )
        return subprocess.CompletedProcess(command, self.returncode, "stdout line", self.stderr)


def _generate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: Callable[..., Any]) -> Any:
    monkeypatch.setattr(pc.subprocess, "run", fake)
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    return pc.generate_one(
        "src/shop/pricing.py",
        pytestgen_bin="pytestgen",
        out_dir=out_dir,
        strategy="hybrid",
        timeout_s=5,
        env={},
    )


def test_generate_one_builds_the_command_and_reads_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRun(tmp_path)
    result = _generate(tmp_path, monkeypatch, fake)
    assert result.status == "ok"
    assert (result.line_before, result.line_after, result.kept_tests) == (40.0, 85.5, 9)
    cmd = fake.command
    assert cmd[:3] == ["pytestgen", "generate", "src/shop/pricing.py"]
    assert cmd[cmd.index("--project-root") + 1] == "."
    assert cmd[cmd.index("--strategy") + 1] == "hybrid"
    assert "--no-existing-tests" not in cmd  # existing tests are the baseline
    assert (
        cmd[cmd.index("-o") + 1]
        .replace("\\", "/")
        .endswith("out/tests/test_src__shop__pricing_generated.py")
    )
    assert (tmp_path / "out" / "src__shop__pricing.log").is_file()


def test_generate_one_records_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(tmp_path, returncode=2, stderr="error: LLM server unreachable", write=False)
    result = _generate(tmp_path, monkeypatch, fake)
    assert result.status == "failed"
    assert result.returncode == 2
    assert "LLM server unreachable" in result.error


def test_generate_one_zero_exit_without_report_is_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _generate(tmp_path, monkeypatch, FakeRun(tmp_path, write=False))
    assert result.status == "failed"
    assert result.error


def test_generate_one_timeout_and_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(command: list[str], **_: Any) -> None:
        raise subprocess.TimeoutExpired(command, 5, output="partial output")

    result = _generate(tmp_path, monkeypatch, timeout)
    assert result.status == "timeout"
    assert "timed out after 5 s" in result.error

    def missing(command: list[str], **_: Any) -> None:
        raise FileNotFoundError("pytestgen")

    result = _generate(tmp_path, monkeypatch, missing)
    assert result.status == "failed"
    assert "cannot start pytestgen" in result.error


# --------------------------------------------------------------------------- comment body


def _ok(tmp_path: Path, name: str, text: str | None = None) -> Any:
    slug = pc.slug_for(name)
    (tmp_path / f"{slug}.md").write_text(text or f"### pytestgen: {slug}\n\nreport of {slug}\n")
    return pc.FileResult(
        file=name,
        slug=slug,
        status="ok",
        line_before=10.0,
        line_after=60.0,
        branch_before=0.0,
        branch_after=50.0,
        kept_tests=4,
        model="qwen",
    )


def test_build_comment_combines_reports_and_errors(tmp_path: Path) -> None:
    good = _ok(tmp_path, "src/a.py")
    bad = pc.FileResult(
        file="src/b.py", slug="b", status="failed", returncode=2, error="error: ``boom``"
    )
    body = pc.build_comment(
        [good, bad], tmp_path, skipped=["src/z.py"], run_url="https://x/runs/1", sha="0123456789"
    )
    assert body.startswith(pc.MARKER + "\n")
    assert body.count(pc.MARKER) == 1
    assert "2 file(s), 1 failed, model `qwen`" in body
    assert "| `src/a.py` | ok | 10.0% -> 60.0% (+50.0 pp) | 0.0% -> 50.0% (+50.0 pp) | 4 |" in body
    assert "| `src/b.py` | failed | n/a | n/a | - |" in body
    assert "report of src__a" in body
    assert "**Generation failed** (exit code 2)" in body
    assert "```text\nerror: ``boom``\n```" in body
    assert "Over the file limit, not processed: `src/z.py`" in body
    assert "[workflow run](https://x/runs/1)" in body
    assert "`0123456`" in body
    assert body.endswith("\n")


def test_build_comment_error_fence_outgrows_backticks(tmp_path: Path) -> None:
    bad = pc.FileResult(file="b.py", slug="b", status="failed", error="x ```` y")
    assert "`````text\nx ```` y\n`````" in pc.build_comment([bad], tmp_path)


def test_build_comment_timeout_wording(tmp_path: Path) -> None:
    slow = pc.FileResult(file="b.py", slug="b", status="timeout", error="timed out after 5 s")
    body = pc.build_comment([slow], tmp_path)
    assert "**Generation timed out**" in body
    assert "| `b.py` | timeout |" in body


def test_build_comment_without_files(tmp_path: Path) -> None:
    body = pc.build_comment([], tmp_path)
    assert body.startswith(pc.MARKER)
    assert "nothing to generate" in body
    assert "| File |" not in body


def test_build_comment_missing_report_file_is_reported_as_failure(tmp_path: Path) -> None:
    result = pc.FileResult(file="src/a.py", slug="a", status="ok")
    body = pc.build_comment([result], tmp_path)
    assert "**Generation failed**" in body


def test_build_comment_stays_under_the_size_limit(tmp_path: Path) -> None:
    huge = "### big\n\n" + "x" * 30_000
    results = [_ok(tmp_path, f"src/m{i}.py", huge) for i in range(4)]
    body = pc.build_comment(results, tmp_path)
    assert len(body) <= pc.MAX_COMMENT_CHARS
    assert body.startswith(pc.MARKER)
    assert "left out because of the comment size limit" in body
    assert body.count("### big") == 1  # one 30k section fits, the others are left out
    assert "m3.py" in body  # but every file stays in the summary table


def test_md_cell_escapes_pipes() -> None:
    assert pc.md_cell("a|b\nc") == r"a\|b c"


# --------------------------------------------------------------------------- sticky comment


def test_parse_json_stream_handles_pages_and_whitespace() -> None:
    text = '[{"id": 1}, {"id": 2}]\n[{"id": 3}]\n  [] \n{"id": 4}'
    assert [c["id"] for c in pc.parse_json_stream(text)] == [1, 2, 3, 4]
    assert pc.parse_json_stream("") == []
    assert pc.parse_json_stream("[1, 2]") == []


def test_find_marker_comment_picks_the_oldest() -> None:
    comments = [
        {"id": 5, "body": "unrelated"},
        {"id": 9, "body": f"{pc.MARKER}\nnew"},
        {"id": 7, "body": f"x {pc.MARKER}"},
        {"id": 3, "body": None},
        {"body": pc.MARKER},
    ]
    assert pc.find_marker_comment(comments) == 7
    assert pc.find_marker_comment([{"id": 1, "body": "hello"}]) is None
    assert pc.find_marker_comment([]) is None


class FakeGh:
    def __init__(self, existing: list[dict[str, Any]]) -> None:
        self.existing = existing
        self.calls: list[tuple[list[str], Mapping[str, Any] | None]] = []

    def __call__(
        self, args: Sequence[str], *, token: str, payload: Mapping[str, Any] | None = None
    ) -> str:
        assert token == "tok"
        self.calls.append((list(args), payload))
        return json.dumps(self.existing) if payload is None else "{}"


def test_upsert_creates_when_there_is_no_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh([{"id": 1, "body": "hi"}])
    monkeypatch.setattr(pc, "gh_api", gh)
    assert pc.upsert_comment("me/proj", 7, "BODY", "tok") == "created"
    args, payload = gh.calls[-1]
    assert args == ["--method", "POST", "repos/me/proj/issues/7/comments"]
    assert payload == {"body": "BODY"}


def test_upsert_updates_the_marker_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh([{"id": 11, "body": f"{pc.MARKER} old"}])
    monkeypatch.setattr(pc, "gh_api", gh)
    assert pc.upsert_comment("me/proj", 7, "BODY", "tok") == "updated"
    assert gh.calls[-1][0] == ["--method", "PATCH", "repos/me/proj/issues/comments/11"]
    assert len(gh.calls) == 2


def test_upsert_without_create_leaves_no_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh([])
    monkeypatch.setattr(pc, "gh_api", gh)
    assert pc.upsert_comment("me/proj", 7, "BODY", "tok", create=False) == "skipped"
    assert len(gh.calls) == 1


def test_gh_api_without_gh_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc.shutil, "which", lambda _: None)
    with pytest.raises(pc.GhError, match="not installed"):
        pc.gh_api(["x"], token="t")


# --------------------------------------------------------------------------- subcommands


@pytest.fixture
def pr_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], Path]:
    def setup(pull_request: dict[str, Any]) -> Path:
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("GITHUB_EVENT_PATH", _event(tmp_path, pull_request))
        monkeypatch.setenv("GITHUB_REPOSITORY", "me/proj")
        monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "gh_output"))
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "gh_summary"))
        monkeypatch.setenv("GITHUB_RUN_ID", "42")
        monkeypatch.setenv("GH_TOKEN", "tok")
        return out_dir

    return setup


def test_select_generate_report_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pr_env: Callable[[dict[str, Any]], Path],
) -> None:
    out_dir = pr_env(_pr("me/proj"))
    (tmp_path / "src").mkdir()
    for name in ("a.py", "b.py"):
        (tmp_path / "src" / name).write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    changed = ["src/a.py", "src/b.py", "src/__init__.py", "README.md"]
    monkeypatch.setattr(pc, "changed_files", lambda base: changed if base == "abc123" else [])

    assert pc.main(["select", "--out-dir", str(out_dir), "--max-files", "5"]) == 0
    selection = json.loads((out_dir / "files.json").read_text())
    assert selection["files"] == ["src/a.py", "src/b.py"]

    calls: list[str] = []

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        target = command[2]
        calls.append(target)
        if target.endswith("a.py"):
            return subprocess.CompletedProcess(command, 2, "", "error: first file is broken")
        Path(command[command.index("--report-md") + 1]).write_text("### pytestgen: b\n\nfine\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    assert pc.main(["generate", "--out-dir", str(out_dir)]) == 0  # errors go to the comment
    assert calls == ["src/a.py", "src/b.py"]  # a failure on the first file does not stop the rest

    assert pc.main(["report", "--out-dir", str(out_dir)]) == 0
    body = (out_dir / "comment.md").read_text()
    assert "error: first file is broken" in body
    assert "fine" in body
    assert body.startswith(pc.MARKER)
    outputs = (tmp_path / "gh_output").read_text()
    assert "report-path=" in outputs
    assert "comment-body-path=" in outputs
    assert pc.MARKER in (tmp_path / "gh_summary").read_text()
    assert "/actions/runs/42" in body
    capsys.readouterr()


def test_generate_can_fail_the_job_on_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pr_env: Callable[[dict[str, Any]], Path],
) -> None:
    out_dir = pr_env(_pr("me/proj"))
    (out_dir / "files.json").write_text(json.dumps({"files": ["src/a.py"], "skipped": []}))
    monkeypatch.setattr(
        pc.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 2, "", "error: nope"),
    )
    assert pc.main(["generate", "--out-dir", str(out_dir)]) == 0
    assert pc.main(["generate", "--out-dir", str(out_dir), "--fail-on-error"]) == 1


def test_select_outside_a_pull_request_is_a_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pr_env: Callable[[dict[str, Any]], Path],
) -> None:
    out_dir = pr_env({})
    assert pc.main(["select", "--out-dir", str(out_dir)]) == 0
    assert json.loads((out_dir / "files.json").read_text())["files"] == []


def test_select_reports_git_problems_as_an_error(
    monkeypatch: pytest.MonkeyPatch,
    pr_env: Callable[[dict[str, Any]], Path],
) -> None:
    out_dir = pr_env(_pr("me/proj"))

    def broken(base: str) -> list[str]:
        raise RuntimeError("cannot compare")

    monkeypatch.setattr(pc, "changed_files", broken)
    assert pc.main(["select", "--out-dir", str(out_dir)]) == 1


def _prepare_comment(out_dir: Path, files: int = 1) -> None:
    (out_dir / "comment.md").write_text(f"{pc.MARKER}\nbody\n")
    results = [
        {"file": f"src/f{i}.py", "slug": f"f{i}", "status": "failed", "error": "e"}
        for i in range(files)
    ]
    (out_dir / "summary.json").write_text(json.dumps({"files": results, "skipped": []}))


def test_comment_posts_for_same_repo_pr(
    monkeypatch: pytest.MonkeyPatch, pr_env: Callable[[dict[str, Any]], Path]
) -> None:
    out_dir = pr_env(_pr("me/proj", number=12))
    _prepare_comment(out_dir)
    seen: list[Any] = []

    def fake_upsert(repo: str, number: int, body: str, token: str, *, create: bool = True) -> str:
        seen.append((repo, number, body, token, create))
        return "created"

    monkeypatch.setattr(pc, "upsert_comment", fake_upsert)
    assert pc.main(["comment", "--out-dir", str(out_dir)]) == 0
    assert seen == [("me/proj", 12, f"{pc.MARKER}\nbody\n", "tok", True)]


def test_comment_with_no_files_only_refreshes_an_existing_one(
    monkeypatch: pytest.MonkeyPatch, pr_env: Callable[[dict[str, Any]], Path]
) -> None:
    out_dir = pr_env(_pr("me/proj"))
    _prepare_comment(out_dir, files=0)
    creates: list[bool] = []
    monkeypatch.setattr(
        pc,
        "upsert_comment",
        lambda *a, create=True, **k: creates.append(create) or "skipped",
    )
    assert pc.main(["comment", "--out-dir", str(out_dir)]) == 0
    assert creates == [False]


def test_comment_is_skipped_for_forks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pr_env: Callable[[dict[str, Any]], Path],
) -> None:
    out_dir = pr_env(_pr("someone/proj"))
    _prepare_comment(out_dir)

    def boom(*_: Any, **__: Any) -> str:
        raise AssertionError("must not call the API for a fork")

    monkeypatch.setattr(pc, "upsert_comment", boom)
    assert pc.main(["comment", "--out-dir", str(out_dir)]) == 0
    assert "PR from a fork" in capsys.readouterr().out


def test_comment_survives_api_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pr_env: Callable[[dict[str, Any]], Path],
) -> None:
    out_dir = pr_env(_pr("me/proj"))
    _prepare_comment(out_dir)

    def denied(*_: Any, **__: Any) -> str:
        raise pc.GhError("HTTP 403: Resource not accessible by integration")

    monkeypatch.setattr(pc, "upsert_comment", denied)
    assert pc.main(["comment", "--out-dir", str(out_dir)]) == 0
    assert "::warning::cannot post the PR comment" in capsys.readouterr().out


def test_comment_without_token_or_pr_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pr_env: Callable[[dict[str, Any]], Path],
) -> None:
    out_dir = pr_env(_pr("me/proj"))
    _prepare_comment(out_dir)
    monkeypatch.setenv("GH_TOKEN", "")
    assert pc.main(["comment", "--out-dir", str(out_dir)]) == 0
    assert "no github-token" in capsys.readouterr().out
    monkeypatch.setenv("GITHUB_EVENT_PATH", "")
    assert pc.main(["comment", "--out-dir", str(out_dir)]) == 0
    assert "not a pull request" in capsys.readouterr().out
