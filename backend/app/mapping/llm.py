"""Ask Claude which column holds each field, from column names and value shapes only (TDD §9).

The model never sees a cell value: it gets each header with a description such as "date,
dd-Mon-yyyy" or "amount with decimals, all positive". Its answer is schema-checked by the SDK
and then validated against the file again, because a model can still name a column that isn't
there. Any failure raises AISuggestionError and the caller falls back to rules.
"""

import json
import logging

import anthropic
from pydantic import BaseModel, Field

from app.config import get_settings
from app.enums import FileKind
from app.mapping.fields import FIELDS
from app.mapping.profile import ColumnProfile

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 45.0


class AISuggestionError(RuntimeError):
    """The AI couldn't give a usable answer. The message is safe to show to the user."""


class FieldChoice(BaseModel):
    field: str = Field(description="Target field name, exactly as listed")
    column: str | None = Field(description="Exact source column header, or null if none fits")
    confidence: float = Field(description="0 to 1: how sure you are this column is right")
    reason: str = Field(description="One short sentence a finance user can read")


class MappingProposal(BaseModel):
    fields: list[FieldChoice]
    personal_data_columns: list[str] = Field(
        description="Headers that look like customer personal data (names, phones, addresses)"
    )


_SYSTEM = """You map columns in an Indian e-commerce partner's report (marketplace settlement, \
courier COD remittance, freight invoice, order export or warehouse return scans) to the fields \
a reconciliation system needs.

You are given the target fields and, for each column in the file, its header and a description \
of its values. You never see the values themselves.

Rules:
- Use only headers from the list, spelled exactly as given. Use each header for at most one field.
- If no column fits a field, return null for it. A wrong guess is worse than a gap: a person \
reviews every mapping, and an unmapped required field simply asks them to choose.
- Confidence: 0.9+ when the header clearly names the field; 0.6-0.9 when the meaning is likely \
but not certain; below 0.6 when you are guessing.
- Deductions such as commission, fees, TCS and TDS are separate fields; do not map the net \
payout or the gross sale to them.
- List columns that look like customer personal data (names, phone numbers, addresses, emails) \
in personal_data_columns, even though no field uses them."""


def _prompt(kind: FileKind, partner: str, profiles: list[ColumnProfile]) -> str:
    fields = [
        {
            "field": f.name,
            "label": f.label,
            "meaning": f.description,
            "type": f.type.value,
            "required": f.required and not f.key_group,
        }
        for f in FIELDS[kind]
    ]
    columns = [p.for_ai() for p in profiles]
    return (
        f"Partner: {partner}\nReport type: {kind.value}\n\n"
        f"Target fields:\n{json.dumps(fields, indent=1)}\n\n"
        f"Columns in the file (header and value description only):\n"
        f"{json.dumps(columns, indent=1)}\n\n"
        "Return one entry per target field."
    )


def is_configured() -> bool:
    return bool(get_settings().anthropic_api_key)


def suggest_with_ai(kind: FileKind, partner: str, profiles: list[ColumnProfile]) -> MappingProposal:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise AISuggestionError("AI suggestions are switched off (no API key is configured).")
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key, timeout=TIMEOUT_SECONDS, max_retries=1
    )
    try:
        response = client.beta.messages.parse(
            model=settings.llm_model,
            max_tokens=4000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _prompt(kind, partner, profiles)}],
            output_format=MappingProposal,
            # Choosing columns is classification-like work; low effort keeps it quick and cheap.
            output_config={"effort": "low"},
            # If a request is declined, the API retries it on a suitable model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APITimeoutError as exc:
        raise AISuggestionError("The AI took too long to answer.") from exc
    except anthropic.RateLimitError as exc:
        raise AISuggestionError("The AI service is busy right now.") from exc
    except anthropic.APIStatusError as exc:
        log.warning("AI mapping request failed: %s", exc)
        raise AISuggestionError("The AI service returned an error.") from exc
    except anthropic.APIConnectionError as exc:
        raise AISuggestionError("Couldn't reach the AI service.") from exc

    if response.stop_reason == "refusal":
        raise AISuggestionError("The AI declined to answer this request.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise AISuggestionError("The AI's answer was incomplete.")
    usage = response.usage
    log.info("AI mapping: %s input, %s output tokens", usage.input_tokens, usage.output_tokens)
    return response.parsed_output
