#!/usr/bin/env python3
"""合并同一章节下分散的多个 tex 片段。

典型用途：
1. 多次 OCR / MinerU 生成了同一章节的不同 tex 文件；
2. 需要保留一份统一的 chapter / introduction 头部；
3. 将后续各 section 内容顺序拼接为一个最终章节文件。

说明：
- 新版章节正文会依赖主文件中的公共排版命令，例如
  ``\questionlist``、``\blanklist``、``\solutionlist``、
  ``\optionlist``、``\questionwithimage`` 等；
- 合并时仍兼容旧版 tex 中直接写出的 ``enumerate`` 结构。

输入：
- 一个或多个已经生成好的章节 tex 文件路径，顺序即最终拼接顺序；
- ``--chapter-name`` 指定合并后统一使用的 ``\chapter{}`` 名称；
- ``--output`` 可选，指定输出文件位置。

输出：
- 一个新的章节 tex 文件；
- 保留第一份文件的头部结构，并统一替换 ``\chapter{}``；
- 将后续文件的各题型 section 依次拼接到正文末尾；
- 如果后续文件开头出现“未分类题目其实是选择题续页”的场景，
  会自动并回上一个选择题 section，并同步修正题数。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CHAPTER_TEX_DIR = PROJECT_ROOT / "zhangz-book" / "chapter_tex"
SECTION_START_RE = re.compile(r"^\\section\*\{", re.MULTILINE)
CHAPTER_LINE_RE = re.compile(r"^\\chapter\{.*\}$", re.MULTILINE)
SECTION_TITLE_RE = re.compile(r"^\s*\\section\*\{(?P<title>[^}]*)\}\s*", re.MULTILINE)
SECTION_COUNT_RE = re.compile(r"(共\s*)(\d+)(\s*小题)")
LIST_TOKEN_RE = re.compile(
    r"\\begin\{enumerate\}"
    r"|\\end\{enumerate\}"
    r"|\\questionlist(?:\[[^]]*\])?"
    r"|\\blanklist(?:\[[^]]*\])?"
    r"|\\solutionlist(?:\[[^]]*\])?"
    r"|\\optionlist"
)
ITEM_TOKEN_RE = re.compile(r"\\item\b")
CHOICE_OPTION_ENUM_RE = re.compile(r"\\optionlist|\\begin\{enumerate\}\[label=\(\\Alph\*\)")
LEGACY_QUESTIONIMAGE_BLOCK_RE = re.compile(
    r"\n?\\(?:newcommand|providecommand)\{\\questionimage\}\[1\]\{%\s*"
    r".*?"
    r"\n\}",
    re.DOTALL,
)


def log(message: str) -> None:
    print(message, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数。

    这里的输入只有三类：
    1. ``tex_files``: 待合并的 tex 文件列表；
    2. ``--chapter-name``: 合并后统一章节名；
    3. ``--output``: 可选输出位置。
    """
    parser = argparse.ArgumentParser(description="合并同一章节下分散的多个 tex 文件。")
    parser.add_argument("tex_files", nargs="+", help="待合并的 tex 文件，按顺序传入。")
    parser.add_argument("--chapter-name", required=True, help="合并后统一使用的章节名称。")
    parser.add_argument("--output", help="输出 tex 路径。默认写入 zhangz-book/chapter_tex/<章节名称>.tex。")
    return parser


def split_tex_parts(content: str, path: Path) -> tuple[str, str]:
    """把单个章节 tex 拆成 header 和 body。

    输入：
    - ``content``: 完整 tex 文本；
    - ``path``: 当前文件路径，仅用于报错信息。

    输出：
    - ``header``: 从文件开头到第一个 ``\section*`` 之前的内容；
    - ``body``: 从第一个 ``\section*`` 开始到文件结尾的内容。

    约束：
    - 当前脚本默认每个章节文件都至少包含一个 ``\section*``；
    - header 中若仍残留旧版 ``\questionimage`` 宏，会在这里顺手剥掉，
      避免合并结果继续带着过时的局部公共组件。
    """
    section_match = SECTION_START_RE.search(content)
    if not section_match:
        raise ValueError(f"文件中未找到 section 起始位置：{path}")
    header = strip_legacy_shared_macros(content[: section_match.start()].rstrip())
    body = content[section_match.start() :].strip()
    if not header or not body:
        raise ValueError(f"文件结构不完整，无法拆分 header/body：{path}")
    return header, body


