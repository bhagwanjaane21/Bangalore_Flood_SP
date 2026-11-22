"""Telegram acknowledgement handling. Never log response bodies or token URLs."""

from dataclasses import dataclass, field

import requests


@dataclass(frozen=True)
class Outcome:
    status: str
    detail: str
    retry_after: int = 0
    message_id: int | None = None


@dataclass
class TelegramSender:
    token: str = field(repr=False)
    chat_id: str = field(repr=False)
    session: object = field(default_factory=requests.Session, repr=False)

    def send(self, text):
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=(3, 10), allow_redirects=False,
            )
        except requests.ConnectTimeout:
            return Outcome("retry", "connect_timeout")
        except requests.RequestException:
            # A read timeout or broken connection may follow a successful send.
            return Outcome("uncertain", "transport_ack_unknown")
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        code = body.get("error_code") if body.get("ok") is False else response.status_code
        if type(code) is not int:
            code = response.status_code
        if code == 429:
            parameters = body.get("parameters")
            delay = parameters.get("retry_after") if isinstance(parameters, dict) else None
            if type(delay) is not int or delay < 1:
                delay = 30
            return Outcome("retry", "rate_limited", retry_after=delay)
        if 500 <= code < 600:
            return Outcome("retry", "provider_unavailable")
        if 300 <= code < 500:
            return Outcome("failed", f"provider_rejected_{code}")
        if 200 <= response.status_code < 300 and body.get("ok") is True:
            result = body.get("result")
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if type(message_id) is int and message_id > 0:
                return Outcome("delivered", "provider_accepted", message_id=message_id)
        return Outcome("uncertain", "invalid_provider_ack")

    def close(self):
        self.session.close()
