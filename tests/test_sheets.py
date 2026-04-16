from collections.abc import Iterable
from typing import Any, cast

import google.auth as google_auth
import google.auth.impersonated_credentials as impersonated_credentials
import google.oauth2.service_account as service_account


class FakeRequest:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload


class FakeValuesResource:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    def batchGet(self, **_kwargs: object) -> FakeRequest:
        return FakeRequest({"valueRanges": self.state["valueRanges"]})

    def update(self, **kwargs: object) -> FakeRequest:
        cast(list[dict[str, object]], self.state["updates"]).append(dict(kwargs))
        return FakeRequest({"updatedRange": kwargs["range"]})

    def append(self, **kwargs: object) -> FakeRequest:
        cast(list[dict[str, object]], self.state["appends"]).append(dict(kwargs))
        return FakeRequest({"updates": {"updatedRange": "'Tracker'!A3:F3"}})


class FakeSpreadsheetsResource:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state
        self.values_resource = FakeValuesResource(state)

    def get(self, **_kwargs: object) -> FakeRequest:
        return FakeRequest(cast(dict[str, object], self.state["metadata"]))

    def values(self) -> FakeValuesResource:
        return self.values_resource


class FakeSheetsService:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state
        self.spreadsheets_resource = FakeSpreadsheetsResource(state)

    def spreadsheets(self) -> FakeSpreadsheetsResource:
        return self.spreadsheets_resource


class FakeCredentials:
    def __init__(self, source: str, scopes: Iterable[Any]) -> None:
        self.source = source
        self.scopes = list(scopes)
        self.subject: str | None = None

    @classmethod
    def from_service_account_info(cls, info: dict[str, str], scopes: Iterable[Any]) -> "FakeCredentials":
        return cls(source=f"info:{info['client_email']}", scopes=scopes)

    @classmethod
    def from_service_account_file(cls, filename: str, scopes: Iterable[Any]) -> "FakeCredentials":
        return cls(source=f"file:{filename}", scopes=scopes)

    def with_subject(self, subject: str) -> "FakeCredentials":
        delegated = FakeCredentials(self.source, self.scopes)
        delegated.subject = subject
        return delegated


class FakeADCCredentials:
    def __init__(self, service_account_email: str = "runner@example.com") -> None:
        self.service_account_email = service_account_email
        self.signer_email = service_account_email


class FakeLoadedImpersonatedADCCredentials(FakeADCCredentials):
    def __init__(self, source_credentials: Any, service_account_email: str = "runner@example.com") -> None:
        super().__init__(service_account_email=service_account_email)
        self._source_credentials = source_credentials


class FakeImpersonatedCredentials:
    def __init__(
        self,
        *,
        source_credentials: Any,
        target_principal: str,
        target_scopes: Iterable[Any],
        subject: str | None = None,
    ) -> None:
        self.source_credentials = source_credentials
        self.target_principal = target_principal
        self.target_scopes = list(target_scopes)
        self.subject = subject


def load_sheets_module() -> Any:
    import job_agent.tools.sheets as sheets

    return cast(Any, sheets)


def test_extract_spreadsheet_id() -> None:
    sheets = load_sheets_module()
    sheet_id = sheets.extract_spreadsheet_id("https://docs.google.com/spreadsheets/d/abc123XYZ/edit#gid=0")
    assert sheet_id == "abc123XYZ"


