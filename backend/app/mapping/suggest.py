"""Combine what we know into one suggested mapping (TDD §9.1).

In order of trust:
1. A saved mapping version with exactly these columns: reuse it whole.
2. A column kept from the active mapping, same name: keep it.
3. Rules and AI agree: confident.
4. They disagree: the AI's pick when it is at least as sure, the other offered as an alternative,
   and confidence lowered so the field shows as "check this".
"""

from dataclasses import asdict, dataclass, field

from app.enums import FileKind
from app.mapping.fields import FIELDS
from app.mapping.fingerprint import normalise_header
from app.mapping.llm import AISuggestionError, MappingProposal, suggest_with_ai
from app.mapping.profile import ColumnProfile
from app.mapping.rules import Choice, infer_options, suggest_with_rules

REVIEW_BELOW = 0.7  # fields below this confidence are highlighted for review [A17]


@dataclass
class FieldSuggestion:
    field: str
    column: str | None
    confidence: float
    reason: str
    source: str  # saved | kept | rules | ai | both | none
    alternative: str | None = None
    options: dict = field(default_factory=dict)


@dataclass
class Suggestion:
    fields: dict[str, FieldSuggestion]
    personal_data_columns: list[str]
    ai_status: str  # used | unavailable | not_needed
    ai_message: str | None = None
    matched_version: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SavedVersion:
    version: int
    fingerprint: str
    mapping: dict
    personal_data_columns: list[str]
    is_active: bool


def _validated_ai(proposal: MappingProposal, kind: FileKind, headers: list[str]) -> dict:
    """Keep only answers that name a real field and a real column, each column once."""
    known_fields = {f.name for f in FIELDS[kind]}
    by_name = {normalise_header(h): h for h in headers}
    picks: dict[str, Choice] = {}
    for choice in sorted(proposal.fields, key=lambda c: -c.confidence):
        if choice.field not in known_fields or choice.field in picks:
            continue
        header = by_name.get(normalise_header(choice.column)) if choice.column else None
        if header is None or any(p.column == header for p in picks.values()):
            continue
        picks[choice.field] = Choice(
            header, round(min(max(choice.confidence, 0.0), 1.0), 2), choice.reason
        )
    return picks


def _merge(rule: Choice | None, ai: Choice | None) -> FieldSuggestion | None:
    if rule and ai and rule.column == ai.column:
        confidence = round(min(1.0, max(rule.confidence, ai.confidence) + 0.05), 2)
        return FieldSuggestion("", ai.column, confidence, ai.reason, "both")
    if rule and ai:
        if ai.confidence >= rule.confidence:
            winner, other, source = ai, rule, "ai"
        else:
            winner, other, source = rule, ai, "rules"
        confidence = round(min(winner.confidence, REVIEW_BELOW - 0.05), 2)
        reason = f"{winner.reason} (rules and AI disagree; the other pick is offered)"
        return FieldSuggestion("", winner.column, confidence, reason, source, other.column)
    if ai:
        return FieldSuggestion("", ai.column, ai.confidence, ai.reason, "ai")
    if rule:
        return FieldSuggestion("", rule.column, rule.confidence, rule.reason, "rules")
    return None


def build_suggestion(
    kind: FileKind,
    partner: str,
    profiles: list[ColumnProfile],
    fingerprint: str,
    saved: list[SavedVersion],
    use_ai: bool = True,
) -> Suggestion:
    headers = [p.header for p in profiles]
    by_header = {p.header: p for p in profiles}

    same_columns = next(
        (v for v in sorted(saved, key=lambda v: -v.version) if v.fingerprint == fingerprint), None
    )
    if same_columns:
        fields = {
            name: FieldSuggestion(
                name,
                spec["column"],
                1.0,
                f"Same columns as saved mapping v{same_columns.version}",
                "saved",
                options={k: v for k, v in spec.items() if k != "column"},
            )
            for name, spec in same_columns.mapping["fields"].items()
        }
        return Suggestion(
            fields,
            list(same_columns.personal_data_columns),
            "not_needed",
            matched_version=same_columns.version,
        )

    active = next((v for v in saved if v.is_active), None)
    previous_specs: dict = active.mapping["fields"] if active else {}

    rules = suggest_with_rules(kind, profiles)
    ai_picks: dict[str, Choice] = {}
    ai_personal: list[str] = []
    ai_status, ai_message = "unavailable", None
    if use_ai:
        try:
            proposal = suggest_with_ai(kind, partner, profiles)
            ai_picks = _validated_ai(proposal, kind, headers)
            ai_personal = [h for h in proposal.personal_data_columns if h in by_header]
            ai_status = "used"
        except AISuggestionError as exc:
            ai_message = f"{exc} Showing rule-based suggestions."
    else:
        ai_message = "AI suggestions are switched off. Showing rule-based suggestions."

    fields: dict[str, FieldSuggestion] = {}
    for f in FIELDS[kind]:
        kept = previous_specs.get(f.name, {}).get("column")
        if kept in by_header:
            suggestion = FieldSuggestion(
                f.name, kept, 0.97, f"Same column as in mapping v{active.version}", "kept"
            )
        else:
            suggestion = _merge(rules.get(f.name), ai_picks.get(f.name))
        if suggestion is None:
            continue
        suggestion.field = f.name
        fields[f.name] = suggestion

    # One column feeds one field: when two fields want it, the more confident one keeps it.
    owner: dict[str, str] = {}
    for name, s in sorted(fields.items(), key=lambda kv: -kv[1].confidence):
        if s.column not in owner:
            owner[s.column] = name
        elif s.alternative and s.alternative not in owner:
            # Its first pick belongs to a surer field; the other candidate is free.
            fields[name] = FieldSuggestion(
                name,
                s.alternative,
                min(s.confidence, REVIEW_BELOW - 0.05),
                f"“{s.column}” fits {owner[s.column]} better, so the other candidate is used",
                s.source,
                alternative=s.column,
            )
            owner[s.alternative] = name
        else:
            fields[name] = FieldSuggestion(
                name,
                None,
                0.0,
                f"“{s.column}” is already used for another field",
                "none",
                alternative=s.column,
            )

    for f in FIELDS[kind]:
        s = fields.get(f.name)
        if s and s.column:
            s.options = infer_options(f, by_header[s.column], previous_specs.get(f.name))

    personal = [p.header for p in profiles if p.personal]
    personal += [h for h in ai_personal if h not in personal]
    personal = [h for h in personal if h not in owner]
    return Suggestion(fields, personal, ai_status, ai_message)
