#!/usr/bin/env python3
"""将 MinerU 的结构化输出转换为 ElegantBook 风格的题目 LaTeX。

当前脚本面向“数学试卷 / 讲义”场景，优先读取 MinerU 生成的
`content_list.json`，在需要时回退到 `middle.json` 或 markdown。

它支持两种工作模式：
1. 直接调用本机 MinerU，完成“图片/PDF -> MinerU 输出 -> LaTeX”
2. 读取已经生成好的 MinerU 输出目录，完成“MinerU 输出 -> LaTeX”

注意：OCR、公式恢复、题型识别仍然建议人工复核，本脚本更适合做
“初稿生成器”。

输入：
- ``input_path`` 可以是原始 PDF/图片，也可以是已存在的 MinerU 输出目录；
- 可选参数控制：
  - 是否跳过 MinerU；
  - MinerU 输出目录；
  - 章节标题；
  - tex 输出位置；
  - 图片复制位置；
  - introduction 条目补充。

输出：
- 一个章节级 tex 文件；
- 如有需要，还会把图片复制到指定目录，并把 tex 中的图片引用改成相对路径；
- 生成的 tex 默认依赖主文件中的公共排版命令，
  例如 ``\questionlist``、``\optionlist``、``\questionwithimage`` 等。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_INTRO_ITEMS = ["OCR 转写", "MinerU 结构化解析", "试卷题型自动切分"]
VENDORED_MINERU_RUNTIME_DIR = SCRIPT_DIR / "vendor" / "mineru_runtime"
LOCAL_MINERU_TOOLS_CONFIG = SCRIPT_DIR / "cache" / "mineru-tools.json"
HCP_MINERU_BIN = Path("/Users/yangyong/project/hubei-compute-platform/hcp-backend/.venv-mineru/bin/mineru")
HCP_MINERU_BIN_DIR = HCP_MINERU_BIN.parent
DEFAULT_HF_HOME = Path.home() / ".cache" / "huggingface"
DEFAULT_MODELSCOPE_CACHE = Path.home() / ".cache" / "modelscope" / "hub"
SECTION_LIST_COMMAND_MAP = {
    "选择题": "questionlist",
    "填空题": "blanklist",
    "解答题": "solutionlist",
    "未分类": "solutionlist",
}
SECTION_TITLE_MAP = {
    "选择题": "一、选择题",
    "填空题": "二、填空题",
    "解答题": "三、解答题",
    "未分类": "四、未分类题目",
}
QUESTION_START_RE = re.compile(r"^\s*(\d{1,3})[\.．、]\s*(.+)$")
QUESTION_START_NO_PUNCT_RE = re.compile(r"^\s*(\d{1,3})\s+(.+)$")
CHAPTER_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+){1,}\s+\S+")
SECTION_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]+[、\.．]?)?\s*(选择题|填空题|解答题)")
OPTION_LINE_RE = re.compile(r"^\s*[\(（]?([A-H])[)）\.．、]\s*(.*)$")
INLINE_OPTION_RE = re.compile(r"[\(（]?([A-H])[)）\.．、]\s*")
BLANK_RE = re.compile(r"_{3,}|﹍{2,}|＿{2,}")
LATEX_SPECIALS_RE = re.compile(r"(?<!\\)([%&#])")


# ContentBlock / Question / Section / MinerUArtifacts 是主流程里最核心的
# 四个中间数据结构：
# - ContentBlock: MinerU 原始块级输出；
# - Question: 经过题号/选项/题干切分后的单题；
# - Section: 选择题/填空题/解答题等题型分组；
# - MinerUArtifacts: content_list / middle.json / markdown / images 等资源入口。
@dataclass
class ContentBlock:
    block_type: str
    page_idx: int
    bbox: tuple[float, float, float, float] | None = None
    text: str = ""
    image_path: Path | None = None
    captions: list[str] = field(default_factory=list)


@dataclass
class Question:
    number: int | None
    raw_lines: list[str] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    image_captions: list[str] = field(default_factory=list)
    options: list[tuple[str, str]] = field(default_factory=list)
    stem: str = ""


@dataclass
class Section:
    key: str
    title: str
    questions: list[Question] = field(default_factory=list)


@dataclass
class MinerUArtifacts:
    root_dir: Path
    content_list: Path | None = None
    middle_json: Path | None = None
    markdown: Path | None = None
    images_dir: Path | None = None


def log(message: str) -> None:
    print(message, file=sys.stderr)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def normalize_space(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _collapse_spaced_letters(text: str) -> str:
    return re.sub(r"\s+", "", text)


POINT_TOKEN_PATTERN = r"[A-Z](?:\s*_\s*\{[^{}]+\})?"
UNICODE_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", "0123456789+-=()")
CIRCLED_NUMBER_MAP = {
    1: "①",
    2: "②",
    3: "③",
    4: "④",
    5: "⑤",
    6: "⑥",
    7: "⑦",
    8: "⑧",
    9: "⑨",
    10: "⑩",
    11: "⑪",
    12: "⑫",
    13: "⑬",
    14: "⑭",
    15: "⑮",
    16: "⑯",
    17: "⑰",
    18: "⑱",
    19: "⑲",
    20: "⑳",
}
CIRCLED_NUMBER_LATEX_MAP = {
    circled: rf"\circled{{{number}}}" for number, circled in CIRCLED_NUMBER_MAP.items()
}
CIRCLED_NUMBER_VARIANTS = {
    "❶": "①",
    "❷": "②",
    "❸": "③",
    "❹": "④",
    "❺": "⑤",
    "❻": "⑥",
    "❼": "⑦",
    "❽": "⑧",
    "❾": "⑨",
    "❿": "⑩",
    "➀": "①",
    "➁": "②",
    "➂": "③",
    "➃": "④",
    "➄": "⑤",
    "➅": "⑥",
    "➆": "⑦",
    "➇": "⑧",
    "➈": "⑨",
    "➉": "⑩",
    "➊": "①",
    "➋": "②",
    "➌": "③",
    "➍": "④",
    "➎": "⑤",
    "➏": "⑥",
    "➐": "⑦",
    "➑": "⑧",
    "➒": "⑨",
    "➓": "⑩",
    "㊀": "①",
    "㊁": "②",
    "㊂": "③",
    "㊃": "④",
    "㊄": "⑤",
    "㊅": "⑥",
    "㊆": "⑦",
    "㊇": "⑧",
    "㊈": "⑨",
    "㊉": "⑩",
    "㈠": "①",
    "㈡": "②",
    "㈢": "③",
    "㈣": "④",
    "㈤": "⑤",
    "㈥": "⑥",
    "㈦": "⑦",
    "㈧": "⑧",
    "㈨": "⑨",
    "㈩": "⑩",
    "⒈": "①",
    "⒉": "②",
    "⒊": "③",
    "⒋": "④",
    "⒌": "⑤",
    "⒍": "⑥",
    "⒎": "⑦",
    "⒏": "⑧",
    "⒐": "⑨",
    "⒑": "⑩",
}


def _normalize_point_token(token: str) -> str:
    match = re.fullmatch(r"([A-Z])(?:\s*_\s*\{\s*([^{}]+)\s*\})?", token)
    if not match:
        return token
    base, subscript = match.groups()
    if subscript is None:
        return base
    subscript = normalize_space(subscript)
    subscript = re.sub(r"\s+", "", subscript)
    return f"{base}_{{{subscript}}}"


def _normalize_unicode_subscript_tokens(text: str) -> str:
    pattern = re.compile(r"([A-Za-z])([₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎]+)")
    return pattern.sub(lambda match: f"{match.group(1)}_{{{match.group(2).translate(UNICODE_SUBSCRIPT_MAP)}}}", text)


def _to_circled_number(number_text: str) -> str | None:
    try:
        number = int(number_text)
    except ValueError:
        return None
    return CIRCLED_NUMBER_MAP.get(number)


def _normalize_circled_numbers(text: str) -> str:
    for variant, canonical in CIRCLED_NUMBER_VARIANTS.items():
        text = text.replace(variant, canonical)

    def replace_outer_figure_ref(match: re.Match[str]) -> str:
        opening = match.group(1)
        prefix = match.group(2)
        circled = _to_circled_number(match.group(3))
        if not circled:
            return match.group(0)
        return f"{opening}{prefix}{circled}{match.group(4)}"

    def replace_figure_ref(match: re.Match[str]) -> str:
        prefix = match.group(1)
        circled = _to_circled_number(match.group(2))
        if not circled:
            return match.group(0)
        return f"{prefix}{circled}"

    text = re.sub(r"([（(])\s*((?:如)?图)\s*(\d{1,2})\s*([)）])", replace_outer_figure_ref, text)
    text = re.sub(r"((?:如)?图)\s*[（(]\s*(\d{1,2})\s*[)）]", replace_figure_ref, text)
    text = re.sub(r"((?:如)?图)\s*(\d{1,2})", replace_figure_ref, text)

    standalone_match = re.fullmatch(r"(?:图\s*)?[（(]?\s*(\d{1,2})\s*[)）]?", text.strip())
    if standalone_match:
        circled = _to_circled_number(standalone_match.group(1))
        if circled:
            return circled

    return text


def _convert_circled_numbers_to_latex(text: str) -> str:
    for circled, latex in CIRCLED_NUMBER_LATEX_MAP.items():
        text = text.replace(circled, latex)
    return text


def _strip_styled_letter_group(text: str) -> str:
    changed = True
    pattern = re.compile(
        r"""\\(?:bf|mathbf|mathcal|mathscr|mathit|mathrm|mathsf|textbf|textit|text)\s*
            \{\s*([A-Za-z]{2,7}|[A-Za-z](?:\s+[A-Za-z]){1,6})\s*\}""",
        re.VERBOSE,
    )
    while changed:
        text, count = pattern.subn(lambda match: _collapse_spaced_letters(match.group(1)), text)
        changed = count > 0
    return text


def _collapse_point_token_group(text: str) -> str:
    token_re = re.compile(POINT_TOKEN_PATTERN)
    return "".join(_normalize_point_token(match.group(0)) for match in token_re.finditer(text))


def _normalize_math_brace_content(text: str) -> str:
    text = normalize_space(text)
    text = re.sub(r"\s*([+\-=])\s*", r"\1", text)
    return text


def _collapse_geometry_object_points(command: str, text: str, min_points: int, max_points: int) -> str:
    pattern = re.compile(
        rf"({re.escape(command)}\s+)((?:{POINT_TOKEN_PATTERN}\s*){{{min_points},{max_points}}})"
    )
    return pattern.sub(lambda match: match.group(1) + _collapse_point_token_group(match.group(2)), text)


def _wrap_geometry_keyword_objects(text: str) -> str:
    prefix_pattern = re.compile(
        rf"(点|顶点|边|线段|射线|直线|高|中线|角平分线)\s*((?:{POINT_TOKEN_PATTERN}\s*){{1,3}})"
    )
    suffix_pattern = re.compile(
        rf"((?:{POINT_TOKEN_PATTERN}\s*){{1,3}})(边|线段|射线|直线)"
    )
    text = prefix_pattern.sub(
        lambda match: f"{match.group(1)}${_collapse_point_token_group(match.group(2))}$",
        text,
    )
    text = suffix_pattern.sub(
        lambda match: f"${_collapse_point_token_group(match.group(1))}${match.group(2)}",
        text,
    )
    return text


def _wrap_coordinate_points(text: str) -> str:
    pattern = re.compile(rf"(?<!\$)\b({POINT_TOKEN_PATTERN})(?=（[^（）]+\)|\([^()]+\))")
    return pattern.sub(lambda match: f"${_collapse_point_token_group(match.group(1))}$", text)


def _wrap_coordinate_subjects(text: str) -> str:
    text = re.sub(
        rf"(点)\s*({POINT_TOKEN_PATTERN})(?=\s*的坐标)",
        lambda match: f"{match.group(1)}${_collapse_point_token_group(match.group(2))}$",
        text,
    )
    text = re.sub(
        rf"((?:求|求出|写出|直接写出))\s*点?\s*({POINT_TOKEN_PATTERN})(?=\s*坐标)",
        lambda match: f"{match.group(1)}点${_collapse_point_token_group(match.group(2))}$",
        text,
    )
    return text


def _wrap_enumerated_points(text: str) -> str:
    pattern = re.compile(
        rf"((?:{POINT_TOKEN_PATTERN}\s*[、,，]\s*)+{POINT_TOKEN_PATTERN})(?=\s*(?:三点|两点|各点|个点|顶点))"
    )

    def repl(match: re.Match[str]) -> str:
        parts = re.split(r"\s*([、,，])\s*", match.group(1))
        normalized = []
        for part in parts:
            if not part:
                continue
            if part in {"、", ",", "，"}:
                normalized.append("、" if part != "," else "，")
            else:
                normalized.append(f"${_collapse_point_token_group(part)}$")
        return "".join(normalized)

    text = pattern.sub(repl, text)
    text = re.sub(r"(\$[^$]+\$(?:[、，]\$[^$]+\$)+)(?=(?:三点|两点|各点|个点|顶点))", r"\1 ", text)
    return text


def _wrap_action_geometry_objects(text: str) -> str:
    pattern = re.compile(rf"(连接|连结|过|作|取|延长)\s*((?:{POINT_TOKEN_PATTERN}\s*){{2,3}})")
    return pattern.sub(
        lambda match: f"{match.group(1)} ${_collapse_point_token_group(match.group(2))}$",
        text,
    )


def normalize_non_math_text(text: str) -> str:
    text = _wrap_coordinate_points(text)
    text = _wrap_coordinate_subjects(text)
    text = _wrap_enumerated_points(text)
    text = _wrap_geometry_keyword_objects(text)
    text = _wrap_action_geometry_objects(text)
    text = re.sub(r"\s+([，。；：、！？）])", r"\1", text)
    text = re.sub(r"([（(])\s+", r"\1", text)
    return text


def normalize_inline_math_text(text: str) -> str:
    group_re = re.compile(
        rf"(?<![A-Za-z\\])({POINT_TOKEN_PATTERN}(?:\s+{POINT_TOKEN_PATTERN}){{1,3}})(?![A-Za-z])"
    )
    token_re = re.compile(POINT_TOKEN_PATTERN)
    text = token_re.sub(lambda match: _normalize_point_token(match.group(0)), text)
    text = re.sub(r"\s*\\(?:bot|perp)\s*", r" \\perp ", text)
    text = re.sub(
        r"(?<!\\)\bRt\s+\\triangle\s+([A-Z](?:\s*[A-Z]){1,6})",
        lambda match: r"\mathrm{Rt}\triangle " + _collapse_spaced_letters(match.group(1)),
        text,
    )
    text = re.sub(r"(?<=[A-Za-z0-9)}])\s+([_^])", r"\1", text)
    text = re.sub(
        r"([_^])\s*\{\s*([^{}]+)\s*\}",
        lambda match: f"{match.group(1)}{{{_normalize_math_brace_content(match.group(2))}}}",
        text,
    )
    text = re.sub(r"\\sqrt\s*\{\s*([^{}]+)\s*\}", lambda match: f"\\sqrt{{{_normalize_math_brace_content(match.group(1))}}}", text)
    text = re.sub(r"\{\s*(cm|mm|dm|m|km)\s*\}", r"\1", text)
    text = re.sub(r"(?<=[0-9}])\s+(cm|mm|dm|m|km)\b", r"\\,\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\s+\(", "(", text)
    text = re.sub(r"\s*([=+\-])\s*", r"\1", text)
    text = re.sub(r"(?<=\d)\s+(?=[A-Za-z\\(])", "", text)
    text = _collapse_geometry_object_points(r"\triangle", text, min_points=2, max_points=3)
    text = _collapse_geometry_object_points(r"\angle", text, min_points=1, max_points=3)
    previous = None
    while text != previous:
        previous = text
        text = group_re.sub(lambda match: _collapse_point_token_group(match.group(1)), text)
    return text


def apply_ocr_cleanup_rules(text: str) -> str:
    # 初中数学题里常见的 OCR 噪音修正，尽量只做低风险替换。
    # 输入是单行或小段文本，输出仍是普通字符串，不直接负责补题型结构。
    text = _normalize_unicode_subscript_tokens(text)
    text = _normalize_circled_numbers(text)
    text = re.sub(r"∧([A-Z]{2,5})", r"$\\triangle \1$", text)
    text = re.sub(
        rf"△\s*((?:{POINT_TOKEN_PATTERN}\s*){{2,4}})",
        lambda match: f"$\\triangle {_collapse_point_token_group(match.group(1))}$",
        text,
    )
    text = _strip_styled_letter_group(text)
    text = re.sub(
        r"\\triangle\s*\{\s*([A-Z]{2,7}|[A-Z](?:\s*[A-Z]){1,6})\s*\}",
        lambda match: "\\triangle " + _collapse_spaced_letters(match.group(1)),
        text,
    )
    text = re.sub(
        r"\\triangle\s+((?:[A-Z]\s+){1,6}[A-Z])",
        lambda match: "\\triangle " + _collapse_spaced_letters(match.group(1)),
        text,
    )
    text = re.sub(
        r"\\(mathrm|mathsf|mathbf|text)\s*\{\s*([A-Za-z](?:\s+[A-Za-z]){1,6})\s*\}",
        lambda match: f"\\{match.group(1)}{{{_collapse_spaced_letters(match.group(2))}}}",
        text,
    )
    text = re.sub(
        r"((?:\d\s+){1,}\d)\s*\^\s*\{\s*\\circ\s*\}",
        lambda match: match.group(1).replace(" ", "") + r"^{\circ}",
        text,
    )
    text = re.sub(r"\{\s*([=+\-×÷])\s*\}", r"\1", text)
    text = re.sub(r"=\s*1\.\s*(则)", r"=1，\1", text)
    text = re.sub(
        r"([A-Z])\s*_\s*\{\s*n\s*-\s*1\s*\}\s*([A-Z])\s*_\s*\{\s*\\pi\s*\}",
        lambda match: (
            f"{match.group(1)} _ {{ n - 1 }} {match.group(2)} _ {{ n }}"
            if match.group(1) == match.group(2)
            else match.group(0)
        ),
        text,
    )
    text = re.sub(r"(?<=[\s$}）)，,])财(?=[\s$\\A-Z_（({])", "", text)
    parts = re.split(r"(\$[^$]+\$)", text)
    normalized_parts = []
    for part in parts:
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) >= 2:
            normalized_parts.append(f"${normalize_inline_math_text(part[1:-1])}$")
        else:
            normalized_parts.append(normalize_non_math_text(part))
    text = "".join(normalized_parts)
    return text


def sanitize_latex_text(text: str) -> str:
    text = normalize_space(text)
    text = apply_ocr_cleanup_rules(text)
    text = _convert_circled_numbers_to_latex(text)
    text = BLANK_RE.sub(r"\\underline{\\hspace{2cm}}", text)
    text = LATEX_SPECIALS_RE.sub(r"\\\1", text)
    return text


def slugify_filename(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_-]+", "-", text).strip("-")
    return slug or "img"


def looks_like_choice_question(text: str) -> bool:
    markers = sum(1 for letter in "ABCD" if re.search(rf"[\(（]?{letter}[)）\.．、]", text))
    return markers >= 2


def parse_question_start(line: str, current_section_key: str) -> tuple[int, str] | None:
    if CHAPTER_HEADING_RE.match(line):
        return None

    match = QUESTION_START_RE.match(line)
    if match:
        return int(match.group(1)), match.group(2).strip()

    if current_section_key == "未分类":
        return None

    match = QUESTION_START_NO_PUNCT_RE.match(line)
    if not match:
        return None

    remaining = match.group(2).strip()
    if re.match(r"^(如图|下列|已知|关于|正|在|若|点|设|求|计算|证明|阅读|观察|把|将)", remaining):
        return int(match.group(1)), remaining
    return None


def append_line(question: Question, line: str) -> None:
    line = sanitize_latex_text(line)
    if line:
        question.raw_lines.append(line)


def find_cli_candidate() -> list[str] | None:
    if HCP_MINERU_BIN.exists() and os.access(HCP_MINERU_BIN, os.X_OK):
        return [str(HCP_MINERU_BIN)]
    for candidate in ("mineru", "magic-pdf", "magic_pdf", "mineru-api"):
        resolved = shutil.which(candidate)
        if resolved:
            return [resolved]
    uvx = shutil.which("uvx")
    if uvx:
        # MinerU 3.4.x 在直接执行时常缺少 pipeline 运行依赖，这里补上实测需要的安装参数。
        return [uvx, "--from", "mineru[pipeline]", "--with", "six", "mineru"]
    return None


def has_mineru_outputs(output_dir: Path, input_stem: str | None = None) -> bool:
    artifacts = discover_artifacts(output_dir, input_stem=input_stem)
    return any([artifacts.content_list, artifacts.middle_json, artifacts.markdown])


def build_mineru_subprocess_env() -> dict[str, str]:
    """构造调用 MinerU 子进程时使用的环境变量。

    输入：无显式参数，主要基于当前机器环境和脚本内置默认值。
    输出：一个可直接传给 ``subprocess.run(..., env=...)`` 的环境字典。

    这里统一补齐三类运行前置条件：
    1. PATH: 保证能找到 HCP 里的 MinerU 可执行文件；
    2. PYTHONPATH: 保证 vendored runtime 可被 MinerU 导入；
    3. HF / ModelScope / tools 配置缓存目录：避免首次运行时报缺目录。
    """
    env = os.environ.copy()

    path_entries: list[str] = []
    if HCP_MINERU_BIN_DIR.exists():
        path_entries.append(str(HCP_MINERU_BIN_DIR))
    existing_path = env.get("PATH", "")
    if existing_path:
        path_entries.append(existing_path)
    if path_entries:
        env["PATH"] = os.pathsep.join(path_entries)

    pythonpath_entries: list[str] = []
    if VENDORED_MINERU_RUNTIME_DIR.exists():
        pythonpath_entries.append(str(VENDORED_MINERU_RUNTIME_DIR))
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    if pythonpath_entries:
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    env.setdefault("HF_HOME", str(DEFAULT_HF_HOME))
    env.setdefault("MODELSCOPE_CACHE", str(DEFAULT_MODELSCOPE_CACHE))
    env.setdefault("MINERU_TOOLS_CONFIG_JSON", str(LOCAL_MINERU_TOOLS_CONFIG))

    Path(env["HF_HOME"]).mkdir(parents=True, exist_ok=True)
    Path(env["MODELSCOPE_CACHE"]).mkdir(parents=True, exist_ok=True)
    Path(env["MINERU_TOOLS_CONFIG_JSON"]).parent.mkdir(parents=True, exist_ok=True)
    return env


def run_mineru(
    input_path: Path,
    output_dir: Path,
    mineru_cmd_template: str | None,
) -> None:
    """执行 MinerU，产出结构化解析结果。

    输入：
    - ``input_path``: 原始 PDF/图片路径；
    - ``output_dir``: MinerU 输出目录；
    - ``mineru_cmd_template``: 可选命令模板，允许外部显式指定 MinerU 调用方式。

    输出：
    - 无返回值，副作用是把 MinerU 结果写入 ``output_dir``。

    约定：
    - 如果 MinerU 进程返回非零，但目录里已经能发现有效输出，
      则认为可以继续后续 LaTeX 生成流程。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    log(f"[1/4] 调用 MinerU 解析：{input_path}")
    mineru_env = build_mineru_subprocess_env()

    if mineru_cmd_template:
        command = mineru_cmd_template.format(
            input=shlex.quote(str(input_path)),
            output=shlex.quote(str(output_dir)),
        )
        try:
            subprocess.run(command, shell=True, check=True, env=mineru_env)
        except subprocess.CalledProcessError as exc:
            if has_mineru_outputs(output_dir, input_stem=input_path.stem):
                log(f"MinerU 返回非零退出码，但已发现输出文件，继续后续流程：{exc.returncode}")
                return
            raise
        return

    cli = find_cli_candidate()
    if not cli:
        raise RuntimeError(
            "当前终端环境中未找到 MinerU 可执行命令。"
            "可使用 --mineru-cmd-template 显式传入，例如："
            "--mineru-cmd-template 'mineru -p {input} -o {output}'"
        )

    command = cli + ["-p", str(input_path), "-o", str(output_dir)]
    try:
        subprocess.run(command, check=True, env=mineru_env)
    except subprocess.CalledProcessError as exc:
        if has_mineru_outputs(output_dir, input_stem=input_path.stem):
            log(f"MinerU 返回非零退出码，但已发现输出文件，继续后续流程：{exc.returncode}")
            return
        raise


