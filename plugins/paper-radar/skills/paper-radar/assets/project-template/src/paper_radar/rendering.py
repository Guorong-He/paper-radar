import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Dict, Iterable

from .config import load_profile
from .models import PaperAnalysis, ScoredPaper
from .venues import matches_preferred_venue


TIER_LABELS = {
    "tier1": "Tier 1 旗舰正式论文",
    "tier2": "Tier 2 高质量正式论文",
    "preprint": "arXiv / preprint",
    "other": "其他正式论文",
}

THEME_PATTERNS = [
    ("动力学对称与形态设计", ["dynamic symmetry", "dynamic isotropy", "omnidirectional", "morphology", "20-leg", "20 条"]),
    ("轮腿自主导航", ["wheeled-legged", "legged", "locomotion", "navigation", "mobile robot", "城市级"]),
    ("触觉闭环操作", ["tactile", "haptic", "slip", "grasp", "dexterous", "触觉", "滑移", "抓取"]),
    ("软体本体感知", ["soft robot", "fluidic", "strain sensor", "deformation", "软体", "应变", "流体"]),
    ("视觉-触觉融合", ["vision", "multimodal", "sensor fusion", "视觉", "多模态", "融合"]),
    ("机器人学习数据", ["robot learning", "reinforcement learning", "demonstration", "VLA", "RL", "示范"]),
    ("长时程操作规划", ["hierarchical", "sequential", "manipulation network", "planning", "长时程", "层级"]),
    ("液体与复杂环境感知", ["droplet", "underwater", "aquatic", "liquid", "液滴", "水下", "湿"]),
]


def render_outputs(
    scored_papers: Iterable[ScoredPaper],
    analyses_by_paper_id: Dict[str, PaperAnalysis],
    paper_id_fn,
    output_dir: str = "output",
    issue_date: date = None,
    issue_editorial: dict = None,
) -> None:
    items = order_items_for_display(list(scored_papers))
    issue_date = issue_date or date.today()
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    digest_url = "digest.html"
    (path / "email.html").write_text(
        render_email(items, analyses_by_paper_id, paper_id_fn, digest_url, issue_date, issue_editorial=issue_editorial),
        encoding="utf-8",
    )
    (path / "digest.html").write_text(
        render_digest_page(items, analyses_by_paper_id, paper_id_fn, issue_date, issue_editorial=issue_editorial),
        encoding="utf-8",
    )


def order_items_for_display(items):
    profile = load_profile()

    def key(pair):
        original_idx, item = pair
        tier_order = {"tier1": 0, "tier2": 1, "preprint": 2, "other": 3}[paper_tier(item, profile)["key"]]
        published = item.paper.published_at.toordinal() if item.paper.published_at else 0
        return (tier_order, -published, -item.score.total_score, original_idx)

    return [item for _, item in sorted(enumerate(items), key=key)]


def paper_tier(item, profile=None):
    profile = profile or load_profile()
    if item.paper.source == "arxiv":
        key = "preprint"
    elif matches_preferred_venue(item.paper.venue or "", profile.get("tier1_broad_venues", [])):
        key = "tier1"
    elif matches_preferred_venue(item.paper.venue or "", profile.get("preferred_venues", [])):
        key = "tier2"
    else:
        key = "other"
    return {"key": key, "label": TIER_LABELS[key]}


