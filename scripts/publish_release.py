"""Immutable tags, complete draft assets, verified publication; reruns verify first."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from scripts.release_assets import asset_names, verify_release_assets
from water_sample_ranker.build_provenance import build_inputs
from water_sample_ranker.versioning import reserved_tag


def gh(*args: str, missing: bool = False) -> Any:
    result = subprocess.run(["gh", *args], capture_output=True)
    if result.returncode:
        if missing and b"HTTP 404" in result.stderr:
            return None
        raise ValueError("GITHUB_COMMAND_FAILED")
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.decode().strip()


def verify_jobs(jobs: list[dict[str, Any]]) -> None:
    linux = [j for j in jobs if "linux-checks" in j["name"] and "ubuntu-latest" in j["name"]]
    windows = [j for j in jobs if "windows-build" in j["name"] and j["name"].endswith(" / build")]
    if (
        not linux
        or not windows
        or any(
            j.get("conclusion") != "success" or j.get("status") != "completed"
            for j in linux + windows
        )
    ):
        raise ValueError("REMOTE_LINUX_WINDOWS_GATES_REQUIRED")


def verify_existing(
    root: Path,
    repo: str,
    tag: str,
    commit: str,
    run_id: str,
    run_number: int,
) -> dict[str, Any] | None:
    release = gh("api", f"repos/{repo}/releases/tags/{tag}", missing=True)
    if release is None:
        return None
    expected = set(asset_names(tag[1:]))
    if (
        release.get("tag_name") != tag
        or not release.get("prerelease")
        or {a["name"] for a in release["assets"]} != expected
        or len(release["assets"]) != len(expected)
    ):
        raise ValueError("CONFLICT_FAIL_RELEASE_METADATA_OR_ASSETS")
    reference = gh("api", f"repos/{repo}/git/ref/tags/{tag}")
    if reference["object"] != {"sha": commit, "type": "commit", "url": reference["object"]["url"]}:
        raise ValueError("CONFLICT_FAIL_TAG_SHA")
    with tempfile.TemporaryDirectory(prefix="wsr-release-verify-") as directory:
        gh("release", "download", tag, "--repo", repo, "--dir", directory)
        verify_release_assets(
            Path(directory),
            version=tag[1:],
            commit=commit,
            run_id=run_id,
            run_number=run_number,
            inputs=build_inputs(root),
        )
    return release


def candidate_name(artifacts: list[dict[str, Any]], version: str, run_id: str, attempt: int) -> str:
    prefix = f"candidate-v{version}-{run_id}-"
    candidates = []
    for item in artifacts:
        name = item["name"]
        suffix = name.removeprefix(prefix)
        if (
            name.startswith(prefix)
            and suffix.isdecimal()
            and 1 <= int(suffix) <= attempt
            and not item.get("expired")
        ):
            candidates.append((int(suffix), name))
    if not candidates or len({n for _, n in candidates}) != len(candidates):
        raise ValueError("VERIFIED_CANDIDATE_ARTIFACT_MISSING")
    return max(candidates)[1]


def main() -> None:
    root = Path.cwd()
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id, run_number = os.environ["GITHUB_RUN_ID"], int(os.environ["GITHUB_RUN_NUMBER"])
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    workflow = os.environ["GITHUB_WORKFLOW_REF"].split("/", 2)[2].split("@", 1)[0]
    tag = reserved_tag(
        event=os.environ["GITHUB_EVENT_NAME"],
        ref=os.environ["GITHUB_REF"],
        workflow=workflow,
        run_number=run_number,
    )
    commit = os.environ["GITHUB_SHA"]
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != commit:
        raise ValueError("RELEASE_CHECKOUT_SHA_MISMATCH")
    run = gh("api", f"repos/{repo}/actions/runs/{run_id}")
    if (
        run["head_sha"] != commit
        or run["event"] != "push"
        or run["run_number"] != run_number
        or run["path"] != ".github/workflows/release-main.yml"
    ):
        raise ValueError("RELEASE_RUN_SOURCE_MISMATCH")
    jobs_by_name = {}
    # Successful prerequisites may belong to an earlier attempt in a failed-jobs rerun.
    for number in range(1, attempt + 1):
        jobs = gh("api", f"repos/{repo}/actions/runs/{run_id}/attempts/{number}/jobs?per_page=100")
        if jobs["total_count"] > 100:
            raise ValueError("RELEASE_JOB_INVENTORY_TRUNCATED")
        for job in jobs["jobs"]:
            jobs_by_name[job["name"]] = job
    verify_jobs(list(jobs_by_name.values()))
    existing = verify_existing(root, repo, tag, commit, run_id, run_number)
    if existing is not None:
        if existing["draft"]:
            gh(
                "release",
                "edit",
                tag,
                "--repo",
                repo,
                "--draft=false",
                "--prerelease",
                "--latest=false",
            )
            existing = verify_existing(root, repo, tag, commit, run_id, run_number)
        if existing is None or existing["draft"]:
            raise ValueError("RELEASE_STILL_DRAFT")
        print(
            json.dumps(
                {"status": "ALREADY_PUBLISHED_VERIFIED", "tag": tag, "url": existing["html_url"]}
            )
        )
        return
    artifacts = gh("api", f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100")
    if artifacts["total_count"] > 100:
        raise ValueError("RELEASE_ARTIFACT_INVENTORY_TRUNCATED")
    name = candidate_name(artifacts["artifacts"], tag[1:], run_id, attempt)
    with tempfile.TemporaryDirectory(prefix="wsr-publish-") as directory:
        gh("run", "download", run_id, "--repo", repo, "--name", name, "--dir", directory)
        assets = Path(directory)
        verify_release_assets(
            assets,
            version=tag[1:],
            commit=commit,
            run_id=run_id,
            run_number=run_number,
            inputs=build_inputs(root),
        )
        remote_tag = gh("api", f"repos/{repo}/git/ref/tags/{tag}", missing=True)
        if remote_tag is not None:
            if remote_tag["object"]["type"] != "commit" or remote_tag["object"]["sha"] != commit:
                raise ValueError("CONFLICT_FAIL_IMMUTABLE_TAG")
        else:
            subprocess.run(["git", "tag", tag, commit], check=True, capture_output=True)
            subprocess.run(
                ["git", "push", "origin", f"refs/tags/{tag}"], check=True, capture_output=True
            )
        remote_tag = gh("api", f"repos/{repo}/git/ref/tags/{tag}")
        if remote_tag["object"]["sha"] != commit or remote_tag["object"]["type"] != "commit":
            raise ValueError("RELEASE_SOURCE_TAG_SHA_MISMATCH")
        notes = root / "artifacts/private/release-notes.txt"
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text(
            f"TECHNICAL PREVIEW — НЕ УТВЕРЖДЁННЫЙ РЕЙТИНГ\n\n"
            f"Версия {tag}; source commit {commit}; reserved run {run_id}/{run_number}.\n"
            "Windows x64 onedir: распаковать целиком, запуск без Python/uv.\n"
            "Linux/Windows checks, fresh frozen GUI/CLI/template: PASS.\n"
            "Синтетический frozen update-all: PASS. Баллы, ранги, TOP пусты.\n"
            "UNKNOWN не заменяется FALSE/нулём; №7–9 — наблюдения/кандидаты.\n"
            "Native Microsoft Excel и ручная Windows UX: NOT_RUN. Методика: PENDING.\n"
            "Производственные данные не включены. Synthetic checks не заменяют их квалификацию.\n"
            "Вопросы заказчику ведутся отдельно. Не прикладывайте приватные файлы к Issues.\n"
            "Проверяйте SHA-256 ZIP по соседним .sha256. Состав связан BUILD_EVIDENCE.json.\n",
            encoding="utf-8",
        )
        gh(
            "release",
            "create",
            tag,
            *[str(assets / n) for n in asset_names(tag[1:])],
            "--repo",
            repo,
            "--verify-tag",
            "--target",
            commit,
            "--title",
            f"Water Sample Ranker {tag} — technical preview",
            "--notes-file",
            str(notes),
            "--draft",
            "--prerelease",
            "--latest=false",
        )
        draft = verify_existing(root, repo, tag, commit, run_id, run_number)
        if draft is None or not draft["draft"]:
            raise ValueError("RELEASE_DRAFT_VERIFICATION_FAILED")
        gh(
            "release",
            "edit",
            tag,
            "--repo",
            repo,
            "--draft=false",
            "--prerelease",
            "--latest=false",
        )
    published = verify_existing(root, repo, tag, commit, run_id, run_number)
    if published is None or published["draft"]:
        raise ValueError("RELEASE_PUBLICATION_NOT_VERIFIED")
    print(
        json.dumps(
            {
                "status": "PUBLIC_PRERELEASE_PUBLISHED",
                "tag": tag,
                "source_commit": commit,
                "url": published["html_url"],
                "isPrerelease": published["prerelease"],
            }
        )
    )


if __name__ == "__main__":
    main()
