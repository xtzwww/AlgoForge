"""Unit tests for the style-consistency reading (C2).

The signal is conservative by design: two features must drift well past
the student's own history before it fires, and it must say nothing with
fewer than four historical samples. These tests pin both the machinery
(feature extraction) and the gate (the threshold rules), because the
whole point of the module is that a single-feature drift is explainable
and therefore must not flag.

Function-style, per the project convention (see conftest.py).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.class_stats import list_flagged_submissions
from app.models.submission import Submission
from app.records_write import _apply_style
from app.style_trace import (
    HIGH_RATIO,
    LOW_RATIO,
    MIN_HISTORY_SAMPLES,
    StyleFeatures,
    compare_against_history,
    from_code,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(engine, expire_on_commit=False)()
    yield db
    db.close()
    engine.dispose()


def make_submission(
    session: Session, user_id: int, code: str, language: str = "cpp"
) -> Submission:
    row = Submission(
        problem_id=None, user_id=user_id, status="WA", code=code, language=language
    )
    session.add(row)
    session.commit()
    return row


def code_with_identifiers(identifiers: list[str]) -> str:
    """A body using exactly the given identifier names (no includes, no main)."""
    return "\n".join(f"int {name} = 0;" for name in identifiers)


def blanky_code(blanks: int, total: int) -> str:
    lines = [f"int x{i} = {i};" for i in range(total - blanks)]
    lines += [""] * blanks
    # 尾随换行使 splitlines() 保留最后一个空行，计数与直觉一致。
    return "\n".join(lines) + "\n"


def history(n: int = MIN_HISTORY_SAMPLES + 2) -> list[StyleFeatures]:
    return [StyleFeatures(4.0, 0.25, 0.02) for _ in range(n)]


def test_identifier_average_length() -> None:
    feats = from_code(code_with_identifiers(["a", "bb", "cccc"]), "cpp")
    assert feats.identifier_avg_len == pytest.approx((1 + 2 + 4) / 3)


def test_keywords_are_not_identifiers() -> None:
    feats = from_code("int x = 1;", "cpp")
    # "int" excluded -> only "x".
    assert feats.identifier_avg_len == pytest.approx(1.0)


def test_blank_ratio() -> None:
    feats = from_code(blanky_code(blanks=2, total=5), "cpp")
    assert feats.blank_ratio == pytest.approx(0.4)


def test_long_line_ratio() -> None:
    code = "x = 1;\n" + ("y = " + "1 + " * 40 + "1;\n")
    feats = from_code(code, "cpp")
    assert feats.long_line_ratio == pytest.approx(0.5)


def test_empty_code_yields_zero_features() -> None:
    feats = from_code("   \n\n", "cpp")
    assert feats.identifier_avg_len == 0.0
    assert feats.blank_ratio == 0.0
    assert feats.long_line_ratio == 0.0


def test_insufficient_history_says_nothing() -> None:
    current = StyleFeatures(9.0, 0.01, 0.0)
    signal = compare_against_history(current, [StyleFeatures(4.0, 0.2, 0.0)] * 3)
    assert signal.flagged is False
    assert "历史提交不足" in signal.reasons[0]


def test_consistent_style_is_not_flagged() -> None:
    current = StyleFeatures(4.1, 0.25, 0.021)
    signal = compare_against_history(current, history())
    assert signal.flagged is False
    assert any("一致" in r for r in signal.reasons)


def test_single_feature_drift_is_not_flagged() -> None:
    # 标识符长度翻倍，但空行与超长行都在带内 —— 可以被"这题需要长名字"
    # 解释，所以不标记。
    current = StyleFeatures(4.0 * HIGH_RATIO * 1.2, 0.25, 0.02)
    signal = compare_against_history(current, history())
    assert signal.flagged is False


def test_two_feature_drift_is_flagged_with_numbers() -> None:
    current = StyleFeatures(4.0 * HIGH_RATIO * 1.5, 0.25 / LOW_RATIO, 0.02)
    signal = compare_against_history(current, history())
    assert signal.flagged is True
    assert any("平均标识符长度" in r for r in signal.reasons)
    assert any("空行占比" in r for r in signal.reasons)
    joined = " ".join(signal.reasons)
    assert "平均标识符长度为 9.00" in joined
    assert "你最近 6 次提交平均 4.00" in joined
    assert "空行占比为 0.42" in joined


def test_reasons_quote_the_students_own_numbers() -> None:
    current = StyleFeatures(9.0, 0.02, 0.0)
    signal = compare_against_history(current, [StyleFeatures(3.0, 0.2, 0.0)] * 6)
    joined = " ".join(signal.reasons)
    assert "最近 6 次提交平均 3.00" in joined
    assert "为 9.00" in joined


def test_zero_baseline_feature_only_flags_in_company() -> None:
    # 历史从未有超长行（基线 0），本次出现——单独不算，要有别的维度。
    current = StyleFeatures(4.1, 0.25, 0.2)
    signal = compare_against_history(current, history())
    assert signal.flagged is False


def test_apply_style_flags_a_drifted_submission(session: Session) -> None:
    """写库链路：突变代码被标记，且队列能看到 style-only 的行。"""
    normal = "\n".join(f"int x{i} = {i};" for i in range(20))
    for _ in range(MIN_HISTORY_SAMPLES + 1):
        make_submission(session, user_id=7, code=normal)

    # 风格突变：长标识符、无空行、一条超长行。
    drifted = (
        "int maximum_subarray_sum_with_prefix_decomposition = 0;\n"
        + "int previous_iteration_result_cache = 0;\n"
        + "int running_total_accumulator = 0;\n"
        + "long long aggregated_" + "x" * 90 + " = 0;\n"
    )
    current = make_submission(session, user_id=7, code=drifted)
    _apply_style(session, current)
    session.commit()

    assert current.style_flagged is True
    assert current.style_evidence is not None
    assert current.style_evidence["flagged"] is True

    # process 未触发，队列仍应通过 style 找到它。
    flagged = list_flagged_submissions(session, limit=50)
    ids = [row["id"] for row in flagged]
    assert current.id in ids
    by_id = next(row for row in flagged if row["id"] == current.id)
    assert by_id["style"] is not None
    assert by_id["style"]["flagged"] is True


def test_apply_style_says_nothing_without_history(session: Session) -> None:
    """历史不足时证据为空、不标记——\"未对比\"不是\"清白\"。"""
    row = make_submission(session, user_id=9, code="int x = 1;\n")
    _apply_style(session, row)
    session.commit()

    assert row.style_flagged is False
    assert row.style_evidence is None
