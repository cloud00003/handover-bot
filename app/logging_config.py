"""Console logging with token redaction, including exception output."""

import logging
import re


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return re.sub(r"\b\d+:[A-Za-z0-9_-]{20,}", "[REDACTED]", text)


def configure_logging(secrets: tuple[str, ...] = ()) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(secrets))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
