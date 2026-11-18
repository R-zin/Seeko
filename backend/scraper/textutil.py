"""HTML -> readable plain text that keeps paragraph and list structure (job descriptions are stored as text)."""
from __future__ import annotations
import html as _html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "section", "article", "header", "footer", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "h5",
          "h6", "blockquote", "pre", "dl", "dt", "dd", "hr", "main", "aside", "nav", "form", "fieldset"}
_SKIP = {"script", "style", "noscript", "template", "svg", "head"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP: self.skip += 1
        elif tag == "br": self.out.append("\n")
        elif tag == "li": self.out.append("\n• ")
        elif tag in ("td", "th"): self.out.append(" ")
        elif tag in _BLOCK: self.out.append("\n\n")

    def handle_startendtag(self, tag, attrs):
        if tag == "br": self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP: self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK: self.out.append("\n")

    def handle_data(self, data):
        if not self.skip: self.out.append(data)


def tidy(text: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v ]+", " ", ln).strip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(markup: str) -> str:
    """Accepts real HTML or entity-escaped HTML (e.g. Greenhouse `content`, JSON-LD descriptions)."""
    if not markup:
        return ""
    if "<" not in markup and "&lt;" in markup:
        markup = _html.unescape(markup)
    p = _Text()
    try:
        p.feed(markup); p.close()
    except Exception:   # malformed markup: fall back to a crude strip
        return tidy(_html.unescape(re.sub(r"<[^>]+>", " ", markup)))
    return tidy("".join(p.out))
