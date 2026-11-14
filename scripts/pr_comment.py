#!/usr/bin/env python3
"""Pull-request helper of the pytestgen GitHub Action (standard library only).

Four subcommands, run one after another by ``action.yml``:

``select``    changed ``.py`` files of the PR -> ``<out>/files.json``
``generate``  ``pytestgen generate`` for each file -> ``<out>/summary.json`` (a failure on one
              file is recorded, never raised, so the others are still reported)
``report``    combine the per-file markdown reports -> ``<out>/comment.md``
``comment``   create or update the sticky PR comment through ``gh api``

The script never touches git state and never writes to the PR branch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

MARKER = "<!-- pytestgen-report -->"
DEFAULT_PATHS = "src/**/*.py"
DEFAULT_MAX_FILES = 5
ARTIFACT_NAME = "pytestgen-tests"
MAX_COMMENT_CHARS = 60_000  # GitHub rejects comments over 65536 characters
ERROR_TAIL_LINES = 25
ERROR_TAIL_CHARS = 2_500
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TEST_DIRS = frozenset({"tests", "test"})


def out(text: str = "") -> None:
    """Write one line to stdout (the production-code rules forbid ``print``)."""
    sys.stdout.write(text + "\n")


def annotate(level: str, message: str) -> None:
    """GitHub Actions workflow command, e.g. ``::warning::``; newlines are escaped."""
    out(f"::{level}::{message.replace('%', '%25').replace(chr(10), '%0A')}")


# --------------------------------------------------------------------------- file selection


def split_patterns(raw: str) -> list[str]:
    """Glob patterns separated by newlines or commas."""
    return [part.strip() for part in re.split(r"[\n,]", raw) if part.strip()]


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob: ``*`` stays inside one directory, ``**`` crosses directories.

    ``src/**/*.py`` matches ``src/a.py`` as well as ``src/pkg/sub/a.py``.
    """
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif char == "*":
            parts.append("[^/]*")
            i += 1
        elif char == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(char))
            i += 1
    return re.compile("".join(parts))


def is_excluded(path: str) -> bool:
    """Tests, conftest files and ``__init__.py`` are never targets."""
    pure = PurePosixPath(path)
    name = pure.name
    return (
        name in {"__init__.py", "conftest.py"}
        or name.startswith("test_")
        or name.endswith("_test.py")
        or any(part in _TEST_DIRS for part in pure.parts[:-1])
    )


def filter_files(
    changed: Iterable[str], patterns: Sequence[str], max_files: int
) -> tuple[list[str], list[str]]:
    """Changed paths matching a pattern, sorted; returns ``(selected, over_the_cap)``."""
    regexes = [glob_to_regex(p) for p in patterns]
    matched = sorted(
        {
            path
            for path in (p.replace("\\", "/").removeprefix("./") for p in changed)
            if path.endswith(".py")
            and not is_excluded(path)
            and any(rx.fullmatch(path) for rx in regexes)
        }
    )
    cap = max(max_files, 0)
    return matched[:cap], matched[cap:]


def slug_for(path: str) -> str:
    """Flat, collision-resistant name: ``src/shop/pricing.py`` -> ``src__shop__pricing``.

    The full path is kept (``src/pkg/x.py`` and ``pkg/x.py`` must not share a slug).
    """
    return "__".join(PurePosixPath(path).with_suffix("").parts)


# --------------------------------------------------------------------------- PR context


@dataclass(frozen=True)
class PrContext:
    number: int | None
    base_sha: str
    is_fork: bool


def load_pr_context(event_path: str | None) -> PrContext:
    """Read the ``pull_request`` webhook payload; unknown fields come back empty."""
    if not event_path or not Path(event_path).is_file():
        return PrContext(None, "", is_fork=False)
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pr = event.get("pull_request") or {}
    base_repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    number = pr.get("number")
    return PrContext(
        number=int(number) if number is not None else None,
        base_sha=str((pr.get("base") or {}).get("sha") or ""),
        is_fork=bool(pr) and head_repo != base_repo,
    )


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git") or "git"
    return subprocess.run([git, *args], capture_output=True, check=False)


def changed_files(base_sha: str) -> list[str]:
    """Files added/copied/modified/renamed between the PR base and ``HEAD``.

    ``base...HEAD`` needs the history (``fetch-depth: 0``). With the default shallow
    checkout of the merge commit, the base commit is fetched alone and compared directly:
    for ``refs/pull/N/merge`` that is exactly the change set of the PR.
    """
    diff = ["diff", "--name-only", "-z", "--diff-filter=ACMR"]
    result = _git([*diff, f"{base_sha}...HEAD"])
    if result.returncode != 0:
        fetched = _git(["fetch", "--no-tags", "--depth=1", "origin", base_sha])
        if fetched.returncode != 0:
            msg = (
                f"cannot compare with base {base_sha}: use actions/checkout with fetch-depth: 0 "
                f"({fetched.stderr.decode(errors='replace').strip()})"
            )
            raise RuntimeError(msg)
        result = _git([*diff, base_sha, "HEAD"])
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.decode(errors='replace').strip()}")
    return [name for name in result.stdout.decode("utf-8", errors="replace").split("\0") if name]


