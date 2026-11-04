"""Ask an LLM which column holds each field, from column names and value shapes only (TDD §9).

The model never sees a cell value: it gets each header with a description such as "date,
dd-Mon-yyyy" or "amount with decimals, all positive". Its answer is constrained to a JSON schema,
parsed into MappingProposal, and then checked against the file again, because a model can still
name a column that isn't there. Any failure raises AISuggestionError and the caller falls back to
rules.

Two providers, chosen with LLM_PROVIDER: Groq (default; openai/gpt-oss-120b with strict
structured outputs) and Anthropic (Claude). Both are called through their own SDKs.
"""

import json
import logging

import anthropic
import groq
from pydantic import BaseModel, Field, ValidationError

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
    settings = get_settings()
    return settings.ai_enabled and bool(settings.llm_api_key)


def suggest_with_ai(kind: FileKind, partner: str, profiles: list[ColumnProfile]) -> MappingProposal:
    settings = get_settings()
    if not is_configured():
        raise AISuggestionError("AI suggestions are switched off (no API key is configured).")
    prompt = _prompt(kind, partner, profiles)
    if settings.llm_provider == "groq":
        return _ask_groq(settings, prompt)
    return _ask_anthropic(settings, prompt)


def _strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic's schema, tightened for strict decoding: every object closed and fully required."""

    def close(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for value in node:
                close(value)
        return node

    return close(model.model_json_schema())


GROQ_ATTEMPTS = 2  # strict decoding still occasionally emits broken JSON; one retry fixes it


def _ask_groq(settings, prompt: str) -> MappingProposal:
    client = groq.Groq(api_key=settings.groq_api_key, timeout=TIMEOUT_SECONDS, max_retries=1)
    request = {
        "model": settings.llm_model_name,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "mapping_proposal",
                "strict": True,
                "schema": _strict_schema(MappingProposal),
            },
        },
        # Choosing columns is classification-like work; low effort keeps it quick and cheap.
        "reasoning_effort": "low",
        "max_completion_tokens": 4000,
        "temperature": 0,
    }
    for attempt in range(1, GROQ_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(**request)
            break
        except groq.BadRequestError as exc:
            # Groq rejects its own malformed output with json_validate_failed; that's random,
            # so ask again once before giving up.
            if "json_validate_failed" in str(exc) and attempt < GROQ_ATTEMPTS:
                log.info("AI mapping: Groq produced invalid JSON, retrying")
                continue
            log.warning("AI mapping request failed: %s", str(exc)[:300])
            raise AISuggestionError("The AI service returned an error.") from exc
        except groq.APITimeoutError as exc:
            raise AISuggestionError("The AI took too long to answer.") from exc
        except groq.RateLimitError as exc:
            raise AISuggestionError("The AI service is busy right now.") from exc
        except groq.APIStatusError as exc:
            log.warning("AI mapping request failed: %s", str(exc)[:300])
            raise AISuggestionError("The AI service returned an error.") from exc
        except groq.APIConnectionError as exc:
            raise AISuggestionError("Couldn't reach the AI service.") from exc

    choice = response.choices[0]
    if choice.finish_reason == "length" or not choice.message.content:
        raise AISuggestionError("The AI's answer was incomplete.")
    try:
        proposal = MappingProposal.model_validate_json(choice.message.content)
    except ValidationError as exc:
        raise AISuggestionError("The AI's answer wasn't in the expected shape.") from exc
    usage = response.usage
    if usage:
        log.info(
            "AI mapping (groq): %s input, %s output tokens",
            usage.prompt_tokens,
            usage.completion_tokens,
        )
    return proposal


def _ask_anthropic(settings, prompt: str) -> MappingProposal:
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key, timeout=TIMEOUT_SECONDS, max_retries=1
    )
    try:
        response = client.beta.messages.parse(
            model=settings.llm_model_name,
            max_tokens=4000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=MappingProposal,
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
    log.info(
        "AI mapping (anthropic): %s input, %s output tokens",
        usage.input_tokens,
        usage.output_tokens,
    )
    return response.parsed_output
