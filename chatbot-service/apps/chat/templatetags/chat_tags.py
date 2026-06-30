import re
import uuid

from django import template
from django.utils.html import escape, mark_safe

register = template.Library()

TOOL_CALL_RE = re.compile(
    r"(?:<\|tool_call\|>\s*)?call:(\w+(?:\.\w+)*)\{([^}]*)\}(?:\s*<\|tool_call\|>)?"
)

def _render_code_block(code, lang):
    cid = "cb-" + uuid.uuid4().hex[:8]
    lang_label = lang or "code"
    header = (
        f'<div class="code-block-header">'
        f'<span>{escape(lang_label)}</span>'
        f'<span>'
        f'<button class="code-block-btn" onclick="copyCode(\'{cid}\')" title="Copy code">📋 Copy</button>'
        f'<button class="code-block-btn" onclick="downloadCode(\'{cid}\', \'{escape(lang_label)}\')" title="Download" style="margin-left:4px">⬇ Download</button>'
        f'</span></div>'
    )
    lang_class = f' class="language-{escape(lang)}"' if lang else ""
    return header + f'<pre><code{lang_class} id="{cid}">{code}</code></pre>'

def _render_markdown(text):
    text = re.sub(r"^#{3}\s+(.*?)$", r"<h3>\1</h3>", text, flags=re.MULTILINE)
    text = re.sub(r"^#{2}\s+(.*?)$", r"<h2>\1</h2>", text, flags=re.MULTILINE)
    text = re.sub(r"^#\s+(.*?)$", r"<h1>\1</h1>", text, flags=re.MULTILINE)

    def _replace_code_block(m):
        lang = m.group(1) or ""
        code = m.group(2)
        return _render_code_block(code, lang)

    text = re.sub(r"```(\w*)\n(.*?)```", _replace_code_block, text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"^\- (.*?)$", r"<li>\1</li>", text, flags=re.MULTILINE)
    text = re.sub(r"(<li>.*?</li>)", r"<ul>\1</ul>", text)
    text = re.sub(r"</ul>\s*<ul>", "", text)
    text = re.sub(r"\n\n", r"</p><p>", text)
    return "<p>" + text + "</p>"

@register.filter
def format_content(text):
    if not text:
        return ""
    cleaned = TOOL_CALL_RE.sub("", text)
    return mark_safe(_render_markdown(cleaned.strip()))
