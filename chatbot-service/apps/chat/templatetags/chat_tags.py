import re
from django import template
from django.utils.html import escape, mark_safe

register = template.Library()

TOOL_CALL_RE = re.compile(
    r'<\|tool_call\|>\s*call:(\w+(?:\.\w+)*)\{([^}]*)\}\s*<\|tool_call\|>'
)

def _render_markdown(text):
    text = re.sub(r'^#{3}\s+(.*?)$', r'<h3>\1</h3>', text, flags=re.M)
    text = re.sub(r'^#{2}\s+(.*?)$', r'<h2>\1</h2>', text, flags=re.M)
    text = re.sub(r'^#\s+(.*?)$', r'<h1>\1</h1>', text, flags=re.M)
    text = re.sub(r'```(\w*)\n(.*?)```', r'<pre><code>\2</code></pre>', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', text)
    text = re.sub(r'^\- (.*?)$', r'<li>\1</li>', text, flags=re.M)
    text = re.sub(r'(<li>.*?</li>)', r'<ul>\1</ul>', text)
    text = re.sub(r'</ul>\s*<ul>', '', text)
    text = re.sub(r'\n\n', r'</p><p>', text)
    text = '<p>' + text + '</p>'
    return text

@register.filter
def format_content(text):
    if not text:
        return ""
    parts = []
    last_end = 0
    for m in TOOL_CALL_RE.finditer(text):
        start, end = m.start(), m.end()
        if start > last_end:
            parts.append(_render_markdown(text[last_end:start]))
        name = m.group(1)
        args = m.group(2).replace('<|"|>', '"').replace("<|'|>", '"')
        parts.append(
            f'<div class="tool-call-block">'
            f'<span class="tc-icon">🔧</span>'
            f'<span class="tc-name">{escape(name)}</span>'
            f'<code class="tc-args">{escape(args)}</code>'
            f'</div>'
        )
        last_end = end
    if last_end < len(text):
        parts.append(_render_markdown(text[last_end:]))
    return mark_safe("".join(parts))
