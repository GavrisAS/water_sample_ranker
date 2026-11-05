"""Read-only schema inventory and source role checks."""

from pathlib import Path

from water_sample_ranker.readers.ooxml import Book


def inventory(path: Path) -> dict[str, object]:
    with Book(path) as b:
        return {
            "sheets": list(b.sheets),
            "external_links": sum(
                n.startswith("xl/externalLinks/externalLink") and n.endswith(".xml")
                for n in b.zip.namelist()
            ),
            "macros_present": any("vbaProject.bin" in n for n in b.zip.namelist()),
            "macros_executed": False,
            "links_executed": False,
        }
