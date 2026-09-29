#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract detailed math content from Word doc using XML."""
from docx import Document
from docx.oxml.ns import qn, nsmap
from lxml import etree

doc = Document('Lossless Transmission for Geo-Distributed AI Computing via LSQ-Sketch and APN-Aware SRv6 Backpressure.docx')

# Math paragraphs indices: P25, P26, P27, P32, P33, P39, P40, P42, P46, P51
# Let's check all paragraphs for math and show full text + math XML

MATH_PARAS = [25, 26, 27, 32, 33, 39, 40, 42, 46, 51]

for idx in MATH_PARAS:
    para = doc.paragraphs[idx]
    text = para.text
    style = para.style.name if para.style else 'Normal'
    
    print(f"\n{'='*70}")
    print(f"P{idx} [{style}]")
    print(f"Plain text: {repr(text)}")
    print(f"-"*70)
    
    # Get full paragraph XML
    xml_str = etree.tostring(para._element, pretty_print=True, encoding='unicode')
    # Show only math related portions (m: namespace)
    # Actually let's extract all runs and see where math is
    runs_info = []
    for elem in para._element:
        tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        if tag == 'r':
            # Text run
            run_text = ''.join(t.text or '' for t in elem.iter(qn('w:t')))
            runs_info.append(('TEXT_RUN', run_text))
        elif tag in ('oMathPara', 'oMath'):
            # Math - extract all m:t elements and structure
            math_texts = [t.text for t in elem.iter(qn('m:t')) if t.text]
            # Also try to get a sense of structure - extract numerator, denominator etc
            struct = []
            for child in elem.iter():
                ctag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if ctag in ('f', 'rad', 'sSup', 'sSub', 'sSubSup', 'd', 'nary', 'eqArr'):
                    struct.append(ctag)
            runs_info.append(('MATH', {'texts': math_texts, 'struct': struct}))
        elif tag == 'hyperlink':
            pass
    
    for rtype, rdata in runs_info:
        if rtype == 'TEXT_RUN':
            if rdata.strip():
                print(f"  [TXT] {repr(rdata)}")
        else:
            print(f"  [MATH] texts={rdata['texts']}, structs={rdata['struct']}")

print("\nDone")
