#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from docx import Document
import json

doc = Document('Lossless Transmission for Geo-Distributed AI Computing via LSQ-Sketch and APN-Aware SRv6 Backpressure.docx')

for t_idx, table in enumerate(doc.tables):
    print(f"=== Table {t_idx + 1} ===")
    for r_idx, row in enumerate(table.rows):
        row_data = [cell.text.strip() for cell in row.cells]
        print(f"Row {r_idx}: {row_data}")
    print()
