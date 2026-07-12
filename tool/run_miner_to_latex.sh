#!/usr/bin/env bash
set -euo pipefail

# 这个脚本是 miner_to_latex.py 的“一键包装层”。
#
# 主要功能：
# 1. 统一准备 MinerU 运行环境（PATH / HF_HOME / MODELSCOPE_CACHE / PYTHONPATH）；
# 2. 给 miner_to_latex.py 补齐项目内约定的默认输出路径；
# 3. 提供更贴近当前项目工作流的别名参数，例如 --chapter-name、
#    --from-mineru-output；
# 4. 在“原始输入 -> MinerU -> tex”和“已有 MinerU 输出 -> tex”两种模式间
#    做路由。
#
# 输入：
# - 一个原始图片/PDF 路径，或已有 MinerU 输出目录/文件；
# - 若干包装参数，再透传给 miner_to_latex.py。
#
# 输出：
# - 默认把章节 tex 写到 zhangz-book/chapter_tex/；
# - 默认把图片复制到 zhangz-book/image/；
# - 最终真正执行的是 miner_to_latex.py。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MINER_TO_LATEX_PY="$SCRIPT_DIR/miner_to_latex.py"
HCP_MINERU_BIN="/Users/yangyong/project/hubei-compute-platform/hcp-backend/.venv-mineru/bin/mineru"
DEFAULT_CHAPTER_TEX_DIR="$PROJECT_ROOT/zhangz-book/chapter_tex"
DEFAULT_IMAGE_DIR="$PROJECT_ROOT/zhangz-book/image"
VENDORED_MINERU_RUNTIME_DIR="$SCRIPT_DIR/vendor/mineru_runtime"
LOCAL_MINERU_TOOLS_CONFIG="$SCRIPT_DIR/cache/mineru-tools.json"

export PATH="/Users/yangyong/project/hubei-compute-platform/hcp-backend/.venv-mineru/bin:${PATH}"
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$HOME/.cache/modelscope/hub}"
export MINERU_TOOLS_CONFIG_JSON="${MINERU_TOOLS_CONFIG_JSON:-$LOCAL_MINERU_TOOLS_CONFIG}"
if [[ -d "$VENDORED_MINERU_RUNTIME_DIR" ]]; then
  export PYTHONPATH="$VENDORED_MINERU_RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}"
fi

# 提前建好缓存目录，避免首次运行时 MinerU 或 Python 侧因目录不存在报错。
mkdir -p "$HF_HOME" "$MODELSCOPE_CACHE"
mkdir -p "$(dirname "$MINERU_TOOLS_CONFIG_JSON")"

if [[ ! -x "$MINER_TO_LATEX_PY" ]]; then
  echo "未找到可执行的 miner_to_latex.py: $MINER_TO_LATEX_PY" >&2
  exit 1
fi

if [[ ! -d "$DEFAULT_CHAPTER_TEX_DIR" ]]; then
  echo "未找到 chapter_tex 目录: $DEFAULT_CHAPTER_TEX_DIR" >&2
  exit 1
fi

if [[ ! -d "$DEFAULT_IMAGE_DIR" ]]; then
  echo "未找到 image 目录: $DEFAULT_IMAGE_DIR" >&2
  exit 1
fi

forward_args=()
extra_args=()