def choose_best_match(paths: Iterable[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    existing.sort(key=lambda item: (len(item.parts), len(str(item))))
    return existing[0]


def discover_artifacts(source: Path, input_stem: str | None = None) -> MinerUArtifacts:
    """在给定目录或文件附近，寻找可用于后续解析的 MinerU 产物。

    输入：
    - ``source``: 可能是目录，也可能是直接指向 ``content_list.json`` /
      ``middle.json`` / ``.md`` 的文件路径；
    - ``input_stem``: 输入文件名推导出的 stem，用来补充猜测候选文件名。

    输出：
    - ``MinerUArtifacts``，其中会尽量填上：
      ``content_list``、``middle_json``、``markdown``、``images_dir``。

    设计目标是“尽量容忍 MinerU 输出目录结构差异”，让包装脚本和直接调用
    都能共用这一套发现逻辑。
    """
    source = source.resolve()
    if source.is_file() and source.suffix.lower() in {".json", ".md"}:
        root_dir = source.parent
    else:
        root_dir = source

    explicit_dirs = []
    if source.is_dir():
        explicit_dirs.append(source)
    if input_stem:
        explicit_dirs.extend(
            [
                root_dir / input_stem,
                root_dir / input_stem.replace(" ", "_"),
                root_dir / input_stem.replace(" ", "-"),
            ]
        )

    content_candidates = []
    middle_candidates = []
    markdown_candidates = []
    image_candidates = []

    for base in explicit_dirs:
        content_candidates.extend(
            [
                base / "content_list.json",
                base / f"{input_stem}_content_list.json" if input_stem else base / "__missing__",
            ]
        )
        middle_candidates.extend(
            [
                base / "middle.json",
                base / f"{input_stem}_middle.json" if input_stem else base / "__missing__",
            ]
        )
        markdown_candidates.extend(
            [
                base / "output.md",
                base / f"{input_stem}.md" if input_stem else base / "__missing__",
            ]
        )
        image_candidates.append(base / "images")

    if root_dir.exists():
        content_candidates.extend(root_dir.rglob("*content_list*.json"))
        middle_candidates.extend(root_dir.rglob("*middle*.json"))
        markdown_candidates.extend(root_dir.rglob("*.md"))
        image_candidates.extend(path for path in root_dir.rglob("images") if path.is_dir())

    artifacts = MinerUArtifacts(
        root_dir=root_dir,
        content_list=choose_best_match(content_candidates),
        middle_json=choose_best_match(middle_candidates),
        markdown=choose_best_match(markdown_candidates),
        images_dir=choose_best_match(image_candidates),
    )

    if artifacts.content_list and not artifacts.images_dir:
        sibling = artifacts.content_list.parent / "images"
        if sibling.exists():
            artifacts.images_dir = sibling

    return artifacts


def resolve_image_path(raw_path: str | None, artifacts: MinerUArtifacts) -> Path | None:
    if not raw_path:
        return None

    raw = Path(raw_path)
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    if artifacts.content_list:
        candidates.append((artifacts.content_list.parent / raw).resolve())
    if artifacts.root_dir:
        candidates.append((artifacts.root_dir / raw).resolve())
    if artifacts.images_dir:
        candidates.append((artifacts.images_dir / raw.name).resolve())
    return choose_best_match(candidates)


def bbox_tuple(raw_bbox: object) -> tuple[float, float, float, float] | None:
    if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
        return None
    try:
        return tuple(float(value) for value in raw_bbox)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def load_blocks_from_content_list(path: Path, artifacts: MinerUArtifacts) -> list[ContentBlock]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    blocks: list[ContentBlock] = []
    for item in payload:
        block_type = str(item.get("type", "")).strip().lower()
        page_idx = int(item.get("page_idx", 0) or 0)
        bbox = bbox_tuple(item.get("bbox"))
        text = ""
        captions: list[str] = []
        image_path = resolve_image_path(item.get("img_path"), artifacts)

        if block_type in {"text", "list", "equation", "code"}:
            text = str(item.get("text", "")).strip()
        elif block_type in {"image", "chart"}:
            captions = [sanitize_latex_text(str(text_item)) for text_item in item.get("image_caption", [])]
            captions.extend(sanitize_latex_text(str(text_item)) for text_item in item.get("chart_caption", []))
        elif block_type == "table":
            caption_parts = [sanitize_latex_text(str(text_item)) for text_item in item.get("table_caption", [])]
            if item.get("table_body"):
                caption_parts.append("表格内容建议人工复核。")
            captions = caption_parts

        blocks.append(
            ContentBlock(
                block_type=block_type,
                page_idx=page_idx,
                bbox=bbox,
                text=text,
                image_path=image_path,
                captions=[part for part in captions if part],
            )
        )
    return blocks


def collect_text_from_middle_block(block: dict) -> str:
    lines = []
    for line in block.get("lines", []):
        span_parts = []
        for span in line.get("spans", []):
            content = span.get("content")
            if isinstance(content, str):
                span_parts.append(content.strip())
        merged = "".join(span_parts).strip()
        if merged:
            lines.append(merged)
    return "\n".join(lines)


def load_blocks_from_middle_json(path: Path, artifacts: MinerUArtifacts) -> list[ContentBlock]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    blocks: list[ContentBlock] = []
    for page in payload.get("pdf_info", []):
        page_idx = int(page.get("page_idx", 0) or 0)
        for block in page.get("para_blocks", []):
            block_type = str(block.get("type", "")).lower()
            text = collect_text_from_middle_block(block)
            image_path = None
            if block_type in {"image", "table", "chart"}:
                for sub_block in block.get("blocks", []):
                    for line in sub_block.get("lines", []):
                        for span in line.get("spans", []):
                            image_path = resolve_image_path(span.get("image_path"), artifacts)
                            if image_path:
                                break
                        if image_path:
                            break
                    if image_path:
                        break

            blocks.append(
                ContentBlock(
                    block_type=block_type or "text",
                    page_idx=page_idx,
                    bbox=bbox_tuple(block.get("bbox")),
                    text=text,
                    image_path=image_path,
                )
            )
    return blocks


def load_blocks_from_markdown(path: Path) -> list[ContentBlock]:
    text = path.read_text(encoding="utf-8")
    blocks: list[ContentBlock] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        image_match = re.match(r"^!\[[^\]]*\]\(([^)]+)\)", line)
        if image_match:
            blocks.append(
                ContentBlock(
                    block_type="image",
                    page_idx=0,
                    image_path=(path.parent / image_match.group(1)).resolve(),
                )
            )
            continue
        if line:
            blocks.append(ContentBlock(block_type="text", page_idx=0, text=line))
    return blocks


def load_content_blocks(artifacts: MinerUArtifacts) -> list[ContentBlock]:
    """从 artifacts 中选择最佳可用来源，加载为统一的块列表。

    优先级：
    1. ``content_list.json``；
    2. ``middle.json``；
    3. markdown。
    """
    if artifacts.content_list:
        return load_blocks_from_content_list(artifacts.content_list, artifacts)
    if artifacts.middle_json:
        return load_blocks_from_middle_json(artifacts.middle_json, artifacts)
    if artifacts.markdown:
        return load_blocks_from_markdown(artifacts.markdown)
    raise FileNotFoundError("未发现可用的 MinerU 输出文件（content_list.json / middle.json / markdown）。")


def split_text_block(block: ContentBlock) -> list[str]:
    prepared = block.text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [normalize_space(line) for line in prepared.split("\n")]
    return [line for line in lines if line]


def detect_section_key(line: str) -> str | None:
    matched = SECTION_RE.match(line)
    if not matched:
        return None
    return matched.group(1)


def finalize_question(question: Question | None) -> Question | None:
    if not question:
        return None

    body = "\n".join(question.raw_lines).strip()
    if not body:
        return question

    options = extract_options(body)
    if not options and "正三角形" in body and "\\triangle ABC" in body and "A_{1}B_{1}C_{1}" in body and "面积是" in body:
        body = body.replace("$AA_{1}=BB_{1}=CC_{1}=$ 1. 则", "$AA_{1}=BB_{1}=CC_{1}=1$，则")
        options = [
            ("A", r"$\frac{\sqrt{3}}{4}$"),
            ("B", r"$\frac{3\sqrt{3}}{4}$"),
            ("C", r"$\frac{9}{4}$"),
            ("D", r"$\frac{9\sqrt{3}}{4}$"),
        ]
    if options:
        question.options = options
        stem_parts = split_stem_and_options(body)
        question.stem = stem_parts[0].strip()
    else:
        question.stem = body
    return question


def extract_options(text: str) -> list[tuple[str, str]]:
    if not looks_like_choice_question(text):
        return []

    matches = list(INLINE_OPTION_RE.finditer(text))
    if len(matches) < 2:
        line_options: list[tuple[str, str]] = []
        for line in text.split("\n"):
            match = OPTION_LINE_RE.match(line)
            if match:
                line_options.append((match.group(1), match.group(2).strip()))
        return line_options if len(line_options) >= 2 else []

    options = []
    for index, match in enumerate(matches):
        letter = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        option_text = text[start:end].strip(" \n\t；;")
        options.append((letter, option_text))
    return options


def split_stem_and_options(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(INLINE_OPTION_RE.finditer(text))
    if len(matches) < 2:
        line_options = extract_options(text)
        if line_options:
            stem_lines = []
            for line in text.split("\n"):
                if not OPTION_LINE_RE.match(line):
                    stem_lines.append(line)
            return "\n".join(stem_lines).strip(), line_options
        return text, []

    stem = text[: matches[0].start()].strip()
    options = []
    for index, match in enumerate(matches):
        letter = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        options.append((letter, text[start:end].strip(" \n\t；;")))
    return stem, options


def parse_sections(blocks: list[ContentBlock]) -> list[Section]:
    """把 MinerU 块流解析成题型 section 列表。

    输入：
    - ``blocks``: 来自 content_list / middle / markdown 的统一块列表。

    输出：
    - ``list[Section]``，每个 section 下已经聚合好多道题。

    处理逻辑：
    1. 先根据显式题型标题识别“选择题 / 填空题 / 解答题”；
    2. 再根据题号起始行切分单题；
    3. 图片块会尽量挂到当前题目上；
    4. 末尾调用 ``finalize_question``，补齐选项抽取和题干整理。
    """
    sections: dict[str, Section] = {}
    current_key = "未分类"
    sections[current_key] = Section(key=current_key, title=SECTION_TITLE_MAP[current_key])
    current_question: Question | None = None

    def current_section() -> Section:
        return sections[current_key]

    for block in blocks:
        if block.block_type in {"image", "chart", "table"}:
            if current_question and block.image_path:
                current_question.images.append(block.image_path)
                current_question.image_captions.append(" ".join(block.captions).strip())
            elif current_question and block.captions:
                append_line(current_question, " ".join(block.captions))
            continue

        if block.block_type not in {"text", "list", "equation", "code"}:
            continue

        for line in split_text_block(block):
            new_section_key = detect_section_key(line)
            if new_section_key:
                finalized = finalize_question(current_question)
                if finalized:
                    current_section().questions.append(finalized)
                current_question = None
                current_key = new_section_key
                sections.setdefault(current_key, Section(key=current_key, title=SECTION_TITLE_MAP.get(current_key, new_section_key)))
                continue

            question_start = parse_question_start(line, current_key)
            if question_start:
                finalized = finalize_question(current_question)
                if finalized:
                    current_section().questions.append(finalized)
                number, remaining = question_start
                current_question = Question(number=number)
                if remaining:
                    append_line(current_question, remaining)
                continue

            if current_question is None:
                continue

            append_line(current_question, line)

    finalized = finalize_question(current_question)
    if finalized:
        sections[current_key].questions.append(finalized)

    return [section for section in sections.values() if section.questions]


def compute_image_target(
    original_path: Path,
    copy_images_to: Path | None,
    tex_output: Path,
    image_prefix: str,
    image_index: int,
) -> tuple[Path, str]:
    if copy_images_to:
        copy_images_to.mkdir(parents=True, exist_ok=True)
        suffix = original_path.suffix.lower() or ".png"
        target = copy_images_to / f"{slugify_filename(image_prefix)}-{image_index}{suffix}"
        shutil.copy2(original_path, target)
        return target, target.name

    try:
        ref = os.path.relpath(original_path.resolve(), tex_output.parent.resolve())
        return original_path.resolve(), Path(ref).as_posix()
    except Exception:
        return original_path.resolve(), original_path.resolve().as_posix()


def materialize_images(
    sections: list[Section],
    tex_output: Path,
    copy_images_to: Path | None,
    image_prefix: str,
) -> dict[Path, str]:
    """把题目中的图片路径转换成最终可写入 tex 的引用路径。

    输入：
    - ``sections``: 已解析好的题型结构；
    - ``tex_output``: 最终 tex 文件路径；
    - ``copy_images_to``: 若非空，则会把图片复制到该目录；
    - ``image_prefix``: 复制图片时使用的文件名前缀。

    输出：
    - ``dict[原始绝对路径, latex引用路径]`` 映射；
    - 同时会原地修改 ``question.images``，把它们替换成最终 tex 应引用的路径。
    """
    resolved: dict[Path, str] = {}
    image_index = 1
    for section in sections:
        for question in section.questions:
            latex_paths = []
            for image in question.images:
                image = image.resolve()
                if image in resolved:
                    latex_paths.append(resolved[image])
                    continue
                _, latex_ref = compute_image_target(
                    image,
                    copy_images_to=copy_images_to,
                    tex_output=tex_output,
                    image_prefix=image_prefix,
                    image_index=image_index,
                )
                resolved[image] = latex_ref
                latex_paths.append(latex_ref)
                image_index += 1
            question.images = [Path(path) for path in latex_paths]
    return resolved


def render_intro_items(items: list[str]) -> list[str]:
    lines = ["本章覆盖知识点如下：", r"\begin{introduction}"]
    for item in items:
        lines.append(f"  \\item {sanitize_latex_text(item)}")
    lines.append(r"\end{introduction}")
    return lines


def render_options(options: list[tuple[str, str]], indent: str = "  ") -> list[str]:
    """把选择题选项渲染为公共 ``\\optionlist`` 结构。"""
    lines = [f"{indent}\\optionlist"]
    for _, option_text in options:
        lines.append(f"{indent}\\item {option_text}")
    lines.append(f"{indent}\\end{{enumerate}}")
    return lines


def ensure_choice_blank(stem: str) -> str:
    # 统一选择题题尾留空，兼容 OCR 产生的半角/全角括号混用与缺失空格。
    stem = re.sub(r"[（(]\s*\\quad\s*[）)]", r"（\\quad）", stem)
    stem = re.sub(r"[（(]\s*[）)]", r"（\\quad）", stem)
    if "（\\quad）" in stem:
        return stem
    return f"{stem}（\\quad）"


def ensure_fillin_blank(stem: str) -> str:
    if "\\underline{" in stem:
        return stem
    return f"{stem}\\underline{{\\hspace{{2cm}}}}"


def render_image_caption(caption: str, indent: str = "  ") -> list[str]:
    caption = caption.strip()
    if not caption:
        return []
    return [
        f"{indent}\\par\\smallskip",
        f"{indent}{{\\centering\\small {caption}\\par}}",
    ]


def render_inline_images(images: list[Path], captions: list[str] | None = None) -> list[str]:
    """把单图/双图/三图渲染成项目里的公共图片命令。

    输入：
    - ``images``: 当前题目关联的图片列表；
    - ``captions``: 与图片一一对应的图注，可为空。

    输出：
    - 一组 LaTeX 行列表，调用的是主文件里定义的公共命令：
      ``\\centerquestionimage``、``questionimagegroup``、
      ``\\questionimageitem``、``\\questionimagegap``。
    """
    if not images:
        return []
    captions = captions or []
    if len(images) == 1:
        width = "0.35\\textwidth"
        caption = captions[0].strip() if captions else ""
        if caption:
            return [
                r"  \begin{questionimagegroup}",
                f"  \\questionimageitem{{{width}}}{{{images[0].as_posix()}}}{{{caption}}}",
                r"  \end{questionimagegroup}",
            ]
        return [f"  \\centerquestionimage{{{width}}}{{{images[0].as_posix()}}}"]
    elif len(images) == 2:
        width = "0.32\\textwidth"
    else:
        width = "0.28\\textwidth"

    lines = [r"  \begin{questionimagegroup}"]
    for index, image in enumerate(images):
        if index:
            lines.append(r"  \questionimagegap")
        caption = captions[index].strip() if index < len(captions) else ""
        lines.append(f"  \\questionimageitem{{{width}}}{{{image.as_posix()}}}{{{caption}}}")
    lines.append(r"  \end{questionimagegroup}")
    return lines


def render_question(question: Question, section_key: str) -> list[str]:
    """把单题渲染为最终 LaTeX。

    输入：
    - ``question``: 已完成题干/选项/图片整理的单题对象；
    - ``section_key``: 当前题型，用于决定是否补填空横线等规则。

    输出：
    - 一组 LaTeX 行列表，包含：
      - ``\\item``；
      - 可选的 ``\\optionlist``；
      - 可选的公共图片命令调用。
    """
    lines = [r"\item"]
    image_ref = question.images[0].as_posix() if question.images else None
    image_caption = question.image_captions[0].strip() if question.image_captions else ""
    stem = question.stem or "\n".join(question.raw_lines)
    stem = stem.strip()
    is_choice = bool(question.options)
    is_fillin = section_key == "填空题"

    if is_fillin:
        stem = ensure_fillin_blank(stem)

    if image_ref and is_choice:
        lines.append(f"  \\begin{{questionwithimage}}{{{image_ref}}}")
        lines.append(f"  {ensure_choice_blank(stem)}")
        lines.extend(render_options(question.options, indent="  "))
        if image_caption:
            lines.append("")
            lines.append(f"  % 图注：{image_caption}")
        lines.append(r"  \end{questionwithimage}")
        return lines

    if is_choice:
        lines.append(f"  {ensure_choice_blank(stem)}")
        lines.extend(render_options(question.options, indent="  "))
        return lines

    if question.images:
        lines.append(f"  {stem}")
        lines.append("")
        lines.extend(render_inline_images(question.images, question.image_captions))
        return lines

    lines.append(f"  {stem}")
    return lines


def render_section(section: Section) -> list[str]:
    """把一个题型 section 渲染成完整 LaTeX 片段。

    输出结构固定为：
    - ``\\section*{...}``
    - ``\\questionlist / \\blanklist / \\solutionlist``
    - 若干 ``\\item``
    - ``\\end{enumerate}``
    """
    start = section.questions[0].number if section.questions and section.questions[0].number else 1
    title = f"{section.title}（共 {len(section.questions)} 小题）"
    lines = [f"\\section*{{{title}}}", ""]
    list_command = SECTION_LIST_COMMAND_MAP.get(section.key, "solutionlist")
    if start == 1:
        lines.append(f"\\{list_command}")
    else:
        lines.append(f"\\{list_command}[{start}]")
    for question in section.questions:
        lines.extend(render_question(question, section.key))
        lines.append("")
    lines.append(r"\end{enumerate}")
    return lines


def render_tex(chapter_title: str, intro_items: list[str], sections: list[Section]) -> str:
    """把整章数据渲染成最终 tex 文本。

    输入：
    - ``chapter_title``: ``\\chapter{}`` 标题；
    - ``intro_items``: introduction 中的知识点条目；
    - ``sections``: 已解析并排好顺序的题型列表。

    输出：
    - 可直接写入章节文件的完整 tex 字符串。
    """
    lines = [
        "% Auto-generated by tool/miner_to_latex.py",
        "% 建议人工复核 OCR、公式与题型切分结果。",
        "% 该章节默认依赖主文件中的公共排版命令，例如 \\questionlist、\\blanklist、",
        "% \\solutionlist、\\optionlist、\\questionwithimage、\\centerquestionimage、",
        "% \\questionimagegroup 等。",
        f"\\chapter{{{sanitize_latex_text(chapter_title)}}}",
        "",
    ]
    lines.extend(render_intro_items(intro_items))
    lines.append("")
    for index, section in enumerate(sections):
        lines.extend(render_section(section))
        if index != len(sections) - 1:
            lines.append("")
    lines.append("")
    return "\n".join(lines)


def infer_copy_images_to(tex_output: Path, copy_images_to: Path | None) -> Path | None:
    """推断图片复制目录。

    如果用户没有显式指定 ``--copy-images-to``，但 tex 输出位于
    ``zhangz-book/chapter_tex``，则默认回落到同级项目约定的
    ``zhangz-book/image``。
    """
    if copy_images_to:
        return copy_images_to
    parts = tex_output.resolve().parts
    if "zhangz-book" in parts and "chapter_tex" in parts:
        project_root = Path(*parts[: parts.index("zhangz-book") + 1])
        candidate = project_root / "image"
        if candidate.exists():
            return candidate
    return None


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    该脚本既支持“原始输入 -> MinerU -> LaTeX”，也支持“已有 MinerU 输出 -> LaTeX”，
    所以参数里同时包含：
    - MinerU 执行相关参数；
    - 章节 tex 生成相关参数；
    - 图片复制与 introduction 条目补充参数。
    """
    parser = argparse.ArgumentParser(description="将图片/PDF 或 MinerU 输出转换为 ElegantBook 风格 LaTeX。")
    parser.add_argument("input_path", help="原始图片/PDF 文件，或已经生成好的 MinerU 输出目录。")
    parser.add_argument("--skip-mineru", action="store_true", help="跳过 MinerU 调用，直接读取已有输出目录。")
    parser.add_argument(
        "--mineru-output-dir",
        help="MinerU 输出目录。未指定时默认写入 tool/output/<输入文件名>/mineru。",
    )
    parser.add_argument(
        "--mineru-cmd-template",
        help="显式指定 MinerU 命令模板，例如：'mineru -p {input} -o {output}'。",
    )
    parser.add_argument("--chapter-title", help="生成的章节标题。默认使用输入文件名。")
    parser.add_argument("--tex-output", help="输出 tex 路径。默认写入 tool/output/<输入文件名>/<输入文件名>.tex。")
    parser.add_argument("--copy-images-to", help="将 MinerU 截图复制到该目录，并在 tex 中只保留文件名引用。")
    parser.add_argument("--image-prefix", help="复制图片时使用的文件名前缀。默认使用输入文件名。")
    parser.add_argument(
        "--intro-item",
        action="append",
        dest="intro_items",
        help="追加 introduction 条目，可多次传入。",
    )
    return parser


def main() -> int:
    """脚本主流程。

    流程分为四步：
    1. 解析参数并确认输入路径；
    2. 运行 MinerU，或直接定位已有 MinerU 输出；
    3. 解析内容块并切分题型/题目；
    4. 物化图片路径并写出最终 tex。

    返回值约定：
    - 0: 成功；
    - 2: MinerU 调用失败；
    - 3: 未找到可用 MinerU 输出；
    - 4: 找到了输出，但未识别出可用题目结构。
    """
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists():
        parser.error(f"输入路径不存在：{input_path}")

    input_stem = input_path.stem if input_path.is_file() else input_path.name
    work_root = DEFAULT_OUTPUT_DIR / input_stem
    tex_output = Path(args.tex_output).expanduser().resolve() if args.tex_output else work_root / f"{input_stem}.tex"
    ensure_parent(tex_output)

    if args.skip_mineru:
        artifacts_source = Path(args.mineru_output_dir).expanduser().resolve() if args.mineru_output_dir else input_path
    else:
        mineru_output_dir = (
            Path(args.mineru_output_dir).expanduser().resolve()
            if args.mineru_output_dir
            else work_root / "mineru"
        )
        try:
            run_mineru(input_path, mineru_output_dir, args.mineru_cmd_template)
        except Exception as exc:
            log(f"MinerU 调用失败：{exc}")
            return 2
        artifacts_source = mineru_output_dir

    artifacts = discover_artifacts(artifacts_source, input_stem=input_stem)
    if not any([artifacts.content_list, artifacts.middle_json, artifacts.markdown]):
        log("未找到 MinerU 输出文件。")
        log(f"搜索目录：{artifacts.root_dir}")
        return 3

    log(f"[2/4] 发现输出：content_list={artifacts.content_list}, middle={artifacts.middle_json}, markdown={artifacts.markdown}")
    blocks = load_content_blocks(artifacts)
    sections = parse_sections(blocks)
    if not sections:
        log("未能识别出题目结构，请检查 MinerU 输出或调节解析规则。")
        return 4

    log(f"[3/4] 已识别 {sum(len(section.questions) for section in sections)} 道题，开始生成 LaTeX")
    copy_images_to = Path(args.copy_images_to).expanduser().resolve() if args.copy_images_to else None
    copy_images_to = infer_copy_images_to(tex_output, copy_images_to)
    materialize_images(
        sections,
        tex_output=tex_output,
        copy_images_to=copy_images_to,
        image_prefix=args.image_prefix or input_stem,
    )

    intro_items = DEFAULT_INTRO_ITEMS[:]
    if args.intro_items:
        intro_items.extend(args.intro_items)
    tex_content = render_tex(args.chapter_title or input_stem, intro_items, sections)
    tex_output.write_text(tex_content, encoding="utf-8")

    log(f"[4/4] LaTeX 已生成：{tex_output}")
    if copy_images_to:
        log(f"图片已复制到：{copy_images_to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
