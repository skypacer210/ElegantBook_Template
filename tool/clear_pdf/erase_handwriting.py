#!/usr/bin/env python3
"""调用 TextIn 手写擦除接口，清洗本地 PDF 或图片文件。

功能：
1. 默认读取当前目录下的 ``example_1.pdf``；
2. 调用 readme-1.md 中说明的 ``handwritten_erase`` 接口；
3. 单文件模式：将接口返回的 base64 JPG 解码后写入本地文件；
4. 批处理模式：当输入为 PDF 且输出为目录时，按页渲染 PDF，并逐页调用接口，
   按顺序输出多张图片。

输入：
- 本地 PDF/图片路径，默认 ``tool/clear_pdf/example_1.pdf``；
- 可选的接口参数，如 ``crop``、``doc_direction``、``dewarp`` 等。

输出：
- 一张去除手写后的 JPG 图片，默认 ``example_1-cleaned.jpg``。
- 或输出到目录的多张图片，例如 ``example_2_out/example_2-p001-cleaned.jpg``。

认证：
- 优先读取环境变量 ``TEXTIN_APP_ID`` / ``TEXTIN_SECRET_CODE``；
- 若未提供，则回退到解析同目录下 ``readme-1.md`` 中记录的密钥。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_PATH = SCRIPT_DIR / "example_1.pdf"
DEFAULT_OUTPUT_PATH = SCRIPT_DIR / "example_1-cleaned.jpg"
README_PATH = SCRIPT_DIR / "readme-1.md"
API_URL = "https://api.textin.com/ai/service/v1/handwritten_erase"
APP_ID_RE = re.compile(r"x-ti-app-id：([A-Za-z0-9]+)")
SECRET_RE = re.compile(r"x-ti-secret-code：([A-Za-z0-9]+)")


class HandwritingEraseError(RuntimeError):
    """表示调用远程擦除接口或解析结果时出现的业务错误。"""


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


def try_import_fitz() -> Any | None:
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return None
    return fitz


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数。

    这些参数直接对应 readme-1.md 中的接口能力，方便后续在不同试卷上
    逐步调节效果，而不需要反复改代码。
    """

    parser = argparse.ArgumentParser(description="调用 TextIn 接口对 PDF/图片执行手写擦除。")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="待处理的 PDF/图片路径，默认使用 tool/clear_pdf/example_1.pdf。",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help=(
            "输出路径：单文件模式为 JPG 文件路径；批处理模式为输出目录路径。"
            "默认写为 tool/clear_pdf/example_1-cleaned.jpg。"
        ),
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="强制启用按页批处理（仅对 PDF 生效）。默认：输入为 PDF 且 --output 为目录时自动启用。",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="PDF 渲染分辨率（DPI），仅在批处理模式生效，默认 150。",
    )
    parser.add_argument(
        "--page-start",
        type=int,
        default=1,
        help="起始页码（从 1 开始），仅在批处理模式生效，默认 1。",
    )
    parser.add_argument(
        "--page-end",
        type=int,
        default=0,
        help="结束页码（从 1 开始，含该页）。0 表示到末页，仅在批处理模式生效，默认 0。",
    )
    parser.add_argument(
        "--page-padding",
        type=int,
        default=3,
        help="页码补零宽度，仅在批处理模式生效，默认 3（p001）。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要处理的页码与输出路径，不调用远端接口。",
    )
    parser.add_argument(
        "--crop",
        type=int,
        choices=(0, 1),
        default=0,
        help="是否执行切边：0 关闭，1 开启。",
    )
    parser.add_argument(
        "--crop-position",
        help="手动切边坐标，格式 x1,y1,x2,y2,x3,y3,x4,y4，需配合 --crop 1。",
    )
    parser.add_argument(
        "--doc-direction",
        type=int,
        choices=(0, 1, 2, 3, 4),
        default=0,
        help="方向转正：0 关闭，1/2/3 为顺时针旋转，4 为自动方向转正。",
    )
    parser.add_argument(
        "--mask-position",
        help="仅擦除指定区域，格式 x1,y1,x2,y2,x3,y3,x4,y4；默认整图。",
    )
    parser.add_argument(
        "--dewarp",
        type=int,
        choices=(0, 1),
        default=1,
        help="是否启用弯曲矫正：0 关闭，1 开启。",
    )
    parser.add_argument(
        "--binarization",
        type=int,
        choices=(0, 1),
        default=1,
        help="是否启用增强锐化：0 关闭，1 开启。",
    )
    parser.add_argument(
        "--image-type",
        type=int,
        choices=(0, 1),
        default=1,
        help="返回图像类型：0 黑白图，1 彩色图。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP 请求超时时间（秒），默认 120。",
    )
    return parser