print_wrapper_help() {
  cat <<EOF
用法：
  ./tool/run_miner_to_latex.sh [包装参数] <输入路径> [miner_to_latex.py 参数]

包装参数：
  --chapter-name <名称>         直接设置默认 tex 文件名与章节标题。
  --from-mineru-output          直接读取已有 MinerU 输出并生成 LaTeX，不重新解析 PDF/图片。
  --from-existing-output        --from-mineru-output 的别名。

说明：
  1. 默认模式会自动调用现成 MinerU：
     ./tool/run_miner_to_latex.sh ./tool/试卷去手写 2026-06-10 14.11_11.jpg --chapter-name 12.11-4等边三角形综合练习
  2. 已有 MinerU 输出时，可直接走“MinerU 输出 -> LaTeX”模式：
     ./tool/run_miner_to_latex.sh --from-mineru-output ./tool/output/xxx/mineru --chapter-name 12.11-4等边三角形综合练习
     ./tool/run_miner_to_latex.sh --from-mineru-output ./tool/output/xxx/hybrid_auto/xxx_content_list.json --chapter-name 12.11-4等边三角形综合练习
  3. 如果 --from-mineru-output 后面不额外指定已有输出路径，则会默认复用：
     ./tool/output/<输入文件名>/mineru
  4. 生成的章节 tex 默认会使用主文件中的公共排版命令
     （如 \\questionlist、\\blanklist、\\solutionlist、\\questionwithimage 等），
     适合直接被 zhangz-book/初二上.tex 这类总文件 include。

以下是 miner_to_latex.py 原生帮助：

EOF
  "$MINER_TO_LATEX_PY" --help
}

exec_miner_to_latex() {
  # 把“用户显式传入的参数”和“包装层推导出来的默认参数”拼成最终命令。
  #
  # 参数来源分两类：
  # - forward_args: 用户已经显式写出的 miner_to_latex.py 原生参数；
  # - extra_args: 包装脚本自动补上的默认值，例如 tex 输出位置、图片目录、
  #   章节标题等。
  local cmd=("$MINER_TO_LATEX_PY")

  if [[ ${#forward_args[@]} -gt 0 ]]; then
    cmd+=("${forward_args[@]}")
  fi

  if [[ ${#extra_args[@]} -gt 0 ]]; then
    cmd+=("${extra_args[@]}")
  fi

  if [[ $# -gt 0 ]]; then
    cmd+=("$@")
  fi

  exec "${cmd[@]}"
}

has_mineru_cmd_template=0
has_tex_output=0
has_copy_images_to=0
has_chapter_title=0
has_skip_mineru=0
from_mineru_output=0
has_explicit_mineru_output_dir=0
chapter_name=""
input_path=""
args=("$@")

# 这里先做一次“轻量参数预解析”：
# - 抽出包装层自己关心的参数，例如 --chapter-name、--from-mineru-output；
# - 其余参数尽量原样放进 forward_args，保持对 miner_to_latex.py 的兼容。
i=0
while [[ $i -lt ${#args[@]} ]]; do
  arg="${args[$i]}"
  case "$arg" in
    --mineru-cmd-template)
      has_mineru_cmd_template=1
      forward_args+=("$arg" "${args[$((i+1))]}")
      ((i+=2))
      continue
      ;;
    --tex-output)
      has_tex_output=1
      forward_args+=("$arg" "${args[$((i+1))]}")
      ((i+=2))
      continue
      ;;
    --copy-images-to)
      has_copy_images_to=1
      forward_args+=("$arg" "${args[$((i+1))]}")
      ((i+=2))
      continue
      ;;
    --chapter-title)
      has_chapter_title=1
      forward_args+=("$arg" "${args[$((i+1))]}")
      ((i+=2))
      continue
      ;;
    --chapter-name)
      chapter_name="${args[$((i+1))]}"
      ((i+=2))
      continue
      ;;
    --from-mineru-output|--from-existing-output)
      from_mineru_output=1
      has_skip_mineru=1
      forward_args+=(--skip-mineru)
      ((i+=1))
      continue
      ;;
    --mineru-output-dir|--image-prefix|--intro-item)
      if [[ "$arg" == "--mineru-output-dir" ]]; then
        has_explicit_mineru_output_dir=1
      fi
      forward_args+=("$arg" "${args[$((i+1))]}")
      ((i+=2))
      continue
      ;;
    --skip-mineru|-h|--help)
      has_skip_mineru=1
      forward_args+=("$arg")
      ((i+=1))
      continue
      ;;
    --*)
      forward_args+=("$arg")
      ((i+=1))
      continue
      ;;
    *)
      if [[ -z "$input_path" ]]; then
        input_path="$arg"
      fi
      forward_args+=("$arg")
      ((i+=1))
      continue
      ;;
  esac
done

if [[ -z "$input_path" ]]; then
  if printf '%s\0' "${args[@]}" | grep -Fzxq -- '--help' || printf '%s\0' "${args[@]}" | grep -Fzxq -- '-h'; then
    print_wrapper_help
    exit 0
  fi
  exec_miner_to_latex
fi

infer_input_stem() {
  # 根据输入路径推导默认输出文件名 stem。
  #
  # 例如：
  # - 原图 / PDF: foo.pdf -> foo
  # - MinerU 目录: .../foo/mineru -> foo
  # - content_list: foo_content_list.json -> foo
  local raw_input_path="$1"
  local basename stem parent_basename

  basename="$(basename "$raw_input_path")"
  stem="${basename%.*}"

  if [[ -d "$raw_input_path" ]]; then
    case "$basename" in
      mineru|auto|hybrid_auto)
        parent_basename="$(basename "$(dirname "$raw_input_path")")"
        if [[ -n "$parent_basename" ]]; then
          stem="$parent_basename"
        fi
        ;;
    esac
  else
    case "$basename" in
      *_content_list.json|*_content_list_v2.json|*_middle.json|*.md)
        stem="${stem%_content_list}"
        stem="${stem%_content_list_v2}"
        stem="${stem%_middle}"
        ;;
    esac
  fi

  printf '%s\n' "$stem"
}

