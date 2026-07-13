import argparse
from pathlib import Path
from typing import Optional

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy import signal


def preprocess_image(image: Image.Image) -> np.ndarray:
    """转灰度并反相，让黑色线条成为高亮区域，提升匹配稳定性。"""
    gray = ImageOps.grayscale(image)
    arr = np.asarray(gray, dtype=np.float32)
    return (255.0 - arr) / 255.0


def integral_image(arr: np.ndarray) -> np.ndarray:
    return np.pad(arr.cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)), mode="constant")


def window_sum(integral: np.ndarray, h: int, w: int) -> np.ndarray:
    return integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]


def normalized_cross_correlation(page_arr: np.ndarray, template_arr: np.ndarray) -> np.ndarray:
    """返回与模板同尺寸窗口的归一化相关性得分图。"""
    th, tw = template_arr.shape
    n = th * tw

    template_zero_mean = template_arr - template_arr.mean()
    template_energy = np.sum(template_zero_mean ** 2)
    if template_energy <= 1e-12:
        raise ValueError("模板图像信息量过低，无法匹配。")

    numerator = signal.correlate2d(page_arr, template_zero_mean, mode="valid")

    ii = integral_image(page_arr)
    ii_sq = integral_image(page_arr ** 2)
    local_sum = window_sum(ii, th, tw)
    local_sq_sum = window_sum(ii_sq, th, tw)

    local_mean = local_sum / n
    local_var = local_sq_sum - 2 * local_mean * local_sum + n * (local_mean ** 2)
    local_var = np.maximum(local_var, 1e-12)

    denominator = np.sqrt(local_var * template_energy)
    return numerator / denominator


def render_page(page: fitz.Page, dpi: int) -> Image.Image:
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    mode = "RGB" if pix.n >= 3 else "L"
    return Image.frombytes(mode, [pix.width, pix.height], pix.samples)


def resize_template(template: Image.Image, scale: float) -> Image.Image:
    width = max(10, int(round(template.width * scale)))
    height = max(10, int(round(template.height * scale)))
    return template.resize((width, height), Image.Resampling.LANCZOS)


def search_best_match(page_img: Image.Image, template_img: Image.Image, scales: np.ndarray):
    page_arr = preprocess_image(page_img)
    best = None

    for scale in scales:
        scaled_template = resize_template(template_img, float(scale))
        if scaled_template.width >= page_img.width or scaled_template.height >= page_img.height:
            continue

        template_arr = preprocess_image(scaled_template)
        scores = normalized_cross_correlation(page_arr, template_arr)
        y, x = np.unravel_index(np.argmax(scores), scores.shape)
        score = float(scores[y, x])

        if best is None or score > best["score"]:
            best = {
                "score": score,
                "x": int(x),
                "y": int(y),
                "w": scaled_template.width,
                "h": scaled_template.height,
                "scale": float(scale),
            }

    if best is None:
        raise RuntimeError("没有找到可用的匹配结果。")
    return best


def clamp_box(x: int, y: int, w: int, h: int, max_w: int, max_h: int):
    x = max(0, min(x, max_w - 1))
    y = max(0, min(y, max_h - 1))
    w = max(1, min(w, max_w - x))
    h = max(1, min(h, max_h - y))
    return x, y, w, h


