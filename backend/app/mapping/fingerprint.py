import hashlib
import re


def normalise_header(header: str) -> str:
    return re.sub(r"\s+", " ", str(header)).strip().lower()


def header_fingerprint(headers: list[str]) -> str:
    """Same set of column names → same fingerprint, whatever their order or spacing."""
    names = sorted(normalise_header(h) for h in headers if str(h).strip())
    return hashlib.sha256("\x1f".join(names).encode()).hexdigest()[:16]
