from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook

from backend.importer import commit_import, load_tabular_rows, preview_import
from backend.logic import enrich_sku


class ImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_temp_root = Path(__file__).resolve().parent.parent / "data" / "test_temp"
        self.workspace_temp_root.mkdir(parents=True, exist_ok=True)
        self.existing_skus = [
            enrich_sku(
                {
                    "sku_code": "690100000001",
                    "brand": "云南白药",
                    "product_name": "云南白药薄荷清爽牙膏",
                    "spec_text": "120g",
                    "efficacy_tags": "防蛀",
                    "current_price": 19.9,
                    "purchase_price": 12.5,
                    "six_month_sales": 320,
                }
            )
        ]

    def test_preview_and_commit_csv_import(self) -> None:
        temp_dir = self.workspace_temp_root / f"csv_{uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        csv_path = temp_dir / "sku.csv"
        csv_path.write_text(
            "条码/SKU编码,品牌,商品名称,规格净含量,功效标签,当前售价,进价,近6个月总销量\n"
            "690100000001,云南白药,云南白药薄荷清爽牙膏,120g,防蛀,19.9,12.5,320\n"
            "690100000010,佳洁士,佳洁士清新牙膏,120g,清新口气,16.9,9.6,88\n",
            encoding="utf-8-sig",
        )
        preview = preview_import("sku", csv_path, self.existing_skus)
        self.assertEqual(preview["row_count"], 2)
        self.assertEqual(preview["mapping"]["sku_code"], "条码/SKU编码")
        committed = commit_import("sku", csv_path, preview["mapping"], self.existing_skus)
        self.assertEqual(len(committed), 2)
        self.assertEqual(committed[1]["brand"], "佳洁士")

    def test_load_xlsx(self) -> None:
        temp_dir = self.workspace_temp_root / f"xlsx_{uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = temp_dir / "candidate.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["品牌", "商品名称", "规格净含量", "功效标签", "线上参考价", "预计进价"])
        sheet.append(["舒适达", "舒适达抗敏牙膏", "100g", "抗敏", 29.9, 18.5])
        workbook.save(xlsx_path)

        headers, rows = load_tabular_rows(xlsx_path)
        self.assertEqual(headers[0], "品牌")
        self.assertEqual(rows[0]["商品名称"], "舒适达抗敏牙膏")


if __name__ == "__main__":
    unittest.main()
