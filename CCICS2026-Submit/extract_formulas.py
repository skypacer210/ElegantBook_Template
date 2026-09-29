#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract paragraphs with formulas (originally Tables 1-5 in docx) from Word doc."""
from docx import Document
from docx.oxml.ns import qn

doc = Document('Lossless Transmission for Geo-Distributed AI Computing via LSQ-Sketch and APN-Aware SRv6 Backpressure.docx')

print("=== Full document paragraphs with their XML content (checking for math) ===")

for i, para in enumerate(doc.paragraphs):
    text = para.text.strip()
    style = para.style.name if para.style else 'Normal'
    
    # Check if paragraph has math (oMath or oMathPara elements)
    has_math = False
    math_texts = []
    for elem in para._element.iter():
        if elem.tag in (qn('m:oMath'), qn('m:oMathPara')):
            has_math = True
            # Try to extract text within math
            for t in elem.iter(qn('w:t')):
                if t.text:
                    math_texts.append(t.text)
    
    # Also check for non-breaking spaces or odd patterns
    if text or has_math:
        # Show first 120 chars
        display_text = text[:120]
        marker = " [MATH]" if has_math else ""
        if has_math and not text:
            display_text = " >>> MATH ONLY: " + " | ".join(math_texts)
        elif has_math and math_texts:
            marker += " >>> " + " | ".join(math_texts)
        print(f"[P{i:3d}] [{style:12s}]{marker}")
        print(f"       TEXT: {display_text}")
        print()