def replace_chapter_name(header: str, chapter_name: str) -> str:
    """把 header 中第一次出现的 ``\chapter{}`` 改成统一章节名。"""
    replaced, count = CHAPTER_LINE_RE.subn(rf"\\chapter{{{chapter_name}}}", header, count=1)
    if count == 0:
        raise ValueError("header 中未找到 \\chapter{...} 行。")
    return replaced


def normalize_output_path(output: str | None, chapter_name: str) -> Path:
    """标准化输出路径。

    - 如果用户显式传了 ``--output``，直接以它为准；
    - 否则默认写到 ``zhangz-book/chapter_tex/<章节名>.tex``。
    """
    if output:
        return Path(output).expanduser().resolve()
    return (DEFAULT_CHAPTER_TEX_DIR / f"{chapter_name}.tex").resolve()


def strip_legacy_shared_macros(header: str) -> str:
    """去掉旧版章节文件里内嵌的共享宏定义。

    当前只处理历史遗留的 ``\questionimage`` 定义。新版项目里这些公共
    命令已经统一放在主文件中，继续保留会导致：
    - 合并结果头部冗余；
    - 宏的来源分散，不利于后续维护。
    """
    stripped = LEGACY_QUESTIONIMAGE_BLOCK_RE.sub("", header)
    return stripped.rstrip()


def is_list_start_token(token: str) -> bool:
    return token != r"\end{enumerate}"


def find_outer_enumerate_bounds(content: str, start_pos: int = 0) -> tuple[int, int]:
    """找到最外层题目列表的起止范围。

    输入：
    - ``content``: 待搜索正文；
    - ``start_pos``: 搜索起点，通常从某个 ``\section*`` 标题之后开始。

    输出：
    - ``(begin, end)``: 最外层题目列表在字符串中的左右边界。

    兼容范围：
    - 旧版 ``\begin{enumerate} ... \end{enumerate}``
    - 新版 ``\questionlist / \blanklist / \solutionlist / \optionlist``
    """
    begin_match = None
    for match in LIST_TOKEN_RE.finditer(content, start_pos):
        if is_list_start_token(match.group()):
            begin_match = match
            break
    if begin_match is None:
        raise ValueError("未找到题目列表起始位置。")

    depth = 0
    for match in LIST_TOKEN_RE.finditer(content, begin_match.start()):
        if is_list_start_token(match.group()):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return begin_match.start(), match.end()
    raise ValueError("存在未闭合的题目列表环境。")


def count_top_level_items(content: str, initial_depth: int = 0) -> int:
    """统计某个题目列表里的“顶层题目数”。

    这里特意区分“顶层题目”与“内层选项”：
    - 选择题的 A/B/C/D 选项也会出现 ``\item``；
    - 但它们不应被误算成真正的题目数量。

    ``initial_depth`` 用于处理“已经切进列表内部”的场景，例如从外层列表
    中截出若干 ``\item`` 片段时，起始深度应视作 1。
    """
    token_re = re.compile(f"{LIST_TOKEN_RE.pattern}|{ITEM_TOKEN_RE.pattern}")
    depth = initial_depth
    count = 0
    for match in token_re.finditer(content):
        token = match.group()
        if ITEM_TOKEN_RE.fullmatch(token):
            if depth == 1:
                count += 1
            continue
        if is_list_start_token(token):
            depth += 1
        else:
            depth = max(0, depth - 1)
    return count


def increment_section_count(title: str, delta: int) -> str:
    if delta <= 0:
        return title
    match = SECTION_COUNT_RE.search(title)
    if not match:
        return title
    updated_count = int(match.group(2)) + delta
    return f"{title[:match.start(2)]}{updated_count}{title[match.end(2):]}"


def extract_leading_unclassified_choice_continuation(body: str) -> tuple[str, str, int] | None:
    """识别“未分类题目其实是选择题续页”的特殊场景。

    输入：
    - 某个待拼接 body。

    输出：
    - 若识别成功，返回 ``(续页题目块, 剩余正文, 续页新增题数)``；
    - 若不是该场景，返回 ``None``。

    识别条件：
    - 文件一开始是“未分类题目” section；
    - 该 section 的外层列表里实际包含选择题选项结构
      （旧版 ``enumerate[(A)]`` 或新版 ``\optionlist``）。
    """
    stripped = body.lstrip()
    title_match = SECTION_TITLE_RE.match(stripped)
    if not title_match:
        return None
    if "未分类题目" not in title_match.group("title"):
        return None

    enum_begin, enum_end = find_outer_enumerate_bounds(stripped, title_match.end())
    outer_enumerate = stripped[enum_begin:enum_end]
    if not CHOICE_OPTION_ENUM_RE.search(outer_enumerate):
        return None

    begin_line_end = stripped.find("\n", enum_begin)
    end_token_start = outer_enumerate.rfind(r"\end{enumerate}")
    if begin_line_end == -1 or end_token_start == -1:
        raise ValueError("未分类题目中的 enumerate 结构不完整。")

    end_token_start += enum_begin
    items = stripped[begin_line_end + 1 : end_token_start].strip()
    remainder = stripped[enum_end:].lstrip()
    item_count = count_top_level_items(items, initial_depth=1)
    return items, remainder, item_count