def load_credentials(readme_path: Path) -> tuple[str, str]:
    """加载接口认证信息。

    优先从环境变量读取，便于后续迁移到更安全的配置方式；
    若环境变量缺失，则回退到解析 readme 文件中的示例密钥。
    """

    app_id = os.environ.get("TEXTIN_APP_ID", "").strip()
    secret_code = os.environ.get("TEXTIN_SECRET_CODE", "").strip()
    if app_id and secret_code:
        return app_id, secret_code

    if not readme_path.exists():
        raise HandwritingEraseError(
            "未找到认证信息：既没有设置 TEXTIN_APP_ID/TEXTIN_SECRET_CODE，"
            f"也没有找到 {readme_path}。"
        )

    content = readme_path.read_text(encoding="utf-8")
    app_match = APP_ID_RE.search(content)
    secret_match = SECRET_RE.search(content)
    if not app_match or not secret_match:
        raise HandwritingEraseError(
            "无法从 readme-1.md 中解析 x-ti-app-id / x-ti-secret-code。"
        )
    return app_match.group(1), secret_match.group(1)


def build_query_params(args: argparse.Namespace) -> dict[str, str]:
    """根据命令行参数构造接口 query string。"""

    params: dict[str, str] = {
        "crop": str(args.crop),
        "doc_direction": str(args.doc_direction),
        "dewarp": str(args.dewarp),
        "binarization": str(args.binarization),
        "image_type": str(args.image_type),
    }
    if args.crop_position:
        params["crop_position"] = args.crop_position
    if args.mask_position:
        params["mask_position"] = args.mask_position
    return params


def call_handwriting_erase_api_bytes(
    *,
    body: bytes,
    query_params: dict[str, str],
    app_id: str,
    secret_code: str,
    timeout: int,
) -> dict[str, Any]:
    """上传二进制内容并调用手写擦除接口，返回接口 JSON。

    接口要求 body 是原始二进制流，因此这里直接读取文件内容作为请求体。
    """

    url = f"{API_URL}?{urlencode(query_params)}"
    request = Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "x-ti-app-id": app_id,
            "x-ti-secret-code": secret_code,
            "Content-Type": "application/octet-stream",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HandwritingEraseError(
            f"接口 HTTP 错误：{exc.code} {exc.reason}，响应内容：{detail}"
        ) from exc
    except URLError as exc:
        raise HandwritingEraseError(f"接口请求失败：{exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HandwritingEraseError(f"接口返回的不是合法 JSON：{payload[:500]}") from exc

    if data.get("code") != 200:
        raise HandwritingEraseError(
            f"接口调用失败，code={data.get('code')}，message={data.get('message')}"
        )
    return data


def call_handwriting_erase_api_path(
    *,
    input_path: Path,
    query_params: dict[str, str],
    app_id: str,
    secret_code: str,
    timeout: int,
) -> dict[str, Any]:
    if not input_path.exists():
        raise HandwritingEraseError(f"输入文件不存在：{input_path}")
    return call_handwriting_erase_api_bytes(
        body=input_path.read_bytes(),
        query_params=query_params,
        app_id=app_id,
        secret_code=secret_code,
        timeout=timeout,
    )


def save_result_image(response_json: dict[str, Any], output_path: Path) -> str | None:
    """将接口返回的 base64 图像写入本地 JPG。

    返回 x_request_id，便于后续定位远端请求日志。
    """

    result = response_json.get("result") or {}
    image_b64 = result.get("image")
    if not image_b64:
        raise HandwritingEraseError("接口返回成功，但 result.image 为空。")

    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception as exc:  # noqa: BLE001
        raise HandwritingEraseError("接口返回的 image 不是合法 base64。") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)
    return response_json.get("x_request_id")