is_mineru_output_like_path() {
  # 用于判断 input_path 自身是否已经像一个 MinerU 输出入口。
  # 如果已经是，就不再额外补 --mineru-output-dir。
  local raw_input_path="$1"
  local basename

  basename="$(basename "$raw_input_path")"
  case "$basename" in
    mineru|auto|hybrid_auto)
      return 0
      ;;
    *_content_list.json|*_content_list_v2.json|*_middle.json|*.md)
      return 0
      ;;
  esac
  return 1
}

input_stem="$(infer_input_stem "$input_path")"

output_stem="$input_stem"
if [[ -n "$chapter_name" ]]; then
  output_stem="$chapter_name"
fi
default_tex_output="$DEFAULT_CHAPTER_TEX_DIR/${output_stem}.tex"

if [[ "$has_tex_output" -eq 0 ]]; then
  extra_args+=(--tex-output "$default_tex_output")
fi
if [[ "$has_copy_images_to" -eq 0 ]]; then
  extra_args+=(--copy-images-to "$DEFAULT_IMAGE_DIR")
fi
if [[ -n "$chapter_name" && "$has_chapter_title" -eq 0 ]]; then
  extra_args+=(--chapter-title "$chapter_name")
fi

if [[ "$from_mineru_output" -eq 1 ]]; then
  # “直接读取已有 MinerU 输出”模式：
  # - 强制补上 --skip-mineru；
  # - 如果用户给的是原始输入名而不是实际输出目录，则按项目默认目录
  #   推导 --mineru-output-dir。
  if [[ "$has_mineru_cmd_template" -eq 1 ]]; then
    echo "--from-mineru-output 模式下无需传 --mineru-cmd-template" >&2
    exit 1
  fi
  if [[ "$has_explicit_mineru_output_dir" -eq 0 ]] && ! is_mineru_output_like_path "$input_path"; then
    extra_args+=(--mineru-output-dir "$SCRIPT_DIR/output/$input_stem/mineru")
  fi
  exec_miner_to_latex
fi

if [[ "$has_mineru_cmd_template" -eq 1 ]]; then
  # 用户已经显式指定了 MinerU 命令模板，直接交给 miner_to_latex.py。
  exec_miner_to_latex
fi

if [[ "$has_skip_mineru" -eq 0 && ! -x "$HCP_MINERU_BIN" ]]; then
  echo "未找到可执行的 MinerU 命令: $HCP_MINERU_BIN" >&2
  exit 1
fi

# 默认走项目里已经验证过的 HCP MinerU 可执行文件。
exec_miner_to_latex \
  --mineru-cmd-template "$HCP_MINERU_BIN -p {input} -o {output}"
