"""Streaming OOXML cells: preserve raw formula/cache/error without executing links."""

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from posixpath import normpath
from zipfile import ZipFile

from water_sample_ranker.domain.models import CellValue

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def q(name: str) -> str:
    return f"{{{NS}}}{name}"


@dataclass(frozen=True, slots=True)
class Cell:
    coordinate: str
    value: CellValue = None
    formula: str | None = None
    status: str = "MISSING"


class Book:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.zip = ZipFile(path)
        root = ET.fromstring(self.zip.read("xl/workbook.xml"))
        rels = ET.fromstring(self.zip.read("xl/_rels/workbook.xml.rels"))
        targets = {
            r.attrib["Id"]: normpath("xl/" + r.attrib["Target"])
            if not r.attrib["Target"].startswith("/")
            else r.attrib["Target"].lstrip("/")
            for r in rels
            if r.attrib.get("TargetMode") != "External"
        }
        self.sheets = {
            s.attrib["name"]: targets[s.attrib[f"{{{REL}}}id"]]
            for s in root.findall(f"{q('sheets')}/{q('sheet')}")
        }
        properties = root.find(q("workbookPr"))
        self.epoch1904 = properties is not None and properties.get("date1904") in {"1", "true"}
        self.strings: list[str] = []
        if "xl/sharedStrings.xml" in self.zip.namelist():
            with self.zip.open("xl/sharedStrings.xml") as stream:
                for _, elem in ET.iterparse(stream, events=("end",)):
                    if elem.tag == q("si"):
                        self.strings.append("".join(t.text or "" for t in elem.iter(q("t"))))
                        elem.clear()

    def __enter__(self) -> Book:
        return self

    def __exit__(self, *_: object) -> None:
        self.zip.close()

    def rows(self, sheet: str, max_row: int | None = None) -> Iterator[tuple[int, dict[str, Cell]]]:
        with self.zip.open(self.sheets[sheet]) as stream:
            for _, elem in ET.iterparse(stream, events=("end",)):
                if elem.tag != q("row"):
                    continue
                row = int(elem.attrib["r"])
                if max_row is not None and row > max_row:
                    return
                cells: dict[str, Cell] = {}
                for c in elem.findall(q("c")):
                    coord = c.attrib["r"]
                    kind = c.attrib.get("t", "n")
                    v = c.find(q("v"))
                    formula_node = c.find(q("f"))
                    formula = "=" + (formula_node.text or "") if formula_node is not None else None
                    raw = v.text if v is not None else None
                    value: CellValue = raw
                    if kind == "s" and raw is not None:
                        value = self.strings[int(raw)]
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(q("t")))
                    elif kind == "n" and raw is not None:
                        try:
                            number = float(raw)
                            value = int(number) if number.is_integer() else number
                        except ValueError:
                            value = raw
                    elif kind == "b":
                        value = raw == "1"
                    status = (
                        "ERROR"
                        if kind == "e"
                        else "VALID"
                        if value is not None
                        else ("FORMULA_NO_CACHE" if formula is not None else "MISSING")
                    )
                    cells[coord.rstrip("0123456789")] = Cell(coord, value, formula, status)
                elem.clear()
                if any(c.value is not None or c.formula for c in cells.values()):
                    yield row, cells
