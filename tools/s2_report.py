"""Print the full S2 accuracy breakdown, whether or not the gate passes.

``test_s2_accuracy.py`` only reveals ``report.describe()`` when the gate
*assertion fails*, so a passing run tells you the score but not which
cases were missed or why. This reuses the harness's own ``_score`` and
``AccuracyReport`` so the numbers are identical, and always prints.

Usage (from the repository root):
    env LLM_MAX_TOKENS=16000 OPENAI_COMPATIBLE_MODEL=agnes-3.0-flash \
        sidecar/.venv/Scripts/python.exe -u tools/s2_report.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))
sys.path.insert(0, str(ROOT / "tests"))

from app.config import settings  # noqa: E402
from test_s2_accuracy import AccuracyReport, _score  # noqa: E402
from app.eval_cases import load_eval_cases  # noqa: E402

print("provider :", settings.llm_provider)
print("model    :", settings.openai_compatible_model)
print("max_tok  :", settings.llm_max_tokens)
print("timeout  :", settings.llm_timeout)
print("=" * 72)

# Scored one at a time with a line printed as each finishes. The whole
# set is 20 remote calls (tens of minutes on a slow endpoint), and a
# version that printed nothing until the end was indistinguishable from
# a hung process.
cases = load_eval_cases()
outcomes = []
for index, case in enumerate(cases, start=1):
    outcome = _score(case)
    outcomes.append(outcome)
    if outcome.error is not None:
        note = f"ERR  {outcome.error[:80]}"
    elif outcome.correct:
        note = f"ok   {outcome.predicted}"
    else:
        note = f"MISS {outcome.predicted} (expected {outcome.expected})"
    print(f"[{index:>2}/{len(cases)}] {case.case_id:<40} {note}", flush=True)

report = AccuracyReport(outcomes=outcomes)
print()
print("=" * 72)
print(report.describe())