# --------------------------------------------------------------------------- generation


@dataclass
class FileResult:
    file: str
    slug: str
    status: str  # ok | failed | timeout
    returncode: int | None = None
    duration_s: float = 0.0
    error: str = ""
    line_before: float | None = None
    line_after: float | None = None
    branch_before: float | None = None
    branch_after: float | None = None
    kept_tests: int | None = None
    model: str = ""


def child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Environment for pytestgen: empty ``PYTESTGEN_*`` values are dropped, so the defaults
    apply (an empty API key would otherwise become an empty bearer token)."""
    env = {k: v for k, v in environ.items() if not (k.startswith("PYTESTGEN_") and not v.strip())}
    env.setdefault("PYTESTGEN_LOG_FORMAT", "console")
    env["NO_COLOR"] = "1"
    return env


def tail_text(text: str) -> str:
    """Last lines of a process log, without ANSI colours, bounded in size."""
    lines = [line.rstrip() for line in _ANSI.sub("", text).splitlines() if line.strip()]
    tail = "\n".join(lines[-ERROR_TAIL_LINES:])
    return tail[-ERROR_TAIL_CHARS:]


def read_json_report(path: Path) -> dict[str, Any]:
    """Numbers of a pytestgen JSON report; ``{}`` if the file is missing or malformed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        delta = data["delta"]
        return {
            "line_before": float(delta["before"]["line_percent"]),
            "line_after": float(delta["after"]["line_percent"]),
            "branch_before": float(delta["before"]["branch_percent"]),
            "branch_after": float(delta["after"]["branch_percent"]),
            "kept_tests": int(data["kept_tests"]),
            "model": str(data.get("model") or ""),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def generate_one(
    file: str,
    *,
    pytestgen_bin: str,
    out_dir: Path,
    strategy: str,
    timeout_s: float,
    env: Mapping[str, str],
) -> FileResult:
    """Run ``pytestgen generate`` for one file; never raises on a generation failure."""
    slug = slug_for(file)
    md_path, json_path = out_dir / f"{slug}.md", out_dir / f"{slug}.json"
    command = [
        pytestgen_bin,
        "generate",
        file,
        "--project-root",
        ".",
        "-o",
        str(out_dir / "tests" / f"test_{slug}_generated.py"),
        "--report-md",
        str(md_path),
        "--report-json",
        str(json_path),
        "--strategy",
        strategy,
    ]
    started = time.monotonic()
    result = FileResult(file=file, slug=slug, status="failed")
    log = ""
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env),
            timeout=timeout_s,
            check=False,
        )
        log = f"{proc.stdout}\n{proc.stderr}"
        result.returncode = proc.returncode
        if proc.returncode == 0 and md_path.is_file():
            result.status = "ok"
        else:
            result.error = tail_text(log) or f"pytestgen exited with code {proc.returncode}"
    except subprocess.TimeoutExpired as exc:
        result.status = "timeout"
        partial = exc.stdout if isinstance(exc.stdout, str) else ""
        log = partial
        result.error = f"timed out after {timeout_s:.0f} s\n{tail_text(partial)}".strip()
    except OSError as exc:
        result.error = f"cannot start pytestgen: {exc}"
    result.duration_s = round(time.monotonic() - started, 1)
    (out_dir / f"{slug}.log").write_text(log, encoding="utf-8")
    if result.status == "ok":
        for key, value in read_json_report(json_path).items():
            setattr(result, key, value)
    return result


# --------------------------------------------------------------------------- comment body


def md_cell(text: str) -> str:
    return text.replace("|", r"\|").replace("\n", " ")


