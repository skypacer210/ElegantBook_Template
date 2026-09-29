#!/usr/bin/env python3

from __future__ import annotations

import re
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    source_dir = project_root / "tool" / "output" / "chuyi_xia_2"
    target_chapter_path = project_root / "zhangz-book" / "chapter_tex" / "12.11-4等边三角形探究.tex"
    chapter_title = "12.11-4 等边三角形探究"

    tex_files = sorted(source_dir.glob("chuyi_xia_2-p*-cleaned.tex"))
    if not tex_files:
        raise SystemExit(f"no tex files found in {source_dir}")

    section_re = re.compile(r"^\\section\*\{", re.M)
    parts: list[str] = []
    parts.append(f"\\chapter{{{chapter_title}}}\n")

    for tex_path in tex_files:
        stem = tex_path.stem
        match_page = re.search(r"p(\d{3})", stem)
        page_label = f"p{match_page.group(1)}" if match_page else stem

        raw = tex_path.read_text(encoding="utf-8")
        match_section = section_re.search(raw)
        body = raw[match_section.start() :] if match_section else raw
        body = section_re.sub(r"\\subsection*{", body)
        parts.append(f"\\section*{{{page_label}}}\n{body.strip()}\n")

    target_chapter_path.parent.mkdir(parents=True, exist_ok=True)
    target_chapter_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

