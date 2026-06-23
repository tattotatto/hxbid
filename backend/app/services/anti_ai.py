"""宏曦标书 - Anti-AI Trace Detection Engine.

Post-processes generated chapter content to detect and quantify AI-writing
artifacts: empty adjectives, missing concrete anchors, repetitive sentence
openings, and keyword-copying from requirements.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Empty / fluffy phrases commonly produced by AI in Chinese bid writing.
# Each entry is (phrase, category, severity_weight).
EMPTY_PHRASES: List[Tuple[str, str, float]] = [
    # --- Generic fluff ---
    ("经验丰富", "空泛形容词", 0.8),
    ("技术精湛", "空泛形容词", 0.8),
    ("服务周到", "空泛形容词", 0.7),
    ("精益求精", "空泛形容词", 0.7),
    ("优质服务", "空泛形容词", 0.6),
    ("高效务实", "空泛形容词", 0.7),
    ("客户至上", "空泛形容词", 0.6),
    ("信誉第一", "空泛形容词", 0.6),
    ("实力雄厚", "空泛形容词", 0.7),
    ("一流水平", "空泛形容词", 0.7),
    ("专业团队", "空泛形容词", 0.5),
    ("管理能力强", "空泛形容词", 0.8),
    ("服务意识强", "空泛形容词", 0.7),
    ("责任心强", "空泛形容词", 0.7),
    ("素质过硬", "空泛形容词", 0.7),
    ("业务精湛", "空泛形容词", 0.8),
    ("作风优良", "空泛形容词", 0.7),
    ("保障有力", "空泛形容词", 0.7),
    # --- Template connectors ---
    ("首先", "模板化连接词", 0.4),
    ("其次", "模板化连接词", 0.4),
    ("再次", "模板化连接词", 0.4),
    ("此外", "模板化连接词", 0.4),
    ("总而言之", "模板化连接词", 0.7),
    ("综上所述", "模板化连接词", 0.7),
    ("值得注意的是", "模板化连接词", 0.5),
    ("众所周知", "模板化连接词", 0.6),
    # --- Common AI boilerplate ---
    ("不断优化", "AI惯用套话", 0.5),
    ("持续改进", "AI惯用套话", 0.5),
    ("全面提升", "AI惯用套话", 0.5),
    ("有效保障", "AI惯用套话", 0.5),
    ("充分发挥", "AI惯用套话", 0.4),
    ("着力打造", "AI惯用套话", 0.5),
    ("切实加强", "AI惯用套话", 0.5),
    ("稳步推进", "AI惯用套话", 0.5),
    ("扎实推进", "AI惯用套话", 0.5),
    ("积极推进", "AI惯用套话", 0.5),
    ("深入贯彻", "AI惯用套话", 0.5),
]

# Regex patterns for concrete anchors.
ANCHOR_PATTERNS: List[Tuple[str, str, float]] = [
    (r"\d+年", "年份", 0.5),
    (r"\d{4}年\d{1,2}月", "具体日期", 0.8),
    (r"\d+人", "人数", 0.6),
    (r"\d+平方米|\d+㎡", "面积", 0.7),
    (r"\d+万|\d+亿", "金额/规模", 0.7),
    (r"\d+次|\d+起", "频次", 0.5),
    (r"\d+个", "数量", 0.4),
    (r"\d+%|\d+％|百分之\d+", "百分比", 0.6),
    (r"[A-Z]{2,}\d{4,}", "证书编号", 0.9),
    (r"（\d{4}）\d+号", "公文文号", 0.8),
    (r"[（\(][A-Za-z\d]{4,}[）\)]", "标准/规范编号", 0.7),
    (r"\d{17}[\dXx]|\d{15}", "身份证号", 0.9),
    (r"1[3-9]\d{9}", "手机号", 0.9),
    (r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁][A-Z]\d{4,}", "车牌号", 0.7),
]

# Paragraph-opening patterns that indicate repetitive structure.
REPETITIVE_OPENINGS = [
    r"^(我们|我方|本公司|我司)",
    r"^(在|针对|根据|按照|依据)",
    r"^(通过|采用|利用|借助)",
    r"^(同时|另外|此外|并且|而且)",
    r"^(确保|保证|保障|做到)",
    r"^(坚持|贯彻|执行|落实)",
    r"^(建立|健全|完善|制定)",
]

# Score thresholds
MAX_EMPTY_PHRASE_RATIO = 0.08      # >8% of sentences with empty phrases → warning
MAX_REPETITIVE_OPENINGS = 3         # >3 consecutive same-pattern → warning
MIN_ANCHOR_PER_PARAGRAPH = 1        # each paragraph should have at least 1 anchor
OVERALL_WARNING_THRESHOLD = 60      # score >= 60 → needs revision
OVERALL_CRITICAL_THRESHOLD = 80     # score >= 80 → strongly recommend regeneration


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EmptyPhraseFinding:
    phrase: str
    category: str
    count: int
    severity: float


@dataclass
class AnchorGap:
    paragraph_index: int
    paragraph_preview: str


@dataclass
class RepetitiveOpening:
    pattern: str
    paragraph_indices: List[int]


@dataclass
class AntiAITraceReport:
    """Complete anti-AI trace analysis for a chapter."""
    chapter_id: str = ""
    chapter_title: str = ""

    # Input stats
    total_chars: int = 0
    total_paragraphs: int = 0
    total_sentences: int = 0

    # Empty phrase findings
    empty_phrases: List[EmptyPhraseFinding] = field(default_factory=list)
    empty_phrase_count: int = 0
    empty_phrase_sentences: int = 0           # sentences containing ≥1 empty phrase

    # Anchor findings
    anchor_count: int = 0
    anchor_gaps: List[AnchorGap] = field(default_factory=list)

    # Repetitive opening findings
    repetitive_openings: List[RepetitiveOpening] = field(default_factory=list)
    max_consecutive_repetition: int = 0

    # Scores (0-100; lower = better / less AI-like)
    empty_phrase_score: float = 0.0
    anchor_score: float = 0.0
    repetition_score: float = 0.0
    overall_score: float = 0.0

    # The thresholds that were applied
    thresholds: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> List[str]:
    """Split text into non-empty paragraphs."""
    if not text:
        return []
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences (Chinese-aware)."""
    if not text:
        return []
    # Split on Chinese/English sentence-ending punctuation
    raw = re.split(r"[。！？；\n!?;]+", text)
    return [s.strip() for s in raw if len(s.strip()) >= 4]