def build_issue_context(items, analyses_by_paper_id=None, paper_id_fn=None, issue_editorial=None):
    profile = load_profile()
    tier_counts = {key: 0 for key in TIER_LABELS}
    for item in items:
        tier_counts[paper_tier(item, profile)["key"]] += 1
    direct_count = sum(1 for item in items if item.paper.paper_type == "direct")
    transferable_count = len(items) - direct_count
    themes = dominant_themes(items)
    lead_theme = item_theme(items[0]) if items else ""
    if lead_theme and lead_theme in themes:
        themes = [lead_theme] + [theme for theme in themes if theme != lead_theme]
    top_themes = themes[:3] or ["具身感知", "机器人系统", "触觉闭环"]
    lead_analysis = None
    issue_analyses = []
    if items and analyses_by_paper_id and paper_id_fn:
        lead_analysis = analyses_by_paper_id.get(paper_id_fn(items[0].paper))
        issue_analyses = [
            analyses_by_paper_id[paper_id_fn(item.paper)]
            for item in items
            if paper_id_fn(item.paper) in analyses_by_paper_id
        ]
    title_focus = "具身智能/感知精选"
    issue_editorial = issue_editorial or {}
    lede = _editorial_override(issue_editorial.get("lede"), max_len=72) or editorial_lede(
        top_themes,
        lead_analysis,
        issue_analyses,
    )
    panel_quote = _editorial_override(issue_editorial.get("panel_quote"), max_len=150) or editorial_signal(
        top_themes,
        lead_analysis,
        issue_analyses,
    )
    return {
        "title_html": html.escape(title_focus),
        "email_title": title_focus + f" {len(items)} 篇",
        "lede": lede,
        "panel_quote": panel_quote,
        "panel_note": (
            f"{len(items)} 篇论文 · {tier_counts['tier1']} 篇旗舰正式论文 · "
            f"{tier_counts['tier2']} 篇高质量正式论文 · {tier_counts['preprint']} 篇 arXiv/preprint；"
            f"{direct_count} 篇直接命中，{transferable_count} 篇可迁移。"
        ),
        "tier1_count": tier_counts["tier1"],
        "tier2_count": tier_counts["tier2"],
        "preprint_count": tier_counts["preprint"],
    }


def dominant_themes(items):
    scored = []
    profile = load_profile()
    for label, patterns in THEME_PATTERNS:
        weight = 0
        for idx, item in enumerate(items):
            haystack = " ".join(
                [
                    item.paper.title or "",
                    item.paper.abstract or "",
                    item.paper.venue or "",
                    " ".join(item.paper.robot_type_tags or []),
                ]
            ).lower()
            if any(pattern.lower() in haystack for pattern in patterns):
                tier = paper_tier(item, profile)["key"]
                weight += {"tier1": 4, "tier2": 2, "preprint": 1, "other": 1}[tier]
                if idx < 3:
                    weight += 2
        if weight:
            scored.append((weight, label))
    return [label for _, label in sorted(scored, key=lambda pair: (-pair[0], pair[1]))]


def editorial_lede(themes, lead_analysis=None, issue_analyses=None):
    issue_analyses = issue_analyses or []
    if lead_analysis:
        focus = _issue_focus_phrase(themes)
        predicate = _abstract_value_predicate(lead_analysis, issue_analyses)
        return _finish_editorial_sentence(f"本期围绕{focus}，核心问题是让感知{predicate}")

    shared = _shared_issue_clause(issue_analyses, soft_limit=44)
    if shared:
        return _finish_editorial_sentence(f"本期沿着{shared}展开，把感知重新放回行动现场")

    return _theme_fallback_lede(themes)


def editorial_signal(themes, lead_analysis=None, issue_analyses=None):
    issue_analyses = issue_analyses or []
    if lead_analysis:
        old_problem = _editorial_clause(
            lead_analysis.old_problem or lead_analysis.problem_frame,
            soft_limit=30,
        )
        novelty = _editorial_clause(
            lead_analysis.true_novelty or lead_analysis.mechanism,
            soft_limit=36,
        )
        evidence = _editorial_clause(lead_analysis.evidence_summary, soft_limit=34)
        if old_problem and novelty and evidence:
            return _finish_editorial_sentence(
                f"导读：先看{old_problem}如何限制真实任务，再看{novelty}如何由{evidence}支撑"
            )
        if old_problem and novelty:
            return _finish_editorial_sentence(f"导读：先看{old_problem}为何难，再看{novelty}如何回应")

    shared = _shared_issue_clause(issue_analyses, soft_limit=34)
    if shared:
        return _finish_editorial_sentence(f"导读：本期的共同线索是{shared}")

    return _theme_fallback_signal(themes)


def _theme_fallback_lede(themes) -> str:
    selected = [theme for theme in (themes or []) if theme][:2]
    if selected:
        return _finish_editorial_sentence(f"本期围绕{'与'.join(selected)}，追问感知如何转化为可靠行动")
    return "本期像一组路标：感知不止回答“看见了什么”，还要回答“下一步怎么动”。"


def _theme_fallback_signal(themes) -> str:
    selected = [theme for theme in (themes or []) if theme][:2]
    if selected:
        return _finish_editorial_sentence(f"导读：从{'到'.join(selected)}，看机器人如何把观察转化为动作")
    return "导读：这些论文共同追问一件事，感知如何变成可靠行动。"