def fence(text: str) -> str:
    """Code block that survives backtick runs inside ``text``."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


def _pct(before: float | None, after: float | None) -> str:
    if before is None or after is None:
        return "n/a"
    return f"{before:.1f}% -> {after:.1f}% ({after - before:+.1f} pp)"


def _status_label(result: FileResult) -> str:
    return {"ok": "ok", "timeout": "timeout"}.get(result.status, "failed")


def summary_table(results: Sequence[FileResult]) -> list[str]:
    lines = [
        "| File | Status | Lines | Branches | Kept tests |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        kept = "-" if r.kept_tests is None else str(r.kept_tests)
        lines.append(
            f"| `{md_cell(r.file)}` | {_status_label(r)} | "
            f"{_pct(r.line_before, r.line_after)} | {_pct(r.branch_before, r.branch_after)} | "
            f"{kept} |"
        )
    return lines


def file_section(result: FileResult, out_dir: Path) -> str:
    """Per-file markdown: pytestgen's own report, or the error."""
    if result.status == "ok":
        try:
            return (out_dir / f"{result.slug}.md").read_text(encoding="utf-8").strip()
        except OSError as exc:
            result = FileResult(**{**asdict(result), "status": "failed", "error": str(exc)})
    reason = "timed out" if result.status == "timeout" else "failed"
    code = f" (exit code {result.returncode})" if result.returncode else ""
    details = fence(result.error or "no output")
    return f"### pytestgen: `{result.file}`\n\n**Generation {reason}**{code}\n\n{details}"


def build_comment(
    results: Sequence[FileResult],
    out_dir: Path,
    *,
    skipped: Sequence[str] = (),
    run_url: str = "",
    sha: str = "",
) -> str:
    """The sticky comment: marker, summary table, then one section per file."""
    head = [MARKER, "## pytestgen: generated tests", ""]
    if not results:
        head.append("No changed Python files matched the configured paths; nothing to generate.")
    else:
        failed = sum(r.status != "ok" for r in results)
        model = next((r.model for r in results if r.model), "")
        facts = f"{len(results)} file(s)" + (f", {failed} failed" if failed else "")
        head += [f"{facts}" + (f", model `{md_cell(model)}`" if model else ""), ""]
        head += summary_table(results)
    notes: list[str] = []
    if skipped:
        listed = ", ".join(f"`{md_cell(s)}`" for s in skipped[:10])
        more = f" and {len(skipped) - 10} more" if len(skipped) > 10 else ""
        notes.append(f"Over the file limit, not processed: {listed}{more}.")
    foot = ["", "---", _footer(run_url, sha)]
    sections = [file_section(r, out_dir) for r in results]
    return _assemble(head, sections, notes, foot)


def _footer(run_url: str, sha: str) -> str:
    where = f"[workflow run]({run_url})" if run_url else "the workflow run"
    commit = f" for `{sha[:7]}`" if sha else ""
    return (
        f"<sub>Generated tests are in the `{ARTIFACT_NAME}` artifact of {where}{commit}; "
        "nothing was committed to this branch.</sub>"
    )


def _assemble(head: list[str], sections: list[str], notes: list[str], foot: list[str]) -> str:
    """Join the parts; whole sections that would exceed the comment limit are left out."""
    fixed = "\n".join([*head, "", *notes, *foot]) + "\n"
    budget = MAX_COMMENT_CHARS - len(fixed) - 400
    kept: list[str] = []
    omitted = 0
    for section in sections:
        cost = len(section) + 2
        if cost <= budget:
            kept.append(section)
            budget -= cost
        else:
            omitted += 1
    if omitted:
        notes = [
            *notes,
            f"{omitted} report(s) left out because of the comment size limit; "
            f"the full reports are in the `{ARTIFACT_NAME}` artifact.",
        ]
    lines = [*head, ""]
    for section in kept:
        lines += [section, ""]
    lines += [*notes, *foot]
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- sticky comment


def parse_json_stream(text: str) -> list[dict[str, Any]]:
    """Objects from ``gh api --paginate`` output: one array per page, back to back."""
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        value, pos = decoder.raw_decode(text, pos)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                items.append(item)
    return items


def find_marker_comment(comments: Iterable[Mapping[str, Any]]) -> int | None:
    """Id of the oldest comment carrying the hidden marker, if any."""
    ids = [
        int(c["id"])
        for c in comments
        if isinstance(c.get("body"), str) and MARKER in c["body"] and "id" in c
    ]
    return min(ids) if ids else None


class GhError(RuntimeError):
    """``gh api`` failed (missing permission, network, ...)."""


def gh_api(args: Sequence[str], *, token: str, payload: Mapping[str, Any] | None = None) -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise GhError("the gh CLI is not installed")
    command = [gh, "api", *args] + (["--input", "-"] if payload is not None else [])
    proc = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GH_TOKEN": token},
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout).strip() or f"gh exited with {proc.returncode}")
    return proc.stdout


def upsert_comment(repo: str, number: int, body: str, token: str, *, create: bool = True) -> str:
    """Create the sticky comment or update the existing one; returns what happened."""
    listing = gh_api(
        ["--paginate", f"repos/{repo}/issues/{number}/comments?per_page=100"], token=token
    )
    existing = find_marker_comment(parse_json_stream(listing))
    if existing is not None:
        gh_api(
            ["--method", "PATCH", f"repos/{repo}/issues/comments/{existing}"],
            token=token,
            payload={"body": body},
        )
        return "updated"
    if not create:
        return "skipped"
    gh_api(
        ["--method", "POST", f"repos/{repo}/issues/{number}/comments"],
        token=token,
        payload={"body": body},
    )
    return "created"


