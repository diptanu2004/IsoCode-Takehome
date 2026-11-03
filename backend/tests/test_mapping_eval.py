"""Suggestion quality on report layouts the rules weren't written for (scripts/mapping_fixtures.py).

The rules must never be confidently wrong: a wrong pick must at least be flagged for review. The AI
check runs only when ANTHROPIC_API_KEY is set, since it calls the real model.
"""

import os

import pytest

from scripts.eval_mapping import Score, score
from scripts.mapping_fixtures import CASES


def _total(use_ai: bool) -> Score:
    total = Score()
    for case in CASES:
        s, _ = score(case, use_ai)
        total.right += s.right
        total.wrong += s.wrong
        total.wrong_confident += s.wrong_confident
        total.missed += s.missed
    return total


def test_rules_are_useful_and_never_confidently_wrong():
    total = _total(use_ai=False)
    assert total.wrong_confident == 0
    assert total.right / total.expected >= 0.75


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_ai_closes_most_of_the_gap():
    total = _total(use_ai=True)
    assert total.wrong_confident <= 1
    assert total.right / total.expected >= 0.9
