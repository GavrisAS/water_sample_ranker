"""Use GitHub's immutable reservation; never infer availability from tags."""

import os

from water_sample_ranker.versioning import reserved_tag


def main() -> None:
    workflow = os.environ["GITHUB_WORKFLOW_REF"].split("/", 2)[2].split("@", 1)[0]
    tag = reserved_tag(
        event=os.environ["GITHUB_EVENT_NAME"],
        ref=os.environ["GITHUB_REF"],
        workflow=workflow,
        run_number=int(os.environ["GITHUB_RUN_NUMBER"]),
    )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"tag={tag}\nversion={tag[1:]}\n")
    print(tag)


if __name__ == "__main__":
    main()