def _editorial_override(text: str, max_len: int) -> str:
    if "..." in (text or "") or "…" in (text or ""):
        return ""
    value = _finish_editorial_sentence(text or "")
    if not value:
        return ""
    if len(value) > max_len:
        return ""
    return value


def _shared_issue_clause(analyses, soft_limit: int) -> str:
    for analysis in analyses:
        for text in (
            analysis.true_novelty,
            analysis.importance_reason,
            analysis.core_insight,
        ):
            clause = _editorial_clause(text, soft_limit=soft_limit)
            if clause:
                return clause
    return ""


def _issue_focus_phrase(themes) -> str:
    selected = [theme for theme in (themes or []) if theme][:2]
    return "与".join(selected) if selected else "具身感知"


def _abstract_value_predicate(lead_analysis, issue_analyses) -> str:
    text = " ".join(
        [
            _analysis_blob(lead_analysis),
            " ".join(_analysis_blob(analysis) for analysis in (issue_analyses or [])[:3]),
        ]
    )
    if any(term in text for term in ["风险", "鲁棒", "真实", "real-world", "sim-to-real", "纠错"]):
        return "管理真实环境中的行动风险"
    if any(term in text for term in ["接触", "触觉", "抓取", "dexterous", "manipulation"]):
        return "支撑接触后的闭环修正"
    if any(term in text for term in ["低延迟", "无人机", "导航", "uav", "drone", "navigation"]):
        return "进入低延迟行动闭环"
    if any(term in text for term in ["身体", "材料", "软体", "本体", "soft robot"]):
        return "成为身体内部的状态读数"
    return "转化为可执行的动作约束"


def _companion_problem_clause(analyses, lead_analysis, soft_limit: int) -> str:
    for analysis in analyses:
        if analysis is lead_analysis:
            continue
        for text in (
            analysis.problem_frame,
            analysis.core_insight,
            analysis.true_novelty,
        ):
            clause = _editorial_clause(text, soft_limit=soft_limit)
            if clause:
                return clause
    return ""


def _editorial_clause(text: str, soft_limit: int) -> str:
    value = _normalize_sentence(text)
    value = re.sub(r"^(这篇|本篇|论文|作者|系统|研究)\s*", "", value)
    value = re.sub(r"^(重要性在于|核心新意是|贡献在于|新意在于)", "", value)
    value = value.strip(" ：:，,。；;“”\"'")
    if not value:
        return ""
    parts = [part.strip(" ：:，,。；;“”\"'") for part in re.split(r"[。；;]", value) if part.strip()]
    value = parts[0] if parts else value
    if len(value) <= soft_limit:
        return value
    comma_parts = [part.strip(" ：:，,。；;“”\"'") for part in re.split(r"[，,]", value) if part.strip()]
    selected = []
    for part in comma_parts:
        candidate = "，".join(selected + [part])
        if selected and len(candidate) > soft_limit:
            break
        selected.append(part)
    return "，".join(selected or comma_parts[:1])


def _finish_editorial_sentence(text: str) -> str:
    value = _normalize_sentence(text).strip("。；; ")
    value = value.replace("...", "").replace("…", "")
    if not value:
        return ""
    return value + "。"


def _analysis_blob(analysis) -> str:
    if not analysis:
        return ""
    return " ".join(
        [
            analysis.problem_frame,
            analysis.mechanism,
            analysis.true_novelty,
            analysis.importance_reason,
        ]
    ).lower()


def item_theme(item):
    haystack = " ".join(
        [
            item.paper.title or "",
            item.paper.abstract or "",
            item.paper.venue or "",
            " ".join(item.paper.robot_type_tags or []),
        ]
    ).lower()
    for label, patterns in THEME_PATTERNS:
        if any(pattern.lower() in haystack for pattern in patterns):
            return label
    return ""


def quick_take(analysis: PaperAnalysis) -> str:
    problem = _natural_clause(analysis.problem_frame, soft_limit=54, max_clauses=1)
    method = _strip_method_prefix(_natural_clause(analysis.mechanism, soft_limit=76, max_clauses=2))
    evidence = _compact_evidence(analysis.evidence_summary, soft_limit=62)
    sentence = f"针对{problem}，论文通过{method}，并验证{evidence}。"
    return _normalize_sentence(sentence)


def _compact_evidence(text: str, soft_limit: int) -> str:
    value = re.sub(r"^[^；;。]*(正式论文|预印本)[；;。]\s*", "", text or "")
    value = re.sub(r"^(摘要)?报告\s*", "", value)
    value = re.sub(r"^作者\s*(报告|展示|验证)\s*", "", value)
    return _natural_clause(value, soft_limit=soft_limit, max_clauses=2)


