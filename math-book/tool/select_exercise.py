

遍历Pdf,将题号和题目内容提取出来，存储tex格式，写入chapter.tex文件中


import re

def parse_math_paper(file_path):
    """
    解析数学试卷文件，提取题号及其内容
    返回：{题号：题目内容}
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            latex_content = f.read()
        
        # 匹配题号位置：\item [1]、\item [2] 等
        pattern = re.compile(r'\\item\s*\[(1[0-9]|[1-9][0-9])\]')
        positions = {}

        # 记录每个题号的起始位置
        for match in pattern.finditer(latex_content):
            q_num = match.group(1)
            start_pos = match.start()
            end_pos = match.end()
            positions[q_num] = start_pos  # 只记录起始位置

        # 按题号排序
        sorted_nums = sorted(positions.keys(), key=lambda x: int(x))

        # 提取每个题号对应的内容
        result = {}
        for i, q_num in enumerate(sorted_nums):
            start = positions[q_num]
            # 找下一个题号的位置作为结束
            if i + 1 < len(sorted_nums):
                end = positions[sorted_nums[i + 1]]
            else:
                end = len(latex_content)
            # 提取题目内容（从题号标记之后开始，到下一个题号前结束）
            content = latex_content[start:end].strip()
            result[q_num] = content

        return result

    except FileNotFoundError:
        print(f"文件 {file_path} 不存在")
        return {}
    except Exception as e:
        print(f"解析出错: {e}")
        return {}