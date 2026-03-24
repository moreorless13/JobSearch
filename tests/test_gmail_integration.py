from job_agent.tools.gmail import (
    extract_message_body,
    resolve_gmail_auth_mode,
    search_gmail_job_updates_impl,
)


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeMessagesResource:
    def __init__(self, list_payloads, get_payloads):
        self.list_payloads = list_payloads
        self.get_payloads = get_payloads
        self.list_calls = []
        self.get_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return FakeRequest(self.list_payloads.pop(0))

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.get_payloads[kwargs["id"]])


class FakeUsersResource:
    def __init__(self, messages_resource):
        self.messages_resource = messages_resource

    def messages(self):
        return self.messages_resource


class FakeGmailService:
    def __init__(self, messages_resource):
        self.messages_resource = messages_resource

    def users(self):
        return FakeUsersResource(self.messages_resource)


def test_resolve_gmail_auth_mode_prefers_service_account(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/tmp/service-account.json")
    monkeypatch.setenv("GMAIL_DELEGATED_USER", "james@example.com")
    monkeypatch.delenv("GMAIL_TOKEN_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE", raising=False)

    assert resolve_gmail_auth_mode() == "service_account"


def test_resolve_gmail_auth_mode_uses_oauth_token(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GMAIL_DELEGATED_USER", raising=False)
    monkeypatch.setenv("GMAIL_TOKEN_FILE", ".gmail_token.json")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE", raising=False)

    assert resolve_gmail_auth_mode() == "oauth_token"


def test_extract_message_body_prefers_nested_text_plain() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": "PGRpdj5IaTwvZGl2Pg"}},
            {"mimeType": "text/plain", "body": {"data": "SGVsbG8gZnJvbSBHbWFpbA"}},
        ],
    }

    assert extract_message_body(payload) == "Hello from Gmail"


def test_search_gmail_job_updates_impl_fetches_and_dedupes_messages(monkeypatch) -> None:
    messages_resource = FakeMessagesResource(
        list_payloads=[
            {"messages": [{"id": "1"}, {"id": "2"}]},
            {"messages": [{"id": "2"}, {"id": "3"}]},
        ],
        get_payloads={
            "1": {
                "id": "1",
                "threadId": "t1",
                "snippet": "Thanks for applying",
                "labelIds": ["INBOX"],
                "internalDate": "300",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Application received"},
                        {"name": "From", "value": "jobs@acme.com"},
                        {"name": "Date", "value": "Mon, 01 Jan 2026 09:00:00 -0500"},
                    ],
                    "body": {"data": "VGhhbmtzIGZvciBhcHBseWluZw"},
                },
            },
            "2": {
                "id": "2",
                "threadId": "t2",
                "snippet": "Interview availability",
                "labelIds": ["INBOX"],
                "internalDate": "200",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Interview availability"},
                        {"name": "From", "value": "recruiting@beta.com"},
                    ],
                    "body": {"data": "UGxlYXNlIHNoYXJlIHlvdXIgYXZhaWxhYmlsaXR5"},
                },
            },
            "3": {
                "id": "3",
                "threadId": "t3",
                "snippet": "Newsletter",
                "labelIds": ["CATEGORY_PROMOTIONS"],
                "internalDate": "100",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Jobs newsletter"},
                        {"name": "From", "value": "alerts@example.com"},
                    ],
                    "body": {"data": "TmV3IGpvYnMgdGhpcyB3ZWVr"},
                },
            },
        },
    )

    monkeypatch.setattr(
        "job_agent.tools.gmail.build_gmail_service",
        lambda: FakeGmailService(messages_resource),
    )

    result = search_gmail_job_updates_impl(
        queries=["application", "interview"],
        max_results=3,
    )

    assert result["implemented"] is True
    assert [message["id"] for message in result["messages"]] == ["1", "2", "3"]
    assert result["messages"][0]["body"] == "Thanks for applying"
    assert len(messages_resource.get_calls) == 3