def _natural_clause(text: str, soft_limit: int, max_clauses: int) -> str:
    value = _normalize_sentence(text)
    value = re.sub(r"^(这篇|论文|作者|系统)\s*", "", value)
    value = value.rstrip("。；;")
    if len(value) <= soft_limit:
        return value
    parts = [part.strip(" ，；;。") for part in re.split(r"[，；;。]", value) if part.strip(" ，；;。")]
    if not parts:
        return value
    selected = []
    for part in parts:
        candidate = "，".join(selected + [part])
        if selected and len(candidate) > soft_limit:
            break
        selected.append(part)
        if len(selected) >= max_clauses:
            break
    return "，".join(selected or parts[:1])


def _strip_method_prefix(text: str) -> str:
    value = re.sub(r"^(用|利用|通过)\s*", "", text or "")
    return value


def _normalize_sentence(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def render_email(items, analyses_by_paper_id, paper_id_fn, digest_url: str, issue_date: date, issue_editorial=None) -> str:
    issue = build_issue_context(items, analyses_by_paper_id, paper_id_fn, issue_editorial=issue_editorial)
    cards = []
    for idx, item in enumerate(items, start=1):
        analysis = analyses_by_paper_id[paper_id_fn(item.paper)]
        tier = paper_tier(item)
        cards.append(
            f"""
            <tr>
              <td style="padding:18px 0;border-top:1px solid #d9dee8;">
                <div style="font-size:12px;color:#667085;margin-bottom:6px;">#{idx:02d} · {html.escape(tier['label'])} · {html.escape(item.paper.venue or "Unknown")} · {item.paper.published_at.isoformat()}</div>
                <div style="font-size:18px;line-height:1.35;font-weight:600;color:#101828;margin-bottom:8px;">{html.escape(item.paper.title)}</div>
                <div style="font-size:14px;line-height:1.65;color:#344054;">{html.escape(quick_take(analysis))}</div>
              </td>
            </tr>
            """
        )
    return f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f5f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#101828;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f7fb;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:720px;background:#ffffff;border-radius:18px;padding:28px 28px 18px;border:1px solid #e4e7ec;">
        <tr><td>
          <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#475467;margin-bottom:10px;">Paper Radar · Weekly Digest</div>
          <div style="font-size:28px;line-height:1.2;font-weight:650;color:#101828;margin-bottom:12px;">{html.escape(issue['email_title'])}</div>
          <div style="font-size:15px;line-height:1.7;color:#344054;margin-bottom:18px;">
            {html.escape(issue['lede'])}
          </div>
          <a href="{digest_url}" style="display:inline-block;background:#0f766e;color:#ffffff;text-decoration:none;padding:12px 16px;border-radius:999px;font-size:14px;font-weight:600;margin-bottom:18px;">打开本期交互网页</a>
        </td></tr>
        {''.join(cards)}
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def render_digest_page(items, analyses_by_paper_id, paper_id_fn, issue_date: date, issue_editorial=None) -> str:
    items = order_items_for_display(list(items))
    issue = build_issue_context(items, analyses_by_paper_id, paper_id_fn, issue_editorial=issue_editorial)
    serializable = []
    for item in items:
        analysis = analyses_by_paper_id[paper_id_fn(item.paper)]
        tier = paper_tier(item)
        serializable.append(
            {
                "title": item.paper.title,
                "authors": item.paper.authors,
                "venue": item.paper.venue or "Unknown",
                "published_at": item.paper.published_at.isoformat(),
                "paper_type": item.paper.paper_type,
                "source_kind": "preprint" if item.paper.source == "arxiv" else "official",
                "robot_type_tags": item.paper.robot_type_tags or ["other"],
                "tier": tier["key"],
                "tier_label": tier["label"],
                "score": item.score.total_score,
                "url": item.paper.url,
                "pdf_url": item.paper.pdf_url,
                "key_figure_path": _relative_output_path(item.paper.key_figure_path),
                "key_figure_caption": item.paper.key_figure_caption,
                "quick_take": quick_take(analysis),
                "analysis": analysis.__dict__,
            }
        )
    data_json = json.dumps(serializable, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Paper Radar · {issue_date.isoformat()}</title>
  <style>
    :root {{
      color-scheme: light;
      --paper:#f4f1e8;
      --paper-strong:#fbfaf4;
      --ink:#181410;
      --ink-soft:#4a4037;
      --muted:#73695f;
      --line:#2a2119;
      --line-soft:rgba(42,33,25,.24);
      --rule:rgba(42,33,25,.44);
      --accent:#a93522;
      --accent-dark:#401d15;
      --signal:#2d6d65;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      background:
        linear-gradient(90deg, var(--accent) 0 18px, transparent 18px),
        repeating-linear-gradient(0deg, rgba(42,33,25,.026), rgba(42,33,25,.026) 1px, transparent 1px, transparent 8px),
        var(--paper);
      color:var(--ink);
      font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",serif;
    }}
    main {{
      width:min(1150px, calc(100vw - 40px));
      margin:0 auto;
      padding:44px 0 78px;
    }}
    .masthead {{
      display:grid;
      grid-template-columns:minmax(0, 1.06fr) minmax(280px, .94fr);
      gap:42px;
      padding-bottom:26px;
      border-bottom:2px solid var(--line);
    }}
    .eyebrow {{
      color:var(--accent);
      font-size:13px;
      font-weight:800;
      letter-spacing:0;
      text-transform:uppercase;
    }}
    h1 {{
      max-width:620px;
      margin:10px 0 16px;
      font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
      font-size:74px;
      font-weight:700;
      line-height:.93;
      letter-spacing:0;
    }}
    .lede {{
      max-width:680px;
      color:var(--ink-soft);
      line-height:1.8;
      font-size:19px;
    }}
    .issue-stats {{
      align-self:end;
      display:grid;
      grid-template-columns:repeat(3, minmax(0, 1fr));
      gap:10px;
    }}
    .stat {{
      min-height:104px;
      border:1px solid var(--line);
      background:rgba(255,255,255,.22);
      padding:18px 15px;
    }}
    .stat strong {{
      display:block;
      font-size:34px;
      line-height:1;
      font-weight:800;
    }}
    .stat span {{
      display:block;
      margin-top:14px;
      color:var(--muted);
      font-size:12px;
      letter-spacing:0;
      text-transform:uppercase;
    }}
    .issue-meta {{
      margin-top:12px;
      color:var(--muted);
      font-size:13px;
      line-height:1.7;
    }}
    .issue-links {{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin-top:16px;
    }}
    .issue-link {{
      color:var(--ink);
      text-decoration:none;
      border:1px solid var(--line-soft);
      background:rgba(255,255,255,.24);
      padding:8px 10px;
      font-size:13px;
    }}
    .issue-link.primary {{
      background:var(--ink);
      border-color:var(--ink);
      color:var(--paper-strong);
    }}
    .layout {{
      display:grid;
      grid-template-columns:minmax(0, 1fr) 360px;
      gap:28px;
      align-items:start;
      margin-top:26px;
    }}
    .filters {{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin:0 0 16px;
      padding-bottom:14px;
      border-bottom:1px solid var(--rule);
    }}
    button.filter {{
      border:1px solid var(--line-soft);
      background:rgba(255,255,255,.28);
      color:var(--ink);
      padding:8px 11px;
      cursor:pointer;
      font-size:13px;
    }}
    button.filter.active {{
      background:var(--ink);
      border-color:var(--ink);
      color:var(--paper-strong);
    }}
    .grid {{ display:grid; gap:0; }}
    .card {{
      position:relative;
      display:grid;
      grid-template-columns:48px minmax(0, 1fr) 70px;
      gap:12px;
      border-top:1px solid var(--rule);
      padding:15px 0;
      transition:background .16s ease;
    }}
    .card:hover {{
      background:rgba(255,255,255,.22);
    }}
    .rank {{
      color:var(--accent);
      font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
      font-size:26px;
      line-height:1.05;
    }}
    .paper-main {{ min-width:0; }}
    .venue {{
      color:var(--muted);
      font-size:13px;
      margin:0 0 7px;
    }}
    h2 {{
      margin:0;
      font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
      font-size:23px;
      line-height:1.25;
      letter-spacing:0;
      overflow-wrap:anywhere;
    }}
    .score {{
      justify-self:end;
      align-self:start;
      border:1px solid var(--accent);
      color:var(--accent);
      min-width:44px;
      padding:5px 7px;
      text-align:center;
      font-family:"SF Mono","Menlo",monospace;
      font-size:12px;
    }}
    .tags {{
      display:flex;
      flex-wrap:wrap;
      gap:7px;
      margin:12px 0;
    }}
    .tag {{
      font-size:12px;
      color:var(--accent-dark);
      background:rgba(169,53,34,.08);
      border:1px solid rgba(169,53,34,.2);
      padding:5px 8px;
    }}
    .tag.source {{
      color:var(--signal);
      background:rgba(45,109,101,.09);
      border-color:rgba(45,109,101,.22);
    }}
    .insight {{
      max-width:780px;
      color:var(--ink-soft);
      line-height:1.76;
      font-size:16px;
    }}
    .actions {{
      display:flex;
      gap:8px;
      margin-top:13px;
      flex-wrap:wrap;
    }}
    .link, .toggle {{
      color:var(--ink);
      text-decoration:none;
      border:1px solid var(--line-soft);
      background:rgba(255,255,255,.24);
      padding:8px 10px;
      cursor:pointer;
      font-size:13px;
    }}
    .toggle {{
      background:var(--ink);
      border-color:var(--ink);
      color:var(--paper-strong);
    }}
    .details {{
      display:none;
      grid-column:2 / 4;
      margin-top:16px;
      padding:18px 0 2px;
      border-top:1px solid var(--line-soft);
      color:var(--muted);
    }}
    .details.open {{ display:block; }}
    .detail-layout {{
      display:grid;
      grid-template-columns:minmax(260px, .78fr) minmax(0, 1fr);
      gap:20px;
      align-items:start;
    }}
    .figure-panel {{
      position:sticky;
      top:16px;
    }}
    .analysis-panel {{
      display:grid;
      gap:8px;
    }}
    .detail-block {{
      padding:12px 0;
      border-top:1px solid rgba(42,33,25,.18);
      color:var(--ink-soft);
      line-height:1.74;
      font-size:15px;
    }}
    .detail-block:first-child {{ border-top:0; padding-top:0; }}
    .detail-block strong {{
      color:var(--ink);
      font-weight:800;
    }}
    .key-figure {{
      margin:0;
      padding:12px;
      background:#fff;
      border:1px solid var(--line);
    }}
    .figure-label {{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:12px;
      color:var(--accent);
      font-size:12px;
      letter-spacing:0;
      text-transform:uppercase;
      margin-bottom:10px;
      font-weight:800;
    }}
    .figure-label span:last-child {{
      color:var(--muted);
      letter-spacing:0;
      text-transform:none;
      font-weight:400;
    }}
    .key-figure img {{
      display:block;
      width:100%;
      max-height:520px;
      object-fit:contain;
      background:#ffffff;
      border:1px solid rgba(42,33,25,.16);
    }}
    .key-figure figcaption {{
      margin-top:8px;
      color:var(--muted);
      font-size:13px;
      line-height:1.6;
      padding:0 2px;
    }}
    .no-figure {{
      min-height:180px;
      display:grid;
      place-items:center;
      text-align:center;
      color:var(--muted);
      border:1px dashed var(--line-soft);
      padding:22px;
      background:rgba(255,255,255,.18);
    }}
    .editorial-panel {{
      position:sticky;
      top:18px;
      min-height:520px;
      background:linear-gradient(180deg, var(--accent-dark), #17130f);
      color:var(--paper-strong);
      padding:26px;
      overflow:hidden;
    }}
    .editorial-panel::before {{
      content:"";
      position:absolute;
      inset:0;
      background:linear-gradient(145deg, rgba(169,53,34,.5), transparent 38%);
      pointer-events:none;
    }}
    .editorial-panel > * {{ position:relative; }}
    .panel-label {{
      color:#d2c5b4;
      font-size:12px;
      letter-spacing:0;
      text-transform:uppercase;
    }}
    .panel-title {{
      margin:10px 0 18px;
      font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
      font-size:38px;
      line-height:1;
    }}
    .panel-tags {{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin-bottom:34px;
    }}
    .panel-tag {{
      border:1px solid rgba(244,241,232,.38);
      color:var(--paper-strong);
      padding:7px 9px;
      font-size:13px;
    }}
    .panel-quote {{
      margin:0;
      max-width:300px;
      font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
      font-size:20px;
      line-height:1.7;
    }}
    .panel-note {{
      position:relative;
      margin-top:22px;
      color:#d2c5b4;
      font-size:13px;
      line-height:1.7;
    }}
    @media (max-width:700px) {{
      main {{ width:auto; margin:0 24px 0 42px; padding-top:28px; }}
      .masthead {{ grid-template-columns:1fr; gap:20px; }}
      h1 {{ font-size:40px; line-height:1; }}
      .lede {{ font-size:16px; }}
      .issue-stats {{ grid-template-columns:repeat(3, 1fr); }}
      .stat {{ min-height:82px; padding:12px 10px; }}
      .layout {{ grid-template-columns:1fr; }}
      .editorial-panel {{
        display:block;
        position:relative;
        top:auto;
        min-height:0;
        margin-top:24px;
        padding:22px;
      }}
      .panel-quote {{ max-width:none; font-size:18px; }}
      .panel-note {{ position:relative; }}
      .card {{ grid-template-columns:40px minmax(0, 1fr); padding-right:64px; }}
      .card h2 {{ font-size:20px; }}
      .score {{ position:absolute; right:0; top:16px; }}
      .details {{ grid-column:1 / 3; }}
      .detail-layout {{ grid-template-columns:1fr; }}
      .figure-panel {{ position:static; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="masthead">
      <div>
      <div class="eyebrow">Paper Radar · Weekly Digest</div>
      <h1>{issue['title_html']}</h1>
      <div class="lede">
        {html.escape(issue['lede'])}
      </div>
      <div class="issue-meta">Issue {issue_date.isoformat()} · Special platforms, embodied perception, tactile intelligence</div>
      <nav class="issue-links" aria-label="Paper Radar navigation">
        <a class="issue-link primary" id="archive-link" href="../issues/index.html">历史推荐</a>
      </nav>
      </div>
      <div class="issue-stats">
        <div class="stat"><strong>{len(items)}</strong><span>papers</span></div>
        <div class="stat"><strong>{issue['tier1_count']}</strong><span>tier 1</span></div>
        <div class="stat"><strong>{issue['tier2_count']}</strong><span>tier 2</span></div>
      </div>
    </section>

    <section class="layout">
      <div>
        <section class="filters" id="filters"></section>
        <section class="grid" id="paper-grid"></section>
      </div>
      <aside class="editorial-panel">
        <div class="panel-label">Paper Radar · Journal Edition</div>
        <h2 class="panel-title">本期研究<br>线索</h2>
        <div class="panel-tags" id="panel-tags"></div>
        <p class="panel-quote">“{html.escape(issue['panel_quote'])}”</p>
        <div class="panel-note">{html.escape(issue['panel_note'])}</div>
      </aside>
    </section>
  </main>

  <script>
    const papers = {data_json};
    const tagLabels = {{
      all: "全部",
      soft_robot: "软体",
      flapping_wing: "扑翼",
      micro_robot: "微型",
      bioinspired: "仿生",
      continuum: "连续体",
      manipulation: "操作",
      aquatic_robot: "水下",
      swarm_robot: "群体",
      locomotion: "运动",
      hard_to_instrument: "难布设",
      other: "其他"
    }};
    const sourceLabels = {{
      official: "正式发表",
      preprint: "预印本"
    }};
    const tierLabels = {{
      tier1: "Tier 1 旗舰正式论文",
      tier2: "Tier 2 高质量正式论文",
      preprint: "arXiv / preprint",
      other: "其他"
    }};
    const filtersEl = document.getElementById("filters");
    const gridEl = document.getElementById("paper-grid");
    const panelTagsEl = document.getElementById("panel-tags");
    const archiveLink = document.getElementById("archive-link");
    const tags = ["all", ...new Set(papers.flatMap(p => p.robot_type_tags))];
    let active = "all";

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, char => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function renderFilters() {{
      filtersEl.innerHTML = tags.map(tag => `
        <button class="filter ${{tag === active ? "active" : ""}}" data-tag="${{tag}}">
          ${{escapeHtml(tagLabels[tag] || tag)}}
        </button>
      `).join("");
      filtersEl.querySelectorAll("button").forEach(btn => {{
        btn.addEventListener("click", () => {{
          active = btn.dataset.tag;
          renderFilters();
          renderCards();
        }});
      }});
    }}

    function configureNavigation() {{
      const isPermanentIssue = /\\/issues\\/\\d{{4}}-\\d{{2}}-\\d{{2}}\\/(?:index\\.html)?$/.test(window.location.pathname);
      archiveLink.href = isPermanentIssue ? "../index.html" : "../issues/index.html";
    }}

    function renderPanelTags() {{
      panelTagsEl.innerHTML = tags
        .filter(tag => tag !== "all")
        .slice(0, 8)
        .map(tag => `<span class="panel-tag">${{escapeHtml(tagLabels[tag] || tag)}}</span>`)
        .join("");
    }}

    function renderCards() {{
      const visible = papers
        .map((paper, originalIdx) => ({{ ...paper, originalIdx }}))
        .filter(p => active === "all" || p.robot_type_tags.includes(active));
      gridEl.innerHTML = visible.map((paper) => `
        <article class="card">
          <div class="rank">${{String(paper.originalIdx + 1).padStart(2, "0")}}</div>
          <div class="paper-main">
            <div class="venue">${{escapeHtml(tierLabels[paper.tier] || paper.tier_label || paper.tier)}} · ${{escapeHtml(paper.venue)}} · ${{escapeHtml(paper.published_at)}} · ${{paper.paper_type === "direct" ? "直接命中" : "可迁移"}}</div>
            <h2>${{escapeHtml(paper.title)}}</h2>
            <div class="tags">
              <span class="tag source">${{escapeHtml(sourceLabels[paper.source_kind] || paper.source_kind)}}</span>
              ${{paper.robot_type_tags.map(tag => `<span class="tag">${{escapeHtml(tagLabels[tag] || tag)}}</span>`).join("")}}
            </div>
            <div class="insight">${{escapeHtml(paper.quick_take || paper.analysis.core_insight)}}</div>
            <div class="actions">
              <button class="toggle">展开深读</button>
              ${{paper.url ? `<a class="link" href="${{escapeHtml(paper.url)}}" target="_blank" rel="noreferrer">原文</a>` : ""}}
              ${{paper.pdf_url ? `<a class="link" href="${{escapeHtml(paper.pdf_url)}}" target="_blank" rel="noreferrer">PDF</a>` : ""}}
            </div>
          </div>
          <div class="score">${{Math.round(paper.score * 100)}}</div>
          <div class="details">
            <div class="detail-layout">
              <aside class="figure-panel">
                ${{paper.key_figure_path ? `
                  <figure class="key-figure">
                    <div class="figure-label"><span>Key Figure</span><span>用于快速抓核心机制</span></div>
                    <img src="${{escapeHtml(paper.key_figure_path)}}" alt="key figure">
                    <figcaption>${{escapeHtml(paper.key_figure_caption || "关键图")}}</figcaption>
                  </figure>
                ` : `
                  <div class="no-figure">暂无可抽取关键图<br>通常是正式出版页面未开放 PDF 或文本层不可检索。</div>
                `}}
              </aside>
              <div class="analysis-panel">
                <div class="detail-block"><strong>问题重述：</strong>${{escapeHtml(paper.analysis.problem_frame)}}</div>
                <div class="detail-block"><strong>第一性原理：</strong>${{escapeHtml(paper.analysis.first_principles)}}</div>
                <div class="detail-block"><strong>生效机制：</strong>${{escapeHtml(paper.analysis.mechanism)}}</div>
                <div class="detail-block"><strong>推进的边界：</strong>${{escapeHtml(paper.analysis.boundary_advanced)}}</div>
                <div class="detail-block"><strong>解决的老问题：</strong>${{escapeHtml(paper.analysis.old_problem)}}</div>
                <div class="detail-block"><strong>为什么成立：</strong>${{escapeHtml(paper.analysis.why_it_works)}}</div>
                <div class="detail-block"><strong>真正新意：</strong>${{escapeHtml(paper.analysis.true_novelty)}}</div>
                <div class="detail-block"><strong>证据：</strong>${{escapeHtml(paper.analysis.evidence_summary)}}</div>
              </div>
            </div>
          </div>
        </article>
      `).join("");
      gridEl.querySelectorAll(".toggle").forEach(button => {{
        button.addEventListener("click", () => {{
          const details = button.closest(".card").querySelector(".details");
          details.classList.toggle("open");
          button.textContent = details.classList.contains("open") ? "收起" : "展开深读";
        }});
      }});
    }}

    configureNavigation();
    renderPanelTags();
    renderFilters();
    renderCards();
  </script>
</body>
</html>"""


def _relative_output_path(path: str) -> str:
    if not path:
        return ""
    normalized = str(path)
    prefix = "output/"
    if normalized.startswith(prefix):
        return normalized[len(prefix):]
    return normalized
