"""Independent small golden evaluator, NOT an Excel engine or native acceptance."""

from typing import Any

from openpyxl.formula import Tokenizer
from openpyxl.utils import range_boundaries


class Evaluator:
    def __init__(self, book: Any) -> None:
        self.book = book

    def cell(self, sheet: str, coord: str) -> Any:
        raw = self.book[sheet][coord].value
        if not isinstance(raw, str) or not raw.startswith("="):
            return raw
        tokens = [t for t in Tokenizer(raw).items if t.type != "WHITE-SPACE"]
        index = 0
        precedence = {
            "=": 1,
            ">": 1,
            "<": 1,
            ">=": 1,
            "<=": 1,
            "<>": 1,
            "&": 2,
            "+": 3,
            "-": 3,
            "*": 4,
            "/": 4,
        }

        def expression(level: int = 0) -> Any:
            nonlocal index
            token = tokens[index]
            index += 1
            if token.type == "FUNC" and token.subtype == "OPEN":
                args = []
                while not (tokens[index].type == "FUNC" and tokens[index].subtype == "CLOSE"):
                    args.append(expression())
                    if tokens[index].type == "SEP":
                        index += 1
                    else:
                        break
                index += 1
                node = ("fn", token.value[:-1], args)
            elif token.type == "PAREN" and token.subtype == "OPEN":
                node = expression()
                index += 1
            elif token.type == "OPERAND":
                node = (
                    ("ref", token.value)
                    if token.subtype == "RANGE"
                    else (
                        "literal",
                        token.value[1:-1] if token.subtype == "TEXT" else float(token.value),
                    )
                )
            else:
                raise AssertionError((raw, token))
            while (
                index < len(tokens)
                and tokens[index].type == "OPERATOR-INFIX"
                and precedence[tokens[index].value] >= level
            ):
                operator = tokens[index].value
                index += 1
                node = ("op", operator, node, expression(precedence[operator] + 1))
            return node

        tree = expression()
        assert index == len(tokens), (raw, index, tokens)

        def ref(value: str) -> Any:
            if value.startswith("tblData["):
                header = value[8:-1]
                ws = self.book["Данные"]
                min_col, min_row, max_col, max_row = range_boundaries(ws.tables["tblData"].ref)
                col = next(c.column for c in ws[min_row] if c.value == header)
                return [ws.cell(r, col).value for r in range(min_row + 1, max_row + 1)]
            name, coordinate = value.rsplit("!", 1) if "!" in value else (sheet, value)
            name = name.strip("'")
            coordinate = coordinate.replace("$", "")
            if ":" in coordinate:
                a, b, c, d = range_boundaries(coordinate)
                return [
                    self.cell(name, self.book[name].cell(r, col).coordinate)
                    for r in range(b, d + 1)
                    for col in range(a, c + 1)
                ]
            return self.cell(name, coordinate)

        def evaluate(node: Any) -> Any:
            kind, *parts = node
            if kind == "literal":
                return parts[0]
            if kind == "ref":
                return ref(parts[0])
            if kind == "op":
                op, a, b = parts
                left, right = evaluate(a), evaluate(b)
                if op == "=":
                    return left == right
                if op == ">":
                    return left > right
                if op == "<=":
                    return left <= right
                if op == "+":
                    return left + right
                if op == "-":
                    return left - right
                if op == "/":
                    return left / right
                if op == "&":
                    return str(left) + str(right)
                raise AssertionError(op)
            name, args = parts
            if name == "IF":
                return evaluate(args[1] if evaluate(args[0]) else args[2])
            if name == "IFERROR":
                try:
                    return evaluate(args[0])
                except IndexError, ZeroDivisionError:
                    return evaluate(args[1])
            values = [evaluate(a) for a in args]
            if name == "INDEX":
                return values[0][int(values[1]) - 1]
            if name == "ISBLANK":
                return values[0] is None
            if name == "ISNUMBER":
                return isinstance(values[0], (float, int)) and not isinstance(values[0], bool)
            if name == "OR":
                return any(values)
            if name == "AND":
                return all(values)
            if name == "COUNTIF":
                return sum(v == values[1] for v in values[0])
            if name == "SUM":
                return sum(
                    v for group in values for v in (group if isinstance(group, list) else [group])
                )
            if name == "ROW":
                return self.book[sheet][coord].row
            raise AssertionError(name)

        return evaluate(tree)
