import sys

from water_sample_ranker.gui.app import main

if __name__ == "__main__":
    if "--cli" in sys.argv[1:]:
        from water_sample_ranker.cli import main as cli_main

        args = sys.argv[1:]
        args.remove("--cli")
        raise SystemExit(cli_main(args))
    raise SystemExit(main())