# --------------------------------------------------------------------------- subcommands


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _set_output(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def cmd_select(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    context = load_pr_context(os.environ.get("GITHUB_EVENT_PATH"))
    base = args.base or context.base_sha
    if not base:
        annotate("warning", "no pull request base found (not a pull_request event?): nothing to do")
        selected: list[str] = []
        skipped: list[str] = []
    else:
        patterns = split_patterns(args.paths) or [DEFAULT_PATHS]
        selected, skipped = filter_files(changed_files(base), patterns, args.max_files)
        selected = [f for f in selected if Path(f).is_file()]
    _write_json(out_dir / "files.json", {"base": base, "files": selected, "skipped": skipped})
    out(f"pytestgen targets ({len(selected)}): {', '.join(selected) or '-'}")
    if skipped:
        annotate("notice", f"{len(skipped)} changed file(s) over the limit were not processed")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    selection = json.loads((out_dir / "files.json").read_text(encoding="utf-8"))
    env = child_env(os.environ)
    results: list[FileResult] = []
    for file in selection["files"]:
        out(f"::group::pytestgen generate {file}")
        result = generate_one(
            file,
            pytestgen_bin=args.pytestgen_bin,
            out_dir=out_dir,
            strategy=args.strategy,
            timeout_s=args.timeout,
            env=env,
        )
        out(f"{file}: {result.status} in {result.duration_s} s")
        out("::endgroup::")
        if result.status != "ok":
            annotate("warning", f"pytestgen failed for {file}")
        results.append(result)
    _write_json(
        out_dir / "summary.json",
        {"files": [asdict(r) for r in results], "skipped": selection.get("skipped", [])},
    )
    failed = [r for r in results if r.status != "ok"]
    return 1 if failed and args.fail_on_error else 0


def _load_summary(out_dir: Path) -> tuple[list[FileResult], list[str]]:
    data = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    return [FileResult(**item) for item in data["files"]], list(data.get("skipped", []))


def cmd_report(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    results, skipped = _load_summary(out_dir)
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo, run_id = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""
    body = build_comment(
        results, out_dir, skipped=skipped, run_url=run_url, sha=os.environ.get("GITHUB_SHA", "")
    )
    comment_path = out_dir / "comment.md"
    comment_path.write_text(body, encoding="utf-8", newline="\n")
    summary_target = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_target:
        with Path(summary_target).open("a", encoding="utf-8") as handle:
            handle.write(body + "\n")
    _set_output("report-path", str((out_dir / "summary.json").resolve()))
    _set_output("comment-body-path", str(comment_path.resolve()))
    out(f"comment body: {comment_path} ({len(body)} characters)")
    return 0


def cmd_comment(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    body = (out_dir / "comment.md").read_text(encoding="utf-8")
    results, _ = _load_summary(out_dir)
    context = load_pr_context(os.environ.get("GITHUB_EVENT_PATH"))
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", "")
    if context.number is None or not repo:
        out("not a pull request: no comment")
    elif context.is_fork:
        annotate("notice", "PR from a fork: the token is read-only, comment skipped (see summary)")
    elif not token:
        annotate("warning", "no github-token: comment skipped")
    else:
        try:
            # With nothing to report, only refresh a comment that already exists.
            outcome = upsert_comment(repo, context.number, body, token, create=bool(results))
            out(f"sticky comment {outcome}")
        except (GhError, subprocess.TimeoutExpired, ValueError) as exc:
            annotate("warning", f"cannot post the PR comment (needs pull-requests: write): {exc}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("select", "generate", "report", "comment"):
        p = sub.add_parser(name)
        p.add_argument("--out-dir", default="out")
        if name == "select":
            p.add_argument("--paths", default=DEFAULT_PATHS)
            p.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
            p.add_argument("--base", default="", help="base commit; default: from the PR event")
            p.set_defaults(func=cmd_select)
        elif name == "generate":
            p.add_argument("--pytestgen-bin", default="pytestgen")
            p.add_argument("--strategy", default="hybrid", choices=["hybrid", "llm"])
            p.add_argument("--timeout", type=float, default=1200.0, help="seconds per file")
            p.add_argument("--fail-on-error", action="store_true")
            p.set_defaults(func=cmd_generate)
        elif name == "report":
            p.set_defaults(func=cmd_report)
        else:
            p.set_defaults(func=cmd_comment)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        annotate("error", f"pr_comment {args.command}: {exc}")
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