def _detect_empty_phrases(text: str) -> Tuple[List[EmptyPhraseFinding], int, int]:
    """Find empty-phrase occurrences and count affected sentences."""
    findings: Dict[str, EmptyPhraseFinding] = {}
    sentences = _split_sentences(text)
    affected_sentences: set = set()

    for phrase, category, severity in EMPTY_PHRASES:
        count = text.count(phrase)
        if count > 0:
            findings[phrase] = EmptyPhraseFinding(
                phrase=phrase,
                category=category,
                count=count,
                severity=severity,
            )
            # Mark which sentences contain this phrase
            for i, sent in enumerate(sentences):
                if phrase in sent:
                    affected_sentences.add(i)

    return (
        sorted(findings.values(), key=lambda f: f.severity * f.count, reverse=True),
        len(affected_sentences),
    )


def _detect_anchors(text: str) -> Tuple[int, List[AnchorGap]]:
    """Count concrete anchors and find paragraphs lacking them."""
    paragraphs = _split_paragraphs(text)
    total_anchors = 0
    gaps: List[AnchorGap] = []

    for i, para in enumerate(paragraphs):
        para_anchors = 0
        for pattern, _name, _weight in ANCHOR_PATTERNS:
            matches = re.findall(pattern, para)
            para_anchors += len(matches)
        total_anchors += para_anchors

        if para_anchors == 0:
            # Only flag paragraphs longer than 50 chars (short sections like
            # "二、项目人员配置方案" are structural, not content)
            if len(para) >= 50:
                preview = para[:60] + "…" if len(para) > 60 else para
                gaps.append(AnchorGap(paragraph_index=i, paragraph_preview=preview))

    return total_anchors, gaps


