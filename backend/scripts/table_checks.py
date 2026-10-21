"""Shared, parser-independent checks for the fixed table experiment."""
import re


def compact(value):
    return None if value is None else re.sub(r"\s+", "", value)


def check_cell(tables, annotation):
    rows = [r for t in tables for r in t["rows"]]
    candidates = [r for r in rows if r and compact(r[0]) == compact(annotation["row"])]
    occurrence = annotation.get("occurrence", 0)
    row = candidates[occurrence] if len(candidates) > occurrence else None
    column = annotation["column"]
    found = row is not None and column < len(row)
    actual = row[column] if found else None
    passed = found and compact(actual) == compact(annotation["expected"])
    return {**annotation, "passed": passed, "actual": actual,
            "reason": None if passed else ("row_or_column_missing" if not found else "cell_mismatch")}
