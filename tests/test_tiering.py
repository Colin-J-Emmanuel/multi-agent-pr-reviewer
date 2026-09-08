from app.graph import Finding, tier_findings


def finding(confidence: float, file: str = "a.py", line: int | None = 1,
            severity: str = "medium") -> Finding:
    return Finding(file=file, line=line, category="security",
                   severity=severity, confidence=confidence, message="x")


def test_noise_floor_findings_are_dropped():
    """A finding the agent is barely confident in must not reach a human."""
    high, low, dropped = tier_findings([finding(0.1)])
    assert (high, low, dropped) == ([], [], 1)


def test_demoted_findings_are_kept_not_discarded():
    """Low confidence means uncertain, not unimportant — it stays in the record."""
    high, low, dropped = tier_findings([finding(0.4)])
    assert len(low) == 1 and dropped == 0

def test_exactly_at_noise_floor_is_kept():
    """0.3 is the floor, not below it — a finding exactly at the boundary is demoted, not dropped."""
    _, low, dropped = tier_findings([finding(0.3)])
    assert len(low) == 1 and dropped == 0

def test_exactly_at_high_threshold_is_high_tier():
    """0.7 counts as high — the comparison is >=, not >."""
    high, low, _ = tier_findings([finding(0.7)])
    assert len(high) == 1 and len(low) == 0

def test_duplicate_findings_collapse():
    """Two agents flagging the same file+line+category is one issue, not two."""
    high, _, _ = tier_findings([finding(0.9), finding(0.9)])
    assert len(high) == 1

def test_severe_findings_are_ordered_first():
    """The most serious issue leads, so a skimming reader sees it."""
    high, _, _ = tier_findings([
        finding(0.9, file="a.py", severity="low"),
        finding(0.8, file="b.py", severity="high"),
    ])
    assert high[0].severity == "high"


def test_clean_pr_produces_nothing():
    assert tier_findings([]) == ([], [], 0)

def test_dedupe_keeps_one_per_location():
    """Same file+line+category from two agents is one issue — the first wins."""
    high, _, _ = tier_findings([
        finding(0.9, severity="low"),
        finding(0.8, severity="high"),   # same file/line/category
    ])
    assert len(high) == 1 and high[0].severity == "low"
