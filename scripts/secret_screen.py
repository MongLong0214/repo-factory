"""Refuse credential-shaped outgoing text without echoing the matched value."""
import re


class SecretScreenError(ValueError):
    pass


PATTERNS = (
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b",
    # Nostr/Buzz private keys (NIP-19 bech32 nsec, checksum not required to refuse).
    r"\bnsec1[02-9ac-hj-np-z]{20,}\b",
    r"\b(?:password|passwd|api[_-]?key|access[_-]?token|secret|client[_-]?secret|private[_-]?key)\b"
    r"[\"']?\s*[:=]\s*[\"']?[^\s\"',;}]{8,}",
    r"\b(?:Authorization\s*:\s*)?Bearer\s+[A-Za-z0-9._~+/-]{16,}=*",
    r"https?://[^\s/:]+:[^\s/@]{8,}@",
)
_CREDENTIAL = re.compile("|".join(f"(?:{pattern})" for pattern in PATTERNS), re.IGNORECASE)


def screen_text(text, location="outgoing content"):
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if _CREDENTIAL.search(text):
        raise SecretScreenError(f"SECRET_SCREEN: credential-shaped content in {location}; remove it before rendering")


def screen_value(value, location="input"):
    if isinstance(value, dict):
        for item in value.values():
            screen_value(item, location)
    elif isinstance(value, list):
        for item in value:
            screen_value(item, location)
    elif isinstance(value, (str, bytes)):
        screen_text(value, location)
