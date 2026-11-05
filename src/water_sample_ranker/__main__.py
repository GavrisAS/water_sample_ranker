import argparse
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from water_sample_ranker.gui.app import main as gui_main


def entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--cli" not in args:
        return gui_main(args)
    from water_sample_ranker.cli import main as cli_main

    args.remove("--cli")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cli-log", type=Path)
    options, command = parser.parse_known_args(args)
    if options.cli_log is None:
        return cli_main(command)
    # Windows windowed executables have no stdout/stderr. Explicit local log,
    # exclusive creation: never overwrite an input or a user's existing file.
    with options.cli_log.open("x", encoding="utf-8", newline="\n") as stream:
        with redirect_stdout(stream), redirect_stderr(stream):
            return cli_main(command)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
