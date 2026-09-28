"""Local review output: per-article JSON previews, an HTML review page, console reports.

Everything shown here comes from untrusted feeds or the AI, so all HTML output is
escaped and only validated http(s) URLs become links.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from techspire.config import GRAPH_HOST
from techspire.enums import ArticleStatus
from techspire.fileio import atomic_write_bytes
from techspire.models import RunSummary, StoredArticle, format_utc, utcnow
from techspire.url_utils import is_http_url

RULE = "=" * 50
COMMENT_PREFIX = "Read full story: "


def comment_text(original_url: str) -> str:
    """The future source-link comment (the one definition used for posting and previews)."""
    return f"{COMMENT_PREFIX}{original_url}"


def future_payload(article: StoredArticle, graph_version: str) -> dict[str, Any]:
    """What a future live publish would send (no token, no page ID)."""
    return {
        "step_1_photo_post": {
            "request": f"POST {GRAPH_HOST}/{graph_version}/{{page-id}}/photos",
            "multipart_fields": {"source": article.image_path.name if article.image_path else None,
                                 "caption": article.facebook_caption, "published": "true"},
        },
        "step_2_source_comment": {
            "request": f"POST {GRAPH_HOST}/{graph_version}/{{post-id}}/comments",
            "form_fields": {"message": comment_text(article.original_url)},
        },
        "authorization": "Authorization: Bearer <FB_PAGE_ACCESS_TOKEN> (header only; never shown)",
    }


def write_preview(article: StoredArticle, previews_dir: Path, graph_version: str) -> Path:
    """JSON preview written as the article becomes READY_FOR_REVIEW."""
    if article.category is None or article.image_path is None:
        raise ValueError("preview requires an approved article with an image")
    data = {
        "article_id": article.id,
        "state": ArticleStatus.READY_FOR_REVIEW.value,
        "source": article.source_name,
        "original_title": article.title,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "category": article.category.value,
        "headline": article.catchy_headline,
        "caption": article.facebook_caption,
        "original_url": article.original_url,
        "image_path": str(article.image_path),
        "future_facebook_payload": future_payload(article, graph_version),
        "facebook_write_performed": False,
        "generated_at": utcnow().isoformat(timespec="seconds"),
    }
    path = previews_dir / f"{article.image_path.stem}.json"
    atomic_write_bytes(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
    return path


def write_review_page(path: Path, ready: list[StoredArticle], rejected: list[StoredArticle],
                      failed: list[StoredArticle], mode_label: str, posted: int = 0) -> Path:
    """Static HTML page listing everything waiting for human review."""
    cards = "\n".join(_card_html(a, path.parent) for a in ready) or \
        '<p class="empty">Nothing is waiting for review yet. Run a dry run to generate cards.</p>'
    rejected_rows = "\n".join(
        f"<li><span class=\"id\">#{a.id}</span> {_e(a.title)} <span class=\"muted\">({_e(a.source_name)})</span>"
        f"<br><span class=\"reason\">{_e(a.rejection_reason or '')}</span></li>" for a in rejected
    ) or "<li class=\"muted\">None.</li>"
    failed_rows = "\n".join(
        f"<li><span class=\"id\">#{a.id}</span> {_e(a.title)} <span class=\"muted\">[{_e(a.error_stage or '')}, "
        f"attempts {a.retry_count}]</span><br><span class=\"reason\">{_e(a.last_error or '')}</span></li>"
        for a in failed
    ) or "<li class=\"muted\">None.</li>"
    posted_line = ("NOTHING HAS BEEN POSTED TO FACEBOOK" if posted == 0
                   else f"{posted} ARTICLE(S) HAVE BEEN POSTED TO FACEBOOK")
    page = _PAGE.format(
        mode=_e(mode_label), posted=posted_line, generated=format_utc(utcnow()), count=len(ready), cards=cards,
        rejected=rejected_rows, failed=failed_rows,
    )
    atomic_write_bytes(path, page.encode("utf-8"))
    return path


def _card_html(article: StoredArticle, previews_dir: Path) -> str:
    image = ""
    if article.image_path:
        try:
            rel = Path(os.path.relpath(article.image_path, previews_dir)).as_posix()
        except ValueError:  # different drive on Windows
            rel = article.image_path.resolve().as_uri()
        alt = _e(article.catchy_headline or "")
        image = f'<img src="{_e(rel)}" alt="{alt}" width="1200" height="630" loading="lazy">'
    link = (f'<a href="{_e(article.original_url)}" rel="noopener noreferrer" target="_blank">'
            f"{_e(article.original_url)}</a>") if is_http_url(article.original_url) else _e(article.original_url)
    published = format_utc(article.published_at) if article.published_at else "unknown"
    return f"""<article class="card">
  {image}
  <div class="body">
    <p class="meta"><span class="pill">{_e(article.category.value if article.category else "")}</span>
      <span>#{article.id}</span><span>{_e(article.source_name)}</span><span>{published}</span></p>
    <h2>{_e(article.catchy_headline or "")}</h2>
    <p class="label">Caption</p>
    <p class="caption">{_e(article.facebook_caption or "")}</p>
    <p class="label">Future source comment</p>
    <p class="comment">{_e(COMMENT_PREFIX)}{link}</p>
    <p class="label">Original title</p>
    <p class="muted">{_e(article.title)}</p>
    <p class="state">State: {_e(article.status.value)} &middot; Facebook write performed: NO</p>
  </div>
