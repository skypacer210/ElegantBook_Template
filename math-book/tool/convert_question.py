import re


试卷排版优化——长题用垂直列表，短题用行内格式，节省版面空间。


def convert_questions(content):
    """
    将LaTeX中的选择题从enumerate环境转换为行内格式
    仅转换那些可以在一行内放下的题目
    """
    # 匹配 enumerate 环境（标准格式：\begin{enumerate}[A.]）
    pattern = r'\\begin{enumerate}\[A\.\](.*?)\\end{enumerate}'
    regex = re.compile(pattern, re.DOTALL)

    def replace_enumerate(match):
        items_content = match.group(1)

        # 提取各个选项的内容（直到下一个 \item 或结尾）
        items = re.findall(r'\\item\s+(.*?)(?=\\item|$)', items_content, re.DOTALL)

        # 清理选项内容
        clean_items = []
        for item in items:
            # 移除多余换行和空格，保留数学公式
            clean_item = re.sub(r'\s+', ' ', item).strip()
            clean_items.append(clean_item)

        # 计算所有选项的总字符长度
        total_length = sum(len(item) for item in clean_items)

        # 如果总长度小于 70，转换为行内格式
        if total_length < 70:
            options = []
            for i, item in enumerate(clean_items):
                letter = chr(ord('A') + i)
                options.append(f"{letter}. {item}")
            return "  ".join(options)
        else:
            # 总长度较长，保持原格式
            return match.group(0)

    # 执行替换
    return regex.sub(replace_enumerate, content)


# 测试示例
test_content = r"""
\begin{enumerate}[A.]
\item 3
\item 5
\item 7
\item 9
\end{enumerate}
"""

result = convert_questions(test_content)
print(result)