def iter_pdf_pages_as_png_bytes(
    *,
    pdf_path: Path,
    dpi: int,
    page_start: int,
    page_end: int,
) -> list[tuple[int, bytes]]:
    fitz = try_import_fitz()
    if fitz is None:
        raise HandwritingEraseError(
            "批处理模式需要 PyMuPDF（import fitz）用于 PDF 拆页渲染。"
            "请安装后重试，或先将 PDF 转为图片再处理。"
        )
    if not pdf_path.exists():
        raise HandwritingEraseError(f"输入文件不存在：{pdf_path}")

    doc = fitz.open(str(pdf_path))
    try:
        total_pages = int(doc.page_count)
        if total_pages <= 0:
            return []
        start_index = max(page_start, 1)
        end_index = page_end if page_end and page_end > 0 else total_pages
        if end_index < start_index:
            raise HandwritingEraseError(
                f"页码范围非法：page_start={page_start}, page_end={page_end}, total_pages={total_pages}"
            )
        end_index = min(end_index, total_pages)

        scale = dpi / 72
        matrix = fitz.Matrix(scale, scale)
        pages: list[tuple[int, bytes]] = []
        for page_no in range(start_index, end_index + 1):
            page = doc.load_page(page_no - 1)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append((page_no, pix.tobytes("png")))
        return pages
    finally:
        doc.close()


def build_batch_output_path(
    *,
    output_dir: Path,
    input_stem: str,
    page_no: int,
    padding: int,
) -> Path:
    return output_dir / f"{input_stem}-p{page_no:0{padding}d}-cleaned.jpg"



def main() -> int:
    """程序入口。

    典型调用：
    ``python3 tool/clear_pdf/erase_handwriting.py``
    """

    args = build_parser().parse_args()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    app_id, secret_code = load_credentials(README_PATH)
    query_params = build_query_params(args)

    output_is_dir = output_path.suffix == ""
    use_batch = bool(args.batch or (is_pdf(input_path) and output_is_dir))

    if not use_batch:
        if args.dry_run:
            print(f"dry-run: 将处理单文件 {input_path} -> {output_path}")
            return 0
        response_json = call_handwriting_erase_api_path(
            input_path=input_path,
            query_params=query_params,
            app_id=app_id,
            secret_code=secret_code,
            timeout=args.timeout,
        )
        request_id = save_result_image(response_json, output_path)
        print(f"已完成手写擦除：{output_path}")
        if request_id:
            print(f"x_request_id: {request_id}")
        return 0

    if not is_pdf(input_path):
        raise HandwritingEraseError("批处理模式仅支持 PDF 输入。")

    output_dir = output_path
    output_dir.mkdir(parents=True, exist_ok=True)

    pages = iter_pdf_pages_as_png_bytes(
        pdf_path=input_path,
        dpi=args.dpi,
        page_start=args.page_start,
        page_end=args.page_end,
    )
    if not pages:
        raise HandwritingEraseError("PDF 未包含可处理的页面。")

    input_stem = input_path.stem
    if args.dry_run:
        for page_no, _ in pages:
            out_path = build_batch_output_path(
                output_dir=output_dir,
                input_stem=input_stem,
                page_no=page_no,
                padding=args.page_padding,
            )
            print(f"dry-run: p{page_no} -> {out_path}")
        return 0

    for page_no, page_bytes in pages:
        out_path = build_batch_output_path(
            output_dir=output_dir,
            input_stem=input_stem,
            page_no=page_no,
            padding=args.page_padding,
        )
        response_json = call_handwriting_erase_api_bytes(
            body=page_bytes,
            query_params=query_params,
            app_id=app_id,
            secret_code=secret_code,
            timeout=args.timeout,
        )
        request_id = save_result_image(response_json, out_path)
        if request_id:
            print(f"p{page_no}: {out_path} (x_request_id: {request_id})")
        else:
            print(f"p{page_no}: {out_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HandwritingEraseError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
