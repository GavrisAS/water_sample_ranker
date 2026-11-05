"""Release series v0.1.PATCH: reserve with the dedicated push-only workflow run."""

import re

_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def parse_version(version: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(version)
    if not match:
        raise ValueError("VERSION_REQUIRES_MAJOR_MINOR_PATCH")
    return int(match[1]), int(match[2]), int(match[3])


def parse_release_tag(tag: str) -> tuple[int, int, int]:
    if not tag.startswith("v"):
        raise ValueError("TAG_REQUIRES_V_PREFIX")
    return parse_version(tag[1:])


def reserved_tag(*, event: str, ref: str, workflow: str, run_number: int) -> str:
    if (event, ref, workflow) != ("push", "refs/heads/main", ".github/workflows/release-main.yml"):
        raise ValueError("INELIGIBLE_RELEASE_EVENT")
    if type(run_number) is not int or run_number < 1:
        raise ValueError("INVALID_RUN_NUMBER")
    # Failed runs consume their number. run_attempt, tags and other workflows never enter here.
    return f"v0.1.{run_number}"
