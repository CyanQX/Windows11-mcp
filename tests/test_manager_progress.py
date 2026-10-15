"""Work fractions have explicit bounds, remain monotonic and cannot fake success."""
import math
from installer.progress import TaskProgress, segment


def test_real_progress_ignores_backwards_retries_and_nonfinite_values():
    progress = TaskProgress()
    for fraction in (0.3, 0.1, math.nan, math.inf, -1):
        progress.advance(fraction, "work")
    assert progress.fraction == 0.3
    progress.advance(1, "last step returned")
    assert progress.fraction < 1
    progress.complete(False)
    assert progress.failed and progress.fraction < 1
    progress.advance(1, "late callback")
    assert progress.fraction < 1


def test_subtasks_map_into_fixed_work_budgets_then_complete():
    progress = TaskProgress()
    report = segment(progress.advance, 0.2, 0.9)
    report(0.5, "halfway through this subtask")
    assert progress.fraction == 0.55
    progress.complete()
    assert progress.fraction == 1 and not progress.failed