def test_resolve_sheets_delegated_user_prefers_generic_env(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    monkeypatch.setenv("GOOGLE_DELEGATED_USER", "generic@example.com")
    monkeypatch.setenv("GMAIL_DELEGATED_USER", "gmail@example.com")

    assert sheets.resolve_sheets_delegated_user() == "generic@example.com"


def test_resolve_sheets_delegated_user_falls_back_to_gmail_env(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    monkeypatch.delenv("GOOGLE_DELEGATED_USER", raising=False)
    monkeypatch.setenv("GMAIL_DELEGATED_USER", "gmail@example.com")

    assert sheets.resolve_sheets_delegated_user() == "gmail@example.com"


def test_load_service_account_credentials_uses_delegated_subject(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    monkeypatch.setattr(service_account, "Credentials", FakeCredentials)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"client_email":"svc@example.com"}')
    monkeypatch.setenv("GOOGLE_DELEGATED_USER", "delegate@example.com")

    credentials = cast(FakeCredentials, sheets.load_service_account_credentials())

    assert credentials.subject == "delegate@example.com"


def test_load_service_account_credentials_uses_adc_without_delegation(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_DELEGATED_USER", raising=False)
    monkeypatch.delenv("GMAIL_DELEGATED_USER", raising=False)
    monkeypatch.setattr(google_auth, "default", lambda scopes=None: (FakeADCCredentials(), "test-project"))

    credentials = sheets.load_service_account_credentials()

    assert isinstance(credentials, FakeADCCredentials)


def test_load_service_account_credentials_uses_adc_impersonation_for_delegation(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("GOOGLE_DELEGATED_USER", "delegate@example.com")
    monkeypatch.setattr(google_auth, "default", lambda scopes=None: (FakeADCCredentials("runner@example.com"), "test-project"))
    monkeypatch.setattr(impersonated_credentials, "Credentials", FakeImpersonatedCredentials)

    credentials = cast(FakeImpersonatedCredentials, sheets.load_service_account_credentials())

    assert credentials.target_principal == "runner@example.com"
    assert credentials.subject == "delegate@example.com"


def test_load_service_account_credentials_supports_adc_file_without_delegation(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_load_credentials_from_file(filename: str, scopes: Iterable[Any]) -> tuple[Any, str]:
        calls.append((filename, tuple(scopes)))
        return FakeADCCredentials("runner@example.com"), "test-project"

    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/tmp/adc.json")
    monkeypatch.delenv("GOOGLE_DELEGATED_USER", raising=False)
    monkeypatch.delenv("GMAIL_DELEGATED_USER", raising=False)
    monkeypatch.setattr(google_auth, "load_credentials_from_file", fake_load_credentials_from_file)

    credentials = sheets.load_service_account_credentials()

    assert isinstance(credentials, FakeADCCredentials)
    assert calls == [("/tmp/adc.json", ("https://www.googleapis.com/auth/spreadsheets",))]


def test_load_service_account_credentials_supports_impersonated_adc_file_for_delegation(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    calls: list[tuple[str, tuple[str, ...]]] = []
    nested_source_credentials = object()

    def fake_load_credentials_from_file(filename: str, scopes: Iterable[Any]) -> tuple[Any, str]:
        calls.append((filename, tuple(scopes)))
        return FakeLoadedImpersonatedADCCredentials(
            source_credentials=nested_source_credentials,
            service_account_email="runner@example.com",
        ), "test-project"

    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/tmp/adc.json")
    monkeypatch.setenv("GOOGLE_DELEGATED_USER", "delegate@example.com")
    monkeypatch.setattr(google_auth, "load_credentials_from_file", fake_load_credentials_from_file)
    monkeypatch.setattr(impersonated_credentials, "Credentials", FakeImpersonatedCredentials)

    credentials = cast(FakeImpersonatedCredentials, sheets.load_service_account_credentials())

    assert calls == [
        ("/tmp/adc.json", ("https://www.googleapis.com/auth/spreadsheets",)),
        ("/tmp/adc.json", ("https://www.googleapis.com/auth/cloud-platform",)),
    ]
    assert credentials.source_credentials is nested_source_credentials
    assert credentials.target_principal == "runner@example.com"
    assert credentials.subject == "delegate@example.com"


def test_resolve_header_mapping_understands_aliases() -> None:
    sheets = load_sheets_module()
    mapping = sheets.resolve_header_mapping(["Company", "Title", "Tailor Resume", "Notes", "Duplicate Key"])
    assert mapping["company"] == 0
    assert mapping["role_title"] == 1
    assert mapping["tailor_resume"] == 2
    assert mapping["notes"] == 3
    assert mapping["duplicate_key"] == 4


def test_project_headers_adds_missing_canonical_columns() -> None:
    sheets = load_sheets_module()
    headers = sheets.project_headers(["Company", "Role Title"], {"company": "Acme", "posting_url": "https://example.com"})
    assert headers == ["Company", "Role Title", "Posting URL"]


def test_project_headers_adds_tailor_resume_column() -> None:
    sheets = load_sheets_module()
    headers = sheets.project_headers(["Company", "Role Title"], {"company": "Acme", "tailor_resume": "yes"})
    assert headers == ["Company", "Role Title", "Tailor Resume"]


def test_project_headers_adds_experience_columns() -> None:
    sheets = load_sheets_module()
    headers = sheets.project_headers(
        ["Company", "Role Title"],
        {
            "company": "Acme",
            "required_experience_years": 5.0,
            "candidate_experience_years": 6.2,
            "experience_gap_years": 1.2,
        },
    )
    assert headers == [
        "Company",
        "Role Title",
        "Required Experience Years",
        "Candidate Experience Years",
        "Experience Gap Years",
    ]


def test_render_row_values_preserves_existing_notes() -> None:
    sheets = load_sheets_module()
    headers = ["Company", "Role Title", "Notes", "Duplicate Key"]
    existing_row = {
        "__raw_by_header": {
            "Company": "Acme",
            "Role Title": "Solutions Engineer",
            "Notes": "existing note",
            "Duplicate Key": "acme::solutions engineer::remote",
        }
    }
    merged_row = {
        "company": "Acme",
        "role_title": "Solutions Engineer",
        "notes": "existing note\nnew note",
        "duplicate_key": "acme::solutions engineer::remote",
    }

    values = sheets.render_row_values(headers, merged_row, existing_row=existing_row)

    assert values == ["Acme", "Solutions Engineer", "existing note\nnew note", "acme::solutions engineer::remote"]


def test_rows_match_hybrid_prefers_duplicate_key() -> None:
    sheets = load_sheets_module()
    existing = {"company": "Acme", "role_title": "Solutions Engineer", "location": "Remote", "duplicate_key": "same"}
    candidate = {"company": "Other", "role_title": "Other", "location": "NY", "duplicate_key": "same"}
    assert sheets.rows_match(existing, candidate, match_strategy="hybrid") is True


def test_upsert_tracker_row_updates_existing_row(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    state = {
        "metadata": {
            "properties": {"title": "Jobs"},
            "sheets": [{"properties": {"title": "Tracker"}}],
        },
        "valueRanges": [
            {
                "values": [
                    ["Company", "Role Title", "Notes", "Duplicate Key"],
                    ["Acme", "Solutions Engineer", "existing note", "acme::solutions engineer::remote"],
                ]
            }
        ],
        "updates": [],
        "appends": [],
    }

    monkeypatch.setattr("job_agent.tools.sheets.build_sheets_service", lambda: FakeSheetsService(state))

    result = sheets.upsert_tracker_row_impl(
        sheet_url="https://docs.google.com/spreadsheets/d/abc123/edit",
        row={
            "company": "Acme",
            "role_title": "Solutions Engineer",
            "notes": "new note",
            "duplicate_key": "acme::solutions engineer::remote",
        },
        duplicate_key="acme::solutions engineer::remote",
        match_strategy="hybrid",
    )

    assert result["implemented"] is True
    assert result["status"] == "updated"
    assert len(state["updates"]) == 1
    assert state["updates"][0]["body"]["values"][0][2] == "existing note\nnew note"


def test_upsert_tracker_row_preserves_existing_status_when_update_defaults_to_new(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    state = {
        "metadata": {
            "properties": {"title": "Jobs"},
            "sheets": [{"properties": {"title": "Tracker"}}],
        },
        "valueRanges": [
            {
                "values": [
                    ["Company", "Role Title", "Status", "Duplicate Key"],
                    ["Acme", "Solutions Engineer", "Interviewing", "acme::solutions engineer::remote"],
                ]
            }
        ],
        "updates": [],
        "appends": [],
    }

    monkeypatch.setattr("job_agent.tools.sheets.build_sheets_service", lambda: FakeSheetsService(state))

    result = sheets.upsert_tracker_row_impl(
        sheet_url="https://docs.google.com/spreadsheets/d/abc123/edit",
        row={
            "company": "Acme",
            "role_title": "Solutions Engineer",
            "status": "New",
            "duplicate_key": "acme::solutions engineer::remote",
        },
        duplicate_key="acme::solutions engineer::remote",
        match_strategy="hybrid",
    )

    assert result["implemented"] is True
    assert result["status"] == "updated"
    assert result["row"]["status"] == "Interviewing"
    assert state["updates"][0]["body"]["values"][0][2] == "Interviewing"


def test_upsert_tracker_row_preserves_existing_experience_values(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    state = {
        "metadata": {
            "properties": {"title": "Jobs"},
            "sheets": [{"properties": {"title": "Tracker"}}],
        },
        "valueRanges": [
            {
                "values": [
                    [
                        "Company",
                        "Role Title",
                        "Required Experience Years",
                        "Candidate Experience Years",
                        "Experience Gap Years",
                        "Duplicate Key",
                    ],
                    ["Acme", "Solutions Engineer", "6", "7.5", "1.5", "acme::solutions engineer::remote"],
                ]
            }
        ],
        "updates": [],
        "appends": [],
    }

    monkeypatch.setattr("job_agent.tools.sheets.build_sheets_service", lambda: FakeSheetsService(state))

    result = sheets.upsert_tracker_row_impl(
        sheet_url="https://docs.google.com/spreadsheets/d/abc123/edit",
        row={
            "company": "Acme",
            "role_title": "Solutions Engineer",
            "required_experience_years": 4.0,
            "candidate_experience_years": 5.0,
            "experience_gap_years": 1.0,
            "duplicate_key": "acme::solutions engineer::remote",
        },
        duplicate_key="acme::solutions engineer::remote",
        match_strategy="hybrid",
    )

    assert result["implemented"] is True
    assert result["row"]["required_experience_years"] == "6"
    assert result["row"]["candidate_experience_years"] == "7.5"
    assert result["row"]["experience_gap_years"] == "1.5"
    assert state["updates"][0]["body"]["values"][0][2:5] == ["6", "7.5", "1.5"]


def test_upsert_tracker_row_appends_and_extends_headers(monkeypatch: Any) -> None:
    sheets = load_sheets_module()
    state = {
        "metadata": {
            "properties": {"title": "Jobs"},
            "sheets": [{"properties": {"title": "Tracker"}}],
        },
        "valueRanges": [
            {
                "values": [
                    ["Company", "Role Title"],
                    ["Acme", "Solutions Engineer"],
                ]
            }
        ],
        "updates": [],
        "appends": [],
    }

    monkeypatch.setattr("job_agent.tools.sheets.build_sheets_service", lambda: FakeSheetsService(state))

    result = sheets.upsert_tracker_row_impl(
        sheet_url="https://docs.google.com/spreadsheets/d/abc123/edit",
        row={
            "company": "Beta",
            "role_title": "Integration Engineer",
            "posting_url": "https://example.com/jobs/2",
        },
        duplicate_key="beta::integration engineer::",
        match_strategy="hybrid",
    )

    assert result["implemented"] is True
    assert result["status"] == "inserted"
    assert len(state["updates"]) == 1
    assert state["updates"][0]["body"]["values"][0] == ["Company", "Role Title", "Posting URL", "Duplicate Key"]
    assert len(state["appends"]) == 1
    assert state["appends"][0]["body"]["values"][0][0] == "Beta"