def _detect_repetitive_openings(text: str) -> Tuple[List[RepetitiveOpening], int]:
    """Detect consecutive paragraphs with the same opening pattern."""
    paragraphs = _split_paragraphs(text)
    if len(paragraphs) < 2:
        return [], 0

    findings: List[RepetitiveOpening] = []
    current_pattern: Optional[str] = None
    current_indices: List[int] = []
    max_consecutive = 0

    for i, para in enumerate(paragraphs):
        matched_pattern = None
        for pattern in REPETITIVE_OPENINGS:
            if re.match(pattern, para):
                matched_pattern = pattern
                break

        if matched_pattern == current_pattern and current_pattern is not None:
            current_indices.append(i)
        else:
            # Finalize previous run
            if len(current_indices) >= 2:
                findings.append(RepetitiveOpening(
                    pattern=current_pattern,
                    paragraph_indices=list(current_indices),
                ))
                max_consecutive = max(max_consecutive, len(current_indices))

            # Start new run
            current_pattern = matched_pattern
            current_indices = [i] if matched_pattern else []

    # Final run
    if len(current_indices) >= 2:
        findings.append(RepetitiveOpening(
            pattern=current_pattern,
            paragraph_indices=list(current_indices),
        ))
        max_consecutive = max(max_consecutive, len(current_indices))

    return findings, max_consecutive


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_empty_phrases(
    affected_sentences: int,
    total_sentences: int,
    findings: List[EmptyPhraseFinding],
) -> float:
    """Score 0-100 for empty phrase usage (higher = worse)."""
    if total_sentences == 0:
        return 0.0

    ratio = affected_sentences / total_sentences

    # Base score from ratio
    if ratio <= 0.03:
        ratio_score = ratio / 0.03 * 20  # 0-20
    elif ratio <= MAX_EMPTY_PHRASE_RATIO:
        ratio_score = 20 + (ratio - 0.03) / (MAX_EMPTY_PHRASE_RATIO - 0.03) * 30  # 20-50
    else:
        ratio_score = 50 + min((ratio - MAX_EMPTY_PHRASE_RATIO) / 0.10 * 50, 50)  # 50-100

    # Severity bonus: high-severity phrases found many times
    severity_bonus = sum(f.severity * f.count for f in findings) / max(total_sentences, 1) * 15

    return min(100, ratio_score + severity_bonus)


def _score_anchors(
    anchor_count: int,
    total_paragraphs: int,
    gaps: List[AnchorGap],
) -> float:
    """Score 0-100 for anchor sparsity (higher = worse / fewer anchors)."""
    if total_paragraphs == 0:
        return 0.0

    anchors_per_para = anchor_count / total_paragraphs
    gap_ratio = len(gaps) / total_paragraphs

    # Ideal: ≥2 anchors per paragraph, no gaps
    if anchors_per_para >= 2.0 and gap_ratio == 0:
        return 0.0
    elif anchors_per_para >= 1.5 and gap_ratio <= 0.1:
        return 15.0
    elif anchors_per_para >= 1.0 and gap_ratio <= 0.2:
        return 30.0
    elif anchors_per_para >= 0.5 and gap_ratio <= 0.4:
        return 50.0
    elif anchors_per_para >= 0.25:
        return 70.0
    else:
        return 90.0


