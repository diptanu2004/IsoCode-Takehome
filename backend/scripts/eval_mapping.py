"""Score mapping suggestions on layouts the rules weren't written for.

    uv run python -m scripts.eval_mapping          # rules only
    uv run python -m scripts.eval_mapping --ai     # rules + AI (needs the provider's key in
                                                   # .env; one call per case)

Prints per case: fields right, wrong (a confident wrong pick is the costly kind), and missed.
"""

import argparse
import time
from dataclasses import dataclass

from app.mapping.fingerprint import header_fingerprint
from app.mapping.profile import profile_table
from app.mapping.suggest import REVIEW_BELOW, build_suggestion
from scripts.mapping_fixtures import CASES, Case, table_for

PAUSE_SECONDS = 4


@dataclass
class Score:
    right: int = 0
    wrong: int = 0
    wrong_confident: int = 0
    missed: int = 0

    @property
    def expected(self) -> int:
        return self.right + self.wrong + self.missed


def score(case: Case, use_ai: bool) -> tuple[Score, list[str]]:
    table = table_for(case)
    suggestion = build_suggestion(
        case.kind,
        "Partner",
        profile_table(table),
        header_fingerprint(table.headers),
        [],
        use_ai=use_ai,
    )
    result, notes = Score(), []
    for field_name, header in case.expected().items():
        got = suggestion.fields.get(field_name)
        column = got.column if got else None
        if column == header:
            result.right += 1
        elif column is None:
            result.missed += 1
            notes.append(f"missed {field_name} (expected “{header}”)")
        else:
            result.wrong += 1
            confident = got.confidence >= REVIEW_BELOW
            result.wrong_confident += confident
            notes.append(
                f"{'CONFIDENT ' if confident else ''}wrong {field_name}: “{column}” "
                f"(expected “{header}”)"
            )
    if suggestion.ai_message:
        notes.append(suggestion.ai_message)
    return result, notes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ai", action="store_true", help="also ask the AI")
    args = parser.parse_args()

    total = Score()
    for i, case in enumerate(CASES):
        if args.ai and i:
            time.sleep(PAUSE_SECONDS)  # stay inside free-tier rate limits
        s, notes = score(case, args.ai)
        for k in ("right", "wrong", "wrong_confident", "missed"):
            setattr(total, k, getattr(total, k) + getattr(s, k))
        print(f"{case.name:28} {s.right}/{s.expected} right")
        for note in notes:
            print(f"    {note}")
    print(
        f"\n{'Rules + AI' if args.ai else 'Rules only'}: {total.right}/{total.expected} right "
        f"({total.right / total.expected:.0%}), {total.wrong} wrong "
        f"({total.wrong_confident} confidently), {total.missed} left for a person to map"
    )


if __name__ == "__main__":
    main()
