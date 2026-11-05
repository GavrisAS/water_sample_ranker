"""Derive a sanitized water template from an explicitly selected root reference."""

import argparse
from pathlib import Path

from water_sample_ranker.excel.template_adapter import derive

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    derive(args.reference, args.output)