def _score_repetition(
    findings: List[RepetitiveOpening],
    max_consecutive: int,
    total_paragraphs: int,
) -> float:
    """Score 0-100 for repetitive openings (higher = worse)."""
    if total_paragraphs < 2:
        return 0.0

    if max_consecutive <= 2:
        consecutive_score = 0
    elif max_consecutive == 3:
        consecutive_score = 30
    elif max_consecutive <= 5:
        consecutive_score = 50
    else:
        consecutive_score = 80

    # Multiple patterns of repetition → worse
    pattern_bonus = len(findings) * 10

    return min(100, consecutive_score + pattern_bonus)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_chapter(
    chapter_id: str,
    chapter_title: str,
    content: str,
) -> AntiAITraceReport:
    """Run the full anti-AI trace analysis on a chapter.

    Args:
        chapter_id: Unique chapter identifier.
        chapter_title: Chapter title.
        content: The generated chapter content to analyze.

    Returns:
        AntiAITraceReport with all findings and scores.
    """
    report = AntiAITraceReport(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        thresholds={
            "max_empty_phrase_ratio": MAX_EMPTY_PHRASE_RATIO,
            "max_repetitive_openings": MAX_REPETITIVE_OPENINGS,
            "min_anchor_per_paragraph": MIN_ANCHOR_PER_PARAGRAPH,
            "warning_threshold": OVERALL_WARNING_THRESHOLD,
            "critical_threshold": OVERALL_CRITICAL_THRESHOLD,
        },
    )

    if not content or not content.strip():
        return report

    paragraphs = _split_paragraphs(content)
    sentences = _split_sentences(content)

    report.total_chars = len(content)
    report.total_paragraphs = len(paragraphs)
    report.total_sentences = len(sentences)

    # 1. Empty phrase detection
    empty_findings, affected = _detect_empty_phrases(content)
    report.empty_phrases = empty_findings
    report.empty_phrase_count = sum(f.count for f in empty_findings)
    report.empty_phrase_sentences = affected
    report.empty_phrase_score = _score_empty_phrases(
        affected, len(sentences), empty_findings
    )

    # 2. Anchor detection
    anchor_count, gaps = _detect_anchors(content)
    report.anchor_count = anchor_count
    report.anchor_gaps = gaps
    report.anchor_score = _score_anchors(anchor_count, len(paragraphs), gaps)

    # 3. Repetitive opening detection
    rep_findings, max_cons = _detect_repetitive_openings(content)
    report.repetitive_openings = rep_findings
    report.max_consecutive_repetition = max_cons
    report.repetition_score = _score_repetition(rep_findings, max_cons, len(paragraphs))

    # 4. Overall score (weighted)
    report.overall_score = round(
        0.40 * report.empty_phrase_score
        + 0.40 * report.anchor_score
        + 0.20 * report.repetition_score,
        1,
    )

    return report


def analyze_project_chapters(
    chapters: List[Dict[str, str]],
) -> List[AntiAITraceReport]:
    """Run analysis on multiple chapters at once.

    Args:
        chapters: List of dicts with keys: id, title, content.

    Returns:
        List of AntiAITraceReport, one per chapter.
    """
    return [
        analyze_chapter(
            chapter_id=ch.get("id", ""),
            chapter_title=ch.get("title", ""),
            content=ch.get("content", ""),
        )
        for ch in chapters
    ]


def report_verdict(report: AntiAITraceReport) -> str:
    """Return a human-readable verdict for a report's overall score.

    Returns one of: 'clean', 'acceptable', 'warning', 'critical'.
    """
    if report.overall_score < 30:
        return "clean"
    elif report.overall_score < OVERALL_WARNING_THRESHOLD:
        return "acceptable"
    elif report.overall_score < OVERALL_CRITICAL_THRESHOLD:
        return "warning"
    else:
        return "critical"


def report_to_dict(report: AntiAITraceReport) -> dict:
    """Convert a report to a JSON-serializable dict for API responses."""
    return {
        "chapter_id": report.chapter_id,
        "chapter_title": report.chapter_title,
        "total_chars": report.total_chars,
        "total_paragraphs": report.total_paragraphs,
        "total_sentences": report.total_sentences,
        "empty_phrase_count": report.empty_phrase_count,
        "empty_phrase_sentences": report.empty_phrase_sentences,
        "empty_phrases": [
            {
                "phrase": f.phrase,
                "category": f.category,
                "count": f.count,
                "severity": f.severity,
            }
            for f in report.empty_phrases[:10]  # top 10
        ],
        "anchor_count": report.anchor_count,
        "anchor_gaps": [
            {
                "paragraph_index": g.paragraph_index,
                "preview": g.paragraph_preview,
            }
            for g in report.anchor_gaps
        ],
        "repetitive_openings": [
            {
                "pattern": r.pattern,
                "paragraph_indices": r.paragraph_indices,
                "count": len(r.paragraph_indices),
            }
            for r in report.repetitive_openings
        ],
        "max_consecutive_repetition": report.max_consecutive_repetition,
        "scores": {
            "empty_phrase": report.empty_phrase_score,
            "anchor": report.anchor_score,
            "repetition": report.repetition_score,
            "overall": report.overall_score,
        },
        "verdict": report_verdict(report),
        "thresholds": report.thresholds,
    }