</article>"""


def format_dry_run_block(article: StoredArticle) -> str:
    """Console block for an approved article, in the operator-facing format."""
    fields = [
        ("Article", article.title),
        ("Source", article.source_name),
        ("Decision", "APPROVED"),
        ("Category", article.category.value if article.category else ""),
        ("Headline", article.catchy_headline or ""),
        ("Caption", article.facebook_caption or ""),
        ("Source URL", article.original_url),
        ("Generated Image", str(article.image_path or "")),
        ("State", article.status.value),
        ("Facebook write performed", "NO"),
    ]
    body = "\n\n".join(f"{label}:\n{value}" for label, value in fields)
    return f"{RULE}\nTECHSPIRE DRY RUN\n{RULE}\n\n{body}\n{RULE}"


def format_summary(summary: RunSummary, review_page: Path, safety_line: str) -> str:
    lines = [
        "-" * 50,
        f"RUN SUMMARY ({summary.mode})  run {summary.run_id}",
        f"  Feeds:              {summary.feeds_ok} ok, {summary.feeds_failed} failed",
        f"  Articles fetched:   {summary.fetched}  (too old: {summary.too_old}, undated: {summary.undated}, "
        f"already known: {summary.duplicates}, same story: {summary.same_story})",
        f"  Interrupted work:   {summary.recovered} recovered, {summary.resumed} resumed",
        f"  AI decisions:       {summary.analyzed}  (approved: {summary.approved}, rejected: {summary.rejected})",
        f"  Failed steps:       {summary.failed}  (retried automatically on later runs, up to the limit)",
        f"  Ready for review:   {summary.ready_for_review}",
        f"  Review page:        {review_page}",
        f"  Facebook writes performed: {summary.facebook_writes}",
        f"  {safety_line}",
        "-" * 50,
    ]
    return "\n".join(lines)


def _e(value: str) -> str:
    return html.escape(value, quote=True)


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' file: data:; style-src 'unsafe-inline'">
<title>Techspire Review Queue</title>
<style>
  :root {{ --bg:#141821; --panel:#1b2130; --line:#2a3242; --text:#f1f5f9; --muted:#94a3b8; --accent:#38bdf8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.55 "Segoe UI",system-ui,sans-serif; }}
  header {{ padding:28px 32px 20px; border-bottom:1px solid var(--line); }}
  h1 {{ margin:0 0 6px; font-size:22px; letter-spacing:.04em; }}
  .banner {{ display:inline-block; margin-top:10px; padding:6px 12px; border:1px solid var(--accent);
            border-radius:999px; color:var(--accent); font-weight:600; font-size:13px; letter-spacing:.05em; }}
  main {{ padding:24px 32px 48px; max-width:1400px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:24px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
  .card img {{ display:block; width:100%; height:auto; border-bottom:1px solid var(--line); }}
  .body {{ padding:16px 18px 18px; }}
  .meta {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:0 0 8px; color:var(--muted); font-size:13px; }}
  .pill {{ color:var(--accent); border:1px solid var(--accent); border-radius:999px; padding:1px 10px; font-weight:600; }}
  h2 {{ font-size:18px; margin:4px 0 12px; line-height:1.35; }}
  .label {{ margin:12px 0 2px; font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }}
  .caption {{ margin:0; white-space:pre-wrap; }}
  .comment, .muted {{ margin:0; color:var(--muted); overflow-wrap:anywhere; }}
  a {{ color:var(--accent); }}
  .state {{ margin:14px 0 0; font-size:13px; color:var(--muted); border-top:1px solid var(--line); padding-top:10px; }}
  section {{ margin-top:40px; }}
  section h3 {{ font-size:16px; margin:0 0 10px; }}
  ul {{ margin:0; padding-left:18px; }}
  li {{ margin-bottom:8px; }}
  .reason {{ color:var(--muted); font-size:14px; }}
  .id {{ color:var(--accent); font-weight:600; }}
  .empty {{ color:var(--muted); }}
  @media (max-width:520px) {{ header, main {{ padding-left:16px; padding-right:16px; }} .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>TECHSPIRE OFFICIAL &middot; Review queue</h1>
  <div class="muted">{count} article(s) ready for review &middot; generated {generated}</div>
  <div class="banner">{mode} &middot; {posted}</div>
</header>
<main>
  <div class="grid">
{cards}
  </div>
  <section><h3>Recently rejected by the editorial filter</h3><ul>
{rejected}
  </ul></section>
  <section><h3>Failed (retried automatically up to the configured limit)</h3><ul>
{failed}
  </ul></section>
</main>
</body>
</html>
"""  # noqa: E501 - HTML/CSS template
