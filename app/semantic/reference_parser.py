# -*- coding: utf-8 -*-
"""
文献解析（独立于内容生成）

目标：
- 支持 URL / 本地文件（txt/md/html）解析为可供写作引用的“内容摘录 + 元信息”
- 解析模块不依赖 core.engine，避免与生成耦合
"""

from __future__ import annotations

import html as _html
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class ParsedReference:
    title: str | None
    citation_key: str
    content_extract: str
    meta: dict


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def _strip_html(s: str) -> str:
    if not s:
        return ""
    # crude strip + unescape
    s = _TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _WS_RE.sub(" ", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = _MULTI_NL_RE.sub("\n\n", s)
    return s.strip()


def _guess_title_from_html(raw_html: str) -> str | None:
    if not raw_html:
        return None
    m = re.search(r"<title[^>]*>([\s\S]*?)</title>", raw_html, flags=re.I)
    if not m:
        return None
    t = _strip_html(m.group(1))
    return t[:180] if t else None


def fetch_url(url: str, *, timeout_s: int = 20, max_chars: int = 300_000) -> tuple[str, dict]:
    """抓取 URL 返回 (raw_text, meta)。"""
    u = (url or "").strip()
    if not u:
        raise ValueError("url 为空")
    parsed = urllib.parse.urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https URL")
    req = urllib.request.Request(
        u,
        headers={
            "User-Agent": "book-Agent/1.0 (+local)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
        },
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        ct = (resp.headers.get("Content-Type") or "").lower()
        raw = resp.read()
    elapsed_ms = int((time.time() - started) * 1000)
    # best-effort decode
    text = ""
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc, errors="replace")
            break
        except Exception:
            continue
    if not text:
        text = raw.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars]
    return text, {"url": u, "content_type": ct, "fetch_elapsed_ms": elapsed_ms}


def parse_url_to_reference(
    url: str,
    *,
    citation_key: str,
    max_extract_chars: int = 4000,
) -> ParsedReference:
    raw, meta = fetch_url(url)
    title = _guess_title_from_html(raw)
    extract = _strip_html(raw)
    if len(extract) > max_extract_chars:
        extract = extract[:max_extract_chars] + "…"
    meta = {**meta, "source": "url", "title": title}
    return ParsedReference(title=title, citation_key=citation_key, content_extract=extract, meta=meta)


def parse_local_text_file(
    file_path: str,
    *,
    citation_key: str,
    max_extract_chars: int = 4000,
) -> ParsedReference:
    p = (file_path or "").strip()
    if not p:
        raise ValueError("file_path 为空")
    with open(p, "rb") as f:
        raw = f.read()
    text = ""
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc, errors="replace")
            break
        except Exception:
            continue
    if not text:
        text = raw.decode("utf-8", errors="replace")
    # 若是 html，做简单 strip
    if re.search(r"<html|<body|<title", text[:4000], flags=re.I):
        title = _guess_title_from_html(text)
        extract = _strip_html(text)
    else:
        title = None
        extract = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(extract) > max_extract_chars:
        extract = extract[:max_extract_chars] + "…"
    meta = {"source": "file", "file_path": p, "title": title}
    return ParsedReference(title=title, citation_key=citation_key, content_extract=extract, meta=meta)

