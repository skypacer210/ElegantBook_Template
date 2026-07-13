import re
import os

input_file = "高一上浙江试卷整理/2501金华十校高一期末_数学答案.txt"
image_dir = "figure/高一上试卷/snapshots_images/"

input_dir = os.path.dirname(input_file)
file_name, file_ext = os.path.splitext(os.path.basename(input_file))
print(f"解析的文件名前缀: {file_name}")

orig_file = os.path.join(input_dir, f"{file_name}_orig{file_ext}")

with open(input_file, 'r', encoding='utf-8') as file:
    content = file.read()

with open(orig_file, 'w', encoding='utf-8') as orig_file_obj:
    orig_file_obj.write(content)

# 匹配图片
image_pattern = re.compile(rf"(({re.escape(file_name)}).page(\d+)_figure_\d+\.png)")
matched_files = []

if os.path.exists(image_dir):
    for f in os.listdir(image_dir):
        match = image_pattern.search(f)
        if match:
            page_num = int(match.group(3))
            matched_files.append((page_num, f))
    matched_files.sort(key=lambda x: x[0])
    print(f"匹配到 {len(matched_files)} 张图片")
else:
    print(f"图片目录不存在: {image_dir}")

# 插入图片
image_inserts = []
for page, f in matched_files:
    img_path = os.path.join(image_dir, f)
    if os.path.exists(img_path):
        image_code = f"\n\\begin{{center}}\\includegraphics[width=0.3\\textwidth]{{{img_path}}}\\end{{center}}\n"
        image_inserts.append(image_code)
        print(f"已准备插入图片：{f}")

if image_inserts:
    content = "\n".join(image_inserts) + "\n" + content
    print(f"成功插入 {len(image_inserts)} 张图片")

# 格式化选择题
choice_pattern = re.compile(
    r'([A-D]\.\s*[^\s].*?)\s*\\quad\s*(?=[A-D]\.)',
    re.DOTALL
)
content = choice_pattern.sub(r'\1\n\n', content)

# 符号替换
content = content.replace(r'\(', '$').replace(r'\)', '$')
content = content.replace(r'\lt', '<').replace(r'\gt', '>')
content = content.replace(r'align*', 'aligned')

# 保存结果
output_file = os.path.join(input_dir, f"{file_name}_processed{file_ext}")
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\n处理完成！输出文件: {output_file}")