def merge_choice_continuation(existing_body: str, continuation_items: str, added_count: int) -> str:
    """把识别出的选择题续页并回前一个选择题 section。

    输入：
    - ``existing_body``: 目前已经累计好的正文；
    - ``continuation_items``: 需要并回去的若干 ``\item``；
    - ``added_count``: 新增题目数，用于修正标题里的“共 N 小题”。

    输出：
    - 一个新的正文字符串，其中：
      1. 最后一个“选择题” section 的题数已自动更新；
      2. 续页题目块已插入该 section 外层列表的结尾处。
    """
    last_choice_match = None
    for match in SECTION_TITLE_RE.finditer(existing_body):
        if "选择题" in match.group("title"):
            last_choice_match = match
    if last_choice_match is None:
        raise ValueError("检测到选择题续页，但前文未找到“选择题”章节。")

    updated_title = increment_section_count(last_choice_match.group("title"), added_count)
    if updated_title != last_choice_match.group("title"):
        existing_body = (
            existing_body[: last_choice_match.start("title")]
            + updated_title
            + existing_body[last_choice_match.end("title") :]
        )
        title_search_start = last_choice_match.start()
    else:
        title_search_start = last_choice_match.start()

    title_match = SECTION_TITLE_RE.search(existing_body, title_search_start)
    if title_match is None or "选择题" not in title_match.group("title"):
        raise ValueError("更新选择题章节标题后重新定位失败。")

    _, enum_end = find_outer_enumerate_bounds(existing_body, title_match.end())
    end_token_start = existing_body.rfind(r"\end{enumerate}", title_match.end(), enum_end)
    if end_token_start == -1:
        raise ValueError("选择题章节缺少外层 \\end{enumerate}。")

    continuation_block = continuation_items.strip()
    if not continuation_block:
        return existing_body
    return (
        existing_body[:end_token_start].rstrip()
        + "\n\n"
        + continuation_block
        + "\n\n"
        + existing_body[end_token_start:]
    )


def merge_body_parts(body_parts: list[str]) -> str:
    """按顺序合并多个 body。

    常规情况是简单顺序拼接；遇到“未分类题目其实是选择题续页”时，会先做
    续页折叠，再继续处理剩余正文。
    """
    merged_parts: list[str] = []
    for body in body_parts:
        working_body = body.strip()
        while merged_parts:
            continuation = extract_leading_unclassified_choice_continuation(working_body)
            if continuation is None:
                break
            items, working_body, item_count = continuation
            merged_parts[-1] = merge_choice_continuation(merged_parts[-1], items, item_count)
            if not working_body.strip():
                break
        if working_body.strip():
            merged_parts.append(working_body)
    return "\n\n".join(merged_parts)


def main() -> int:
    """脚本主流程。

    流程概览：
    1. 解析参数并校验输入文件是否存在；
    2. 逐个文件拆成 header/body；
    3. 保留第一份 header，并统一替换章节名；
    4. 合并所有 body，处理续页折叠；
    5. 将最终 tex 写入目标路径。
    """
    args = build_parser().parse_args()

    tex_paths = [Path(item).expanduser().resolve() for item in args.tex_files]
    for tex_path in tex_paths:
        if not tex_path.exists():
            raise FileNotFoundError(f"输入文件不存在：{tex_path}")

    header_parts: list[str] = []
    body_parts: list[str] = []

    for index, tex_path in enumerate(tex_paths):
        content = tex_path.read_text(encoding="utf-8")
        header, body = split_tex_parts(content, tex_path)
        if index == 0:
            header_parts.append(replace_chapter_name(header, args.chapter_name))
        body_parts.append(body)

    merged_body = merge_body_parts(body_parts)
    merged = "\n\n".join(part.strip() for part in header_parts + [merged_body] if part.strip()) + "\n"
    output_path = normalize_output_path(args.output, args.chapter_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(merged, encoding="utf-8")

    log(f"已合并 {len(tex_paths)} 个 tex 文件：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
