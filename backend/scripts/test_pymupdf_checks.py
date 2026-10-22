"""Run in the isolated PDF experiment environment: python -m unittest discover -s backend/scripts -p test_pymupdf_checks.py."""

import unittest
from pymupdf_checks import markdown_tables, exported_tables, match_table
from table_checks import check_cell


class MarkdownChecks(unittest.TestCase):
    def test_preserve_values_and_columns(self):
        text = "| 项目 | 2024 | 2023 |\n|---|---|---|\n| 长<br>行名 | (1,234.50) | - |\n| 空白 | | 0 |\n| 转义\\|符号 | 2 | 3 |\n"
        tables = markdown_tables(text)
        self.assertTrue(
            check_cell(tables, dict(row="长行名", column=1, expected="(1,234.50)"))[
                "passed"
            ]
        )
        self.assertTrue(
            check_cell(tables, dict(row="空白", column=1, expected=""))["passed"]
        )
        self.assertFalse(
            check_cell(tables, dict(row="空白", column=1, expected=None))["passed"]
        )
        self.assertFalse(
            check_cell(tables, dict(row="长行名", column=2, expected="0"))["passed"]
        )
        self.assertEqual(tables[0]["rows"][-1][0], "转义|符号")

    def test_no_false_region_assignment(self):
        text = "| 项目 | 数值 |\n|---|---|\n| A | 1 |\n"
        chunk = {"text": text, "tables": []}
        self.assertEqual(
            match_table(exported_tables(chunk), [0, 0, 10, 10])[1]["status"],
            "unmatched",
        )
        chunk["page_boxes"] = [
            {"class": "table", "pos": [0, len(text)], "bbox": [0, 0, 10, 10]}
        ]
        tables = exported_tables(chunk)
        self.assertEqual(match_table(tables, [0, 0, 10, 10])[1]["status"], "matched")
        self.assertEqual(
            match_table(tables * 2, [0, 0, 10, 10])[1]["status"], "ambiguous"
        )


if __name__ == "__main__":
    unittest.main()
