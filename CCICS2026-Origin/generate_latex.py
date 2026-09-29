#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate LaTeX file from extracted Word content following the jpconf template.
"""
import json
import re

def escape_latex(text):
    """Escape special LaTeX characters."""
    replacements = {
        '\\': r'\textbackslash{}',
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
    }
    # Handle backslash first
    result = text.replace('\\', replacements['\\'])
    for char, repl in replacements.items():
        if char != '\\':
            result = result.replace(char, repl)
    return result

def process_inline_math(text):
    """
    Convert inline math placeholders. The Word doc has spaces where math should be.
    We'll leave simple text as-is and mark obvious variables with $...$ where needed.
    """
    # For now, just escape LaTeX special chars
    return escape_latex(text)

def is_section_header(style):
    """Check if style is a section level header."""
    return style in ['大1', '大1A']

def is_subsection_header(style):
    """Check if style is a subsection level header."""
    return style in ['大1A下']

def strip_section_number(text):
    """Remove leading numbers from section titles for \section{}."""
    # Match patterns like "1 Introduction", "2. Title", "3.1 Subtitle"
    match = re.match(r'^(\d+(?:\.\d+)*)\s+(.*)', text.strip())
    if match:
        return match.group(2)
    # Also handle "1) Title" patterns for list items
    return text

def style_to_latex(para):
    """Convert paragraph style and content to LaTeX."""
    style = para['style']
    text = para['text'].strip()
    
    if not text:
        return ''
    
    # Title (first Normal paragraph)
    if style == 'Normal' and para['index'] == 0:
        return f'\\title{{{process_inline_math(text)}}}\n\n'
    
    # Authors
    if style == 'BT1':
        # Convert author superscripts: "Name1, Name2" -> "Name$^1$, Name$^2$"
        author_text = text
        # Fix superscripts: digit after name should be $^digit$
        author_text = re.sub(r'(\w)(\d)([,)])', r'\1$^\2$\3', author_text)
        author_text = re.sub(r'(\w)(\d)$', r'\1$^\2$', author_text)
        return f'\\author{{{process_inline_math(author_text)}}}\n\n'
    
    # Addresses
    if style == 'Normal' and para['index'] == 2:
        # Two addresses in one paragraph, split them
        lines = text.split('\n')
        result = ''
        for line in lines:
            line = line.strip()
            if line:
                # Convert "1. Address" -> "$^1$ Address"
                match = re.match(r'^(\d+)\.\s*(.*)', line)
                if match:
                    num = match.group(1)
                    addr = match.group(2)
                    result += f'\\address{{$^{num}$ {process_inline_math(addr)}}}\n'
        result += '\n'
        return result
    
    # Abstract header
    if style == '英参' and text.lower() == 'abstract':
        return '\\begin{abstract}\n'
    
    # Abstract content (first 正文1 after abstract header)
    if style == '正文1':
        return process_inline_math(text) + '\n\\end{abstract}\n\n'
    
    # Keywords
    if style == '英参2':
        # Remove "Keywords  " prefix
        kw_text = re.sub(r'^Keywords\s*', '', text)
        return f'\\noindent{{\\bf Keywords.}} {process_inline_math(kw_text)}\n\n'
    
    # Section headers
    if is_section_header(style):
        title = strip_section_number(text)
        # Skip if it's an intro paragraph without number (like section 3 intro text)
        if re.match(r'^\d+', text):
            return f'\\section{{{process_inline_math(title)}}}\n\n'
        else:
            # Regular paragraph content under section
            return process_inline_math(text) + '\n\n'
    
    # Subsection headers
    if is_subsection_header(style):
        title = strip_section_number(text)
        return f'\\subsection{{{process_inline_math(title)}}}\n\n'
    
    # Figure captions
    if style == 'Text' and text.startswith('Fig.'):
        # Extract figure number and caption
        match = re.match(r'Fig\.\s*(\d+)\s*(.*)', text)
        if match:
            fig_num = match.group(1)
            caption = match.group(2).strip()
            return (
                f'\\begin{{figure}}[h]\n'
                f'\\begin{{center}}\n'
                f'\\includegraphics[width=14pc]{{name.eps}}\n'
                f'\\end{{center}}\n'
                f'\\caption{{\\label{{fig{fig_num}}}{process_inline_math(caption)}}}\n'
                f'\\end{{figure}}\n\n'
            )
    
    # Table captions
    if (style == '表题' or style == 'Body Text') and text.startswith('Table'):
        # Just return text, actual table environment will be inserted separately
        return ''
    
    # First Paragraph (same as body text)
    if style in ['First Paragraph', '正文2', 'Body Text', 'Text']:
        return process_inline_math(text) + '\n\n'
    
    # Acknowledgements
    if style == 'Normal' and para.get('index', 0) > 90 and 'Acknowledgements' in text:
        return '\\ack\n'
    
    # References header
    if style == '大1A' and 'References' in text:
        return '\\section*{References}\n\\begin{thebibliography}{99}\n'
    
    # Default: regular text
    return process_inline_math(text) + '\n\n'


def main():
    # Load extracted content
    with open('extracted_content.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    paragraphs = data['paragraphs']
    tables = data['tables']
    
    # Build LaTeX output
    output_lines = []
    
    # Document preamble (from template)
    output_lines.append(r'\documentclass[a4paper]{jpconf}')
    output_lines.append(r'\usepackage{graphicx}')
    output_lines.append(r'\begin{document}')
    output_lines.append('')
    
    # Track state
    in_references = False
    refs_started = False
    current_section = None
    table_captions = {}
    
    # First pass: collect table captions
    table_idx = 0
    for para in paragraphs:
        text = para['text'].strip()
        if (para['style'] in ['表题', 'Body Text'] and 
            (text.startswith('Table') or text.startswith('表'))):
            table_captions[table_idx] = text
            table_idx += 1
    
    # Process paragraphs
    ref_count = 0
    for para in paragraphs:
        text = para['text'].strip()
        
        # Handle references section
        if 'References' in text and para['style'] in ['大1A', '大1']:
            in_references = True
            output_lines.append('\\section*{References}')
            output_lines.append('\\begin{thebibliography}{99}')
            refs_started = True
            continue
        
        if in_references and refs_started:
            # Check if this is a reference entry
            if para['style'] == 'Normal' and text:
                # Format: Author, Title, Venue, etc.
                ref_count += 1
                # Try to extract and format properly
                ref_text = process_inline_math(text)
                # Remove reference number if present at start like "[1]"
                ref_text = re.sub(r'^\[\d+\]\s*', '', ref_text)
                output_lines.append(f'\\bibitem{{ref{ref_count}}} {ref_text}')
            continue
        
        # Skip abstract/keywords header markers already handled
        if para['style'] == '英参' and text.lower() == 'abstract':
            output_lines.append('\\begin{abstract}')
            continue
        
        if para['style'] == '英参2':
            kw_text = re.sub(r'^Keywords\s*', '', text)
            output_lines.append(f'\\end{{abstract}}')
            output_lines.append('')
            output_lines.append(f'\\noindent{{\\bf Keywords.}} {process_inline_math(kw_text)}')
            output_lines.append('')
            continue
        
        # Handle normal content
        latex = style_to_latex_smart(para, output_lines)
        if latex:
            output_lines.extend(latex.split('\n'))
    
    # Close references
    if refs_started:
        output_lines.append('\\end{thebibliography}')
    
    # Close document
    output_lines.append('\\end{document}')
    
    # Write to file
    with open('Lossless Transmission for Geo-Distributed AI Computing via LSQ-Sketch and APN-Aware SRv6 Backpressure -Origin.tex', 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))
        
    print(f'Generated Lossless Transmission for Geo-Distributed AI Computing via LSQ-Sketch and APN-Aware SRv6 Backpressure -Origin.tex successfully')
    print(f'Total lines: {len(output_lines)}')


def style_to_latex_smart(para, current_output):
    """Enhanced version with better state tracking."""
    style = para['style']
    text = para['text'].strip()
    idx = para.get('index', -1)
    
    if not text:
        return ''
    
    result_parts = []
    
    # === Title & Authors & Addresses (first 3 paragraphs) ===
    if idx == 0 and style == 'Normal':
        result_parts.append(f'\\title{{{process_inline_math(text)}}}')
        return '\n'.join(result_parts) + '\n\n'
    
    if idx == 1 and style == 'BT1':
        author_text = text
        # Fix superscripts carefully
        # Pattern: Name1, Name2 () -> Name$^1$, Name$^2$ ()
        author_text = re.sub(r'([A-Za-z])(\d)', r'\1$^\2$', author_text)
        result_parts.append(f'\\author{{{process_inline_math(author_text)}}}')
        return '\n'.join(result_parts) + '\n\n'
    
    if idx == 2 and style == 'Normal':
        addr_lines = text.split('\n')
        for line in addr_lines:
            line = line.strip()
            if line:
                match = re.match(r'^(\d+)\.\s*(.*)', line)
                if match:
                    num = match.group(1)
                    addr = match.group(2)
                    result_parts.append(f'\\address{{$^{num}$ {process_inline_math(addr)}}}')
        # Add ead placeholder
        result_parts.append('\\ead{corresponding.author@email.com}')
        return '\n'.join(result_parts) + '\n\n'
    
    # === Abstract content (正文1) ===
    if style == '正文1':
        # Check if abstract was just opened
        return process_inline_math(text)
    
    # === Keywords (英参2) ===
    if style == '英参2':
        kw_text = re.sub(r'^Keywords\s*', '', text)
        return f'\\noindent{{\\bf Keywords.}} {process_inline_math(kw_text)}\n\n'
    
    # === Section headers ===
    if style == '大1' or (style == '大1A' and re.match(r'^\d+\s+[A-Z]', text)):
        # Section: "1 Introduction", "2 Overall Architecture..."
        if re.match(r'^\d+\s', text):
            title = strip_section_number(text)
            return f'\\section{{{process_inline_math(title)}}}\n\n'
    
    if style == '大1A':
        # Check if numbered section
        if re.match(r'^\d+\s', text) or re.match(r'^\d+\.\s', text):
            title = strip_section_number(text)
            return f'\\section{{{process_inline_math(title)}}}\n\n'
        # Otherwise it's intro text for section
        return process_inline_math(text) + '\n\n'
    
    # === Subsection headers ===
    if style == '大1A下':
        title = strip_section_number(text)
        return f'\\subsection{{{process_inline_math(title)}}}\n\n'
    
    # === Figure captions ===
    if style == 'Text' and text.lower().startswith('fig.'):
        match = re.match(r'[Ff]ig\.\s*(\d+)\s*(.*)', text)
        if match:
            fig_num = match.group(1)
            caption = match.group(2).strip()
            return (
                f'\\begin{{figure}}[h]\n'
                f'\\begin{{center}}\n'
                f'\\includegraphics[width=14pc]{{name.eps}}\n'
                f'\\end{{center}}\n'
                f'\\caption{{\\label{{fig{fig_num}}}{process_inline_math(caption)}}}\n'
                f'\\end{{figure}}\n\n'
            )
    
    # === Table caption placeholders (we'll insert real tables later) ===
    if style in ['表题', 'Body Text'] and (text.startswith('Table') or text.startswith('表')):
        match = re.match(r'Table\s*(\d+)\s*(.*)', text)
        if match:
            tbl_num = match.group(1)
            caption = match.group(2).strip()
            # Insert placeholder table (user can fill with real data)
            return (
                f'\\begin{{table}}[h]\n'
                f'\\caption{{\\label{{tab{tbl_num}}}{process_inline_math(caption)}}}\n'
                f'\\begin{{center}}\n'
                f'\\begin{{tabular}}{{llll}}\n'
                f'\\br\n'
                f'Column 1 \\& Column 2 \\& Column 3 \\& Column 4 \\\\\n'
                f'\\mr\n'
                f'... \\& ... \\& ... \\& ... \\\\\n'
                f'\\br\n'
                f'\\end{{tabular}}\n'
                f'\\end{{center}}\n'
                f'\\end{{table}}\n\n'
            )
    
    # === Acknowledgements ===
    if style == '1' or ('Acknowledg' in text and idx > 80):
        if 'Acknowledg' in text:
            return '\\ack\n'
        return process_inline_math(text) + '\n\n'
    
    if idx == 101 and style == 'Normal' and 'Acknowledgements' not in text:
        # First Normal after section 7 = acknowledgements text
        return process_inline_math(text) + '\n\n'
    
    # === General body text ===
    if style in ['正文2', 'First Paragraph', 'Body Text', 'Text', 'Normal']:
        return process_inline_math(text) + '\n\n'
    
    # Fallback
    return process_inline_math(text) + '\n\n'


if __name__ == '__main__':
    main()
