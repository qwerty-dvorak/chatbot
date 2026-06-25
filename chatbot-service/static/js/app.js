(function(){'use strict';

/* ── Minimal client-side syntax highlighter ──────────────────────────────────
 *     Zero external dependencies. Handles the most common languages seen in
 *     LLM output. Drop-in replacement for highlight.js on this project.
 *     Supports: Python, JavaScript/JSX, TypeScript, HTML, CSS, JSON, YAML,
 *     SQL, Bash, C/C++/C#, Java, Rust, Go, Ruby, PHP, Diff, Markdown.
 *     Usage:  highlightElement(el)  –  applies spans inside the element.
 */

const LANG = {
  python: {
    kw: /\b(False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b/,
    builtin: /\b(__import__|abs|all|any|bin|bool|bytearray|bytes|callable|chr|classmethod|compile|complex|delattr|dict|dir|divmod|enumerate|eval|exec|filter|float|format|frozenset|getattr|globals|hasattr|hash|help|hex|id|input|int|isinstance|issubclass|iter|len|list|locals|map|max|memoryview|min|next|object|oct|open|ord|pow|print|property|range|repr|reversed|round|set|setattr|slice|sorted|staticmethod|str|sum|super|tuple|type|vars|zip)\b/,
    deco: /^(\s*)(@\w+)/m,
  },
  javascript: {
    kw: /\b(await|break|case|catch|class|const|continue|debugger|default|delete|do|else|enum|export|extends|false|finally|for|function|if|implements|import|in|instanceof|interface|let|new|null|of|package|private|protected|public|return|static|super|switch|this|throw|true|try|typeof|undefined|var|void|while|with|yield|async|from|get|set|of|module)\b/,
    builtin: /\b(Array|Boolean|console|Date|document|Error|fetch|JSON|Map|Math|Number|Object|Promise|RegExp|Set|String|Symbol|URL|WeakMap|WeakSet|window)\b/,
  },
  typescript: {
    kw: /\b(any|as|asserts|async|await|boolean|break|case|catch|class|const|constructor|continue|declare|default|delete|do|else|enum|export|extends|false|finally|for|from|function|get|if|implements|import|in|infer|instanceof|interface|is|keyof|let|module|namespace|never|new|null|of|package|private|protected|provides|public|readonly|record|require|return|satisfies|set|static|string|super|switch|symbol|this|throw|true|try|type|typeof|undefined|union|unique|unknown|var|void|while|with|yield)\b/,
    builtin: /\b(Array|Boolean|console|Date|Error|fetch|JSON|Map|Math|never|null|Number|Object|Promise|RegExp|Set|string|symbol|String|undefined|URL|WeakMap|WeakSet)\b/,
  },
  html: {
    tag: /(&lt;\/?)([\w-]+)([\s>])/g,
    attr: /\s([\w:-]+)(=)?/g,
    entity: /&(?:amp|lt|gt|quot|#\d+);/g,
  },
  css: {
    prop: /([\w-]+)\s*:/g,
    val: /:\s*(#[0-9a-fA-F]{3,8}|rgba?|hsla?|[.\w]+)/g,
    pseudo: /::?[\w-]+/g,
  },
  json: { str: /"(?:[^"\\]|\\.)*"/g, num: /\b-?\d+\.?\d*([eE][+-]?\d+)?\b/g },
  yaml: { key: /^[\t ]*[\w-]+\s*:/m, anchor: /[&*][\w-]+/g },
  sql: { kw: /\b(SELECT|FROM|WHERE|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|ALTER|DROP|INDEX|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AND|OR|NOT|IN|LIKE|BETWEEN|IS|NULL|AS|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|UNION|ALL|DISTINCT|CASE|WHEN|THEN|ELSE|END|EXISTS|FOREIGN|KEY|PRIMARY|REFERENCES|CASCADE|BEGIN|COMMIT|ROLLBACK|TRANSACTION|GRANT|REVOKE)\b/i },
  bash: { kw: /\b(if|then|else|elif|fi|for|while|do|done|case|esac|function|return|local|export|set|unset|trap|exit|source|alias|type|declare|echo|printf|read|test|let|exec)\b/, builtin: /\b(cd|ls|rm|cp|mv|mkdir|chmod|chown|grep|sed|awk|cat|head|tail|sort|wc|find|xargs|tar|gzip|curl|wget|ps|kill|mount|df|du|sudo|docker|git|make|pip|npm|yarn|node|python|ruby|gem|bundle|ssh|scp|rsync)\b/ },
  c: { kw: /\b(auto|break|case|char|const|continue|default|do|double|else|enum|extern|float|for|goto|if|int|long|register|return|short|signed|sizeof|static|struct|switch|typedef|union|unsigned|void|volatile|while)\b/, pre: /^(\s*)(#\s*(?:include|define|ifdef|ifndef|endif|pragma|error|warning)\b.*)/m },
  cpp: { kw: /\b(auto|break|case|catch|char|class|const|constexpr|continue|decltype|default|delete|do|double|else|enum|explicit|export|extern|float|for|friend|goto|if|inline|int|long|mutable|namespace|new|noexcept|nullptr|operator|override|private|protected|public|register|return|short|signed|sizeof|static|static_cast|struct|switch|template|this|throw|true|try|typedef|typeid|typename|union|unsigned|using|virtual|void|volatile|while)\b/ },
  java: { kw: /\b(abstract|assert|boolean|break|byte|case|catch|char|class|const|continue|default|do|double|else|enum|extends|false|final|finally|float|for|goto|if|implements|import|instanceof|int|interface|long|native|new|null|package|private|protected|public|return|short|static|strictfp|super|switch|synchronized|this|throw|throws|transient|true|try|void|volatile|while)\b/ },
  rust: { kw: /\b(as|async|await|break|const|continue|crate|dyn|else|enum|extern|false|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|ref|return|self|Self|static|struct|super|trait|true|type|unsafe|use|where|while|yield)\b/, builtin: /\b(Box|Option|Result|String|Vec|HashMap|Arc|Mutex|Rc|Cell|RefCell|Clone|Copy|Debug|Display|Eq|PartialEq|Ord|PartialOrd|Hash|Default|Iterator|IntoIterator|From|Into|TryFrom|TryInto|ToString|as_mut|as_ref|clone|collect|expect|filter|into_iter|is_ok|is_err|iter|map|ok|unwrap|unwrap_or|unwrap_or_else)\b/ },
  go: { kw: /\b(break|case|chan|const|continue|default|defer|else|fallthrough|for|func|go|goto|if|import|interface|map|package|range|return|select|struct|switch|type|var)\b/, builtin: /\b(append|cap|close|complex|copy|delete|imag|len|make|new|panic|print|println|real|recover|string|bool|byte|error|float32|float64|int|int8|int16|int32|int64|rune|uint|uint8|uint16|uint32|uint64|uintptr)\b/ },
  ruby: { kw: /\b(BEGIN|END|alias|and|begin|break|case|class|def|defined|do|else|elsif|end|ensure|false|for|if|in|module|next|nil|not|or|private|protected|public|raise|redo|rescue|retry|return|self|super|then|true|undef|unless|until|when|while|yield)\b/, sym: /:\w+/ },
  php: { kw: /\b(abstract|and|array|as|break|callable|case|catch|class|clone|const|continue|declare|default|die|do|echo|else|elseif|empty|enddeclare|endfor|endforeach|endif|endswitch|endwhile|eval|exit|extends|final|finally|for|foreach|function|global|goto|if|implements|include|include_once|instanceof|insteadof|interface|isset|list|match|namespace|new|or|print|private|protected|public|require|require_once|return|static|switch|throw|trait|try|unset|use|var|while|xor|yield|true|false|null|parent|self)\b/ },
  diff: { plus: /^\+.*/gm, minus: /^-.*/gm, meta: /^@@.*@@/gm, hdr: /^diff\s--git.*/gm, hdr2: /^---|\+\+\+/gm },
  markdown: { hdr: /^#{1,6}\s/m, link: /\[([^\]]+)\]\(([^)]+)\)/g, bold: /\*\*([^*\n]+)\*\*/g, italic: /\*([^*\n]+)\*/g, code: /`[^`\n]+`/g },
};

/* ── Tokenize a single line (or segment) of source code ──────────────────────*/
function tokenizeLine(line, langDef) {
  var tokens = [];
  var remaining = line;

  while (remaining.length > 0) {
    var best = null;
    var bestLen = 0;
    var bestType = '';

    /* check string literals (double-quoted first) */
    var dq = remaining.match(/^"([^"\\]|\\.)*"/);
    if (dq && dq[0].length > bestLen) { best = dq; bestLen = dq[0].length; bestType = 's'; }
    var sq = remaining.match(/^'([^'\\]|\\.)*'/);
    if (sq && sq[0].length > bestLen) { best = sq; bestLen = sq[0].length; bestType = 's'; }
    var bt = remaining.match(/^`([^`\\]|\\.)*`/);
    if (bt && bt[0].length > bestLen) { best = bt; bestLen = bt[0].length; bestType = 's'; }

    /* comments // and # */
    var slc = remaining.match(/^\/\/[^\n]*/);
    if (slc && slc[0].length > bestLen) { best = slc; bestLen = slc[0].length; bestType = 'c'; }
    var hc = remaining.match(/^#[^\n]*/);
    if (hc && hc[0].length > bestLen) { best = hc; bestLen = hc[0].length; bestType = 'c'; }
    var mlc = remaining.match(/^\/\*[\s\S]*?\*\//);
    if (mlc && mlc[0].length > bestLen) { best = mlc; bestLen = mlc[0].length; bestType = 'c'; }

    /* numbers */
    var num = remaining.match(/^\b\d+\.?\d*([eE][+-]?\d+)?[fFlL]?\b/);
    if (num && num[0].length > bestLen) { best = num; bestLen = num[0].length; bestType = 'n'; }

    /* language-specific keywords and builtins */
    if (langDef) {
      if (langDef.kw) {
        var kwMatch = remaining.match(langDef.kw);
        if (kwMatch && kwMatch.index === 0 && kwMatch[0].length > bestLen) {
          best = kwMatch; bestLen = kwMatch[0].length; bestType = 'k';
        }
      }
      if (langDef.builtin) {
        var biMatch = remaining.match(langDef.builtin);
        if (biMatch && biMatch.index === 0 && biMatch[0].length > bestLen) {
          best = biMatch; bestLen = biMatch[0].length; bestType = 'b';
        }
      }
    }

    /* decorators (@foo) */
    var deco = remaining.match(/^@\w+/);
    if (deco && deco[0].length > bestLen) { best = deco; bestLen = deco[0].length; bestType = 'd'; }

    /* fallback: one char */
    if (!best) {
      tokens.push({ t: 'n', v: remaining[0] });
      remaining = remaining.slice(1);
      continue;
    }

    /* unhighlighted text before best match */
    if (best.index > 0) {
      tokens.push({ t: 'n', v: remaining.slice(0, best.index) });
    }
    tokens.push({ t: bestType, v: best[0] });
    remaining = remaining.slice(best.index + best[0].length);
  }
  return tokens;
}

function span(type) {
  var cls = 'hl-';
  if (type === 'k' || type === 'b') cls += 'kw';
  else if (type === 's') cls += 'str';
  else if (type === 'c') cls += 'cm';
  else if (type === 'n') cls += 'num';
  else if (type === 'd') cls += 'deco';
  else return null;
  return '<span class="' + cls + '">';
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ── Detect language from class or content heuristics ───────────────────────*/
function detectLang(el) {
  for (var i = 0; i < el.classList.length; i++) {
    var cls = el.classList[i];
    if (cls === 'language-python' || cls === 'lang-python' || cls === 'python') return 'python';
    if (cls.indexOf('language-') === 0) return cls.slice(9);
    if (cls.indexOf('lang-') === 0) return cls.slice(5);
  }
  var text = (el.textContent || '').trim().slice(0, 200);
  if (/^\s*(import |from |def |class |print\(|if __name)/m.test(text)) return 'python';
  if (/^\s*(const |let |var |function |=>|import |export |console\.)/m.test(text)) return 'javascript';
  if (/^</m.test(text)) return 'html';
  if (/^\s*[.#@][\w-]+\s*\{/m.test(text)) return 'css';
  if (/^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE)\b/mi.test(text)) return 'sql';
  if (/^\s*#!\/bin\/(bash|sh)/m.test(text)) return 'bash';
  return '';
}

/* ── Main highlight function (replaces hljs.highlightElement) ──────────────*/
window.highlightElement = function(el) {
  if (!el || !el.textContent) return;
  var lang = detectLang(el) || el.parentElement?.className?.match(/language-(\w+)/)?.[1];
  lang = lang || 'text';
  var langDef = LANG[lang] || null;
  var text = el.textContent;
  var lines = text.split('\n');
  var out = '';
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    var tokens = tokenizeLine(line, langDef);
    var lineOut = '';
    for (var t = 0; t < tokens.length; t++) {
      var tok = tokens[t];
      var tag = span(tok.t);
      if (tag) {
        lineOut += tag + escHtml(tok.v) + '</span>';
      } else {
        lineOut += escHtml(tok.v);
      }
    }
    out += lineOut + (i < lines.length - 1 ? '\n' : '');
  }
  el.innerHTML = out;
  el.setAttribute('data-highlighted', 'yes');
};

/* ── Initialize all code blocks on DOMContentLoaded ──────────────────────────*/
function initHighlighting(root) {
  root = root || document;
  var codes = root.querySelectorAll('pre code:not([data-highlighted])');
  codes.forEach(function(el) { try { window.highlightElement(el); } catch(e) {} });
}

/* auto-init on load */
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function() { initHighlighting(); });
} else {
  initHighlighting();
}

/* ── Export utility functions ────────────────────────────────────────────────*/
window.renderCodeBlock = function(code, lang) {
  var langLabel = lang || 'code';
  var codeId = 'cb-' + Math.random().toString(36).slice(2, 8);
  var esc = escHtml(code);
  return '<div class="code-block-header"><span>' + langLabel + '</span><span>' +
    '<button class="code-block-btn" onclick="copyCode(\'' + codeId + '\')" title="Copy code">📋 Copy</button>' +
    '<button class="code-block-btn" onclick="downloadCode(\'' + codeId + '\', \'' + langLabel + '\')" title="Download" style="margin-left:4px">⬇ Download</button>' +
    '</span></div>' +
    '<pre><code class="language-' + langLabel + '" id="' + codeId + '">' + esc + '</code></pre>';
};

/* ── Toast system ────────────────────────────────────────────────────────────*/
window.showToast = function(message, type) {
  type = type || 'info';
  var container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
  var toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  var msgSpan = document.createElement('span');
  msgSpan.textContent = message;
  toast.appendChild(msgSpan);
  var closeBtn = document.createElement('button');
  closeBtn.className = 'toast-close';
  closeBtn.textContent = '✕';
  closeBtn.addEventListener('click', function() { toast.remove(); });
  toast.appendChild(closeBtn);
  container.appendChild(toast);
  setTimeout(function() {
    if (toast.parentNode) toast.remove();
  }, 5000);
};

/* ── Clipboard helpers ──────────────────────────────────────────────────────*/
window.copyCode = function(id) {
  var el = document.getElementById(id);
  if (!el) return;
  var text = el.textContent || el.innerText;
  navigator.clipboard.writeText(text).then(function() {
    var btn = el.closest('pre').previousElementSibling.querySelector('.code-block-btn');
    if (btn) { btn.textContent = '✓ Copied'; btn.classList.add('copied'); setTimeout(function(){ btn.textContent = '📋 Copy'; btn.classList.remove('copied'); }, 2000); }
  }).catch(function() {});
};

window.downloadCode = function(id, lang) {
  var el = document.getElementById(id);
  if (!el) return;
  var text = el.textContent || el.innerText;
  var blob = new Blob([text], {type: 'text/plain'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'code.' + (lang || 'txt');
  a.click();
  URL.revokeObjectURL(a.href);
};

})();
