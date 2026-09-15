"""Regression test: 自评分/自动修改喂给 AI 的章节正文必须先归一化.

Bug context: ``BidEditor.tsx`` 保存 ``editor.getHTML()``，于是
``project_chapters.final_content`` 里是 ``<p>…<br>…</p>``。导出侧已在
``render_engine`` 归一化（见 test_render_html_content.py），但 ``scoring.py``
的两处读法漏了：

- ``rescore_project`` 把 final_content 直接丢给 ``run_scoring`` 判卷
- ``auto_fix_scoring_item`` 把它当 ``chapter_content`` 交给改写

后果是编辑过的章节带着标签被判卷；改写路径更糟——标签被当正文复述回去，再写回
``final_content``，等于把污染又固化一层。
"""

import json

from app.api.scoring import _build_scoring_inputs, _chapter_text_for_ai

HTML_CHAPTER = (
    "<p>二、服务方案<br>响应时间：接到通知后2小时内到场<br>"
    "协同支撑：提供7×24小时值守</p>"
)


class FakeChapter:
    """Stand-in for ProjectChapter — 这几个字段就是 scoring 读的全部."""

    def __init__(self, title, content, order_index=0, ai_generated_content="", children_json="[]"):
        self.title = title
        self.final_content = content
        self.ai_generated_content = ai_generated_content
        self.order_index = order_index
        self.children_json = children_json


# ── 判卷输入 ──────────────────────────────────────────────────────────────

def test_html_final_content_is_normalized_for_scoring():
    """编辑过的章节进判卷前必须去掉标签，否则判卷看到的是标记不是正文."""
    inputs = _build_scoring_inputs([FakeChapter("服务方案", HTML_CHAPTER)])

    content = inputs[0]["content"]
    for tag in ("<p>", "</p>", "<br>"):
        assert tag not in content, f"literal {tag} reached the scoring input"


def test_html_br_becomes_newline_for_scoring():
    """<br> 要变回换行——一整章塌成一行会让判卷分不清小节."""
    inputs = _build_scoring_inputs([FakeChapter("服务方案", HTML_CHAPTER)])

    content = inputs[0]["content"]
    assert "二、服务方案\n响应时间：接到通知后2小时内到场" in content
    assert "协同支撑：提供7×24小时值守" in content


def test_plain_text_content_reaches_scoring_unchanged():
    """纯文本章节（TreeEditor / 管线生成）不能被顺手改动."""
    plain = "二、服务方案\n\n响应时间：2小时\n协同支撑：7×24小时"
    inputs = _build_scoring_inputs([FakeChapter("服务方案", plain)])

    assert plain in inputs[0]["content"]


def test_final_content_wins_over_ai_generated():
    """final_content 优先于 ai_generated_content 的既有语义不变."""
    ch = FakeChapter("服务方案", "编辑后的正文", ai_generated_content="AI 原始正文")
    inputs = _build_scoring_inputs([ch])

    assert "编辑后的正文" in inputs[0]["content"]
    assert "AI 原始正文" not in inputs[0]["content"]


def test_ai_generated_content_normalized_when_final_empty():
    """没编辑过的章节走 ai_generated_content，同样要吃归一化."""
    ch = FakeChapter("服务方案", "", ai_generated_content="<p>AI 正文<br>第二行</p>")
    inputs = _build_scoring_inputs([ch])

    assert "<p>" not in inputs[0]["content"]
    assert "AI 正文\n第二行" in inputs[0]["content"]


def test_children_titles_still_appended_after_children_json():
    """小节标题追加回正文的既有行为不能丢."""
    ch = FakeChapter(
        "服务方案", "<p>正文</p>",
        children_json=json.dumps(
            [{"title": "响应时间", "children": [{"title": "到场时限"}]}], ensure_ascii=False
        ),
    )
    inputs = _build_scoring_inputs([ch])

    content = inputs[0]["content"]
    assert "小节：" in content
    assert "响应时间" in content
    assert "到场时限" in content
    assert "<p>" not in content


def test_chapters_are_sorted_by_order_index():
    """判卷输入按 order_index 排序（与导出同一份顺序）."""
    inputs = _build_scoring_inputs([
        FakeChapter("第三章", "丙", order_index=2),
        FakeChapter("第一章", "甲", order_index=0),
        FakeChapter("第二章", "乙", order_index=1),
    ])

    assert [i["title"] for i in inputs] == ["第一章", "第二章", "第三章"]


# ── 自动修改输入 ──────────────────────────────────────────────────────────

def test_autofix_chapter_text_is_normalized():
    """自动修改拿到的 chapter_content 同样不能带标签."""
    text = _chapter_text_for_ai(FakeChapter("服务方案", HTML_CHAPTER))

    assert "<p>" not in text and "<br>" not in text
    assert "二、服务方案\n响应时间" in text


def test_autofix_chapter_text_prefers_final_content():
    """自动修改的取值优先级与判卷一致."""
    ch = FakeChapter("服务方案", "编辑后", ai_generated_content="AI 原始")
    assert _chapter_text_for_ai(ch) == "编辑后"