def hierarchical_match(
    page_img: Image.Image,
    template_img: Image.Image,
    min_scale: float,
    max_scale: float,
    steps: int,
    coarse_factor: float = 0.35,
):
    """先缩小粗搜，再在原图附近精搜。"""
    coarse_page = page_img.resize(
        (
            max(50, int(page_img.width * coarse_factor)),
            max(50, int(page_img.height * coarse_factor)),
        ),
        Image.Resampling.BILINEAR,
    )
    coarse_template = template_img.resize(
        (
            max(20, int(template_img.width * coarse_factor)),
            max(20, int(template_img.height * coarse_factor)),
        ),
        Image.Resampling.BILINEAR,
    )

    coarse_scales = np.linspace(min_scale, max_scale, max(9, min(steps, 17)))
    coarse_best = search_best_match(coarse_page, coarse_template, coarse_scales)

    approx_scale = coarse_best["scale"]
    approx_x = int(round(coarse_best["x"] / coarse_factor))
    approx_y = int(round(coarse_best["y"] / coarse_factor))
    approx_w = int(round(template_img.width * approx_scale))
    approx_h = int(round(template_img.height * approx_scale))

    margin_x = max(40, approx_w // 4)
    margin_y = max(40, approx_h // 4)
    region_x = max(0, approx_x - margin_x)
    region_y = max(0, approx_y - margin_y)
    region_r = min(page_img.width, approx_x + approx_w + margin_x)
    region_b = min(page_img.height, approx_y + approx_h + margin_y)
    region = page_img.crop((region_x, region_y, region_r, region_b))

    refine_min = max(min_scale, approx_scale - 0.12)
    refine_max = min(max_scale, approx_scale + 0.12)
    refine_scales = np.linspace(refine_min, refine_max, max(7, min(steps, 11)))
    refine_best = search_best_match(region, template_img, refine_scales)

    refine_best["x"] += region_x
    refine_best["y"] += region_y
    refine_best["x"], refine_best["y"], refine_best["w"], refine_best["h"] = clamp_box(
        refine_best["x"],
        refine_best["y"],
        refine_best["w"],
        refine_best["h"],
        page_img.width,
        page_img.height,
    )
    return refine_best


def crop_template_from_pdf(
    pdf_path: str,
    template_path: str,
    output_path: str,
    debug_path: Optional[str] = None,
    dpi: int = 150,
    min_scale: float = 0.6,
    max_scale: float = 1.4,
    steps: int = 33,
):
    pdf_file = Path(pdf_path)
    template_file = Path(template_path)
    output_file = Path(output_path)
    if debug_path is None:
        debug_file = output_file.with_name(output_file.stem + "_debug.png")
    else:
        debug_file = Path(debug_path)

    template_img = Image.open(template_file).convert("RGB")

    best_global = None
    doc = fitz.open(pdf_file)
    try:
        for page_index, page in enumerate(doc):
            page_img = render_page(page, dpi=dpi)
            best_page = hierarchical_match(
                page_img=page_img,
                template_img=template_img,
                min_scale=min_scale,
                max_scale=max_scale,
                steps=steps,
            )
            best_page["page_index"] = page_index
            best_page["page_img"] = page_img

            if best_global is None or best_page["score"] > best_global["score"]:
                best_global = best_page
    finally:
        doc.close()

    if best_global is None:
        raise RuntimeError("未能在 PDF 中定位目标图片。")

    x, y, w, h = best_global["x"], best_global["y"], best_global["w"], best_global["h"]
    crop = best_global["page_img"].crop((x, y, x + w, y + h))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    crop.save(output_file)

    debug_img = best_global["page_img"].copy()
    draw = ImageDraw.Draw(debug_img)
    draw.rectangle((x, y, x + w, y + h), outline="red", width=4)
    debug_img.save(debug_file)

    print(f"匹配页码: {best_global['page_index'] + 1}")
    print(f"匹配得分: {best_global['score']:.4f}")
    print(f"缩放比例: {best_global['scale']:.4f}")
    print(f"裁剪区域: x={x}, y={y}, w={w}, h={h}")
    print(f"输出图片: {output_file}")
    print(f"调试图片: {debug_file}")


def main():
    parser = argparse.ArgumentParser(description="从 PDF 中按模板图自动定位并裁剪。")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("template_path", help="目标模板图片路径")
    parser.add_argument(
        "-o",
        "--output",
        default="cropped_from_pdf.png",
        help="输出图片路径",
    )
    parser.add_argument("--debug", default=None, help="带红框的调试图路径")
    parser.add_argument("--dpi", type=int, default=150, help="PDF 渲染 DPI")
    parser.add_argument("--min-scale", type=float, default=0.6, help="模板最小缩放")
    parser.add_argument("--max-scale", type=float, default=1.4, help="模板最大缩放")
    parser.add_argument("--steps", type=int, default=33, help="缩放采样数量")
    args = parser.parse_args()

    crop_template_from_pdf(
        pdf_path=args.pdf_path,
        template_path=args.template_path,
        output_path=args.output,
        debug_path=args.debug,
        dpi=args.dpi,
        min_scale=args.min_scale,
        max_scale=args.max_scale,
        steps=args.steps,
    )


if __name__ == "__main__":
    main()
