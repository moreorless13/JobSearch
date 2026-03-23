from job_agent.tools.sheets import (
    extract_spreadsheet_id,
    project_headers,
    render_row_values,
    resolve_header_mapping,
    rows_match,
    upsert_tracker_row_impl,
)


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeValuesResource:
    def __init__(self, state):
        self.state = state

    def batchGet(self, **_kwargs):
        return FakeRequest({"valueRanges": self.state["valueRanges"]})

    def update(self, **kwargs):
        self.state["updates"].append(kwargs)
        return FakeRequest({"updatedRange": kwargs["range"]})

    def append(self, **kwargs):
        self.state["appends"].append(kwargs)
        return FakeRequest({"updates": {"updatedRange": "'Tracker'!A3:F3"}})


class FakeSpreadsheetsResource:
    def __init__(self, state):
        self.state = state
        self.values_resource = FakeValuesResource(state)

    def get(self, **_kwargs):
        return FakeRequest(self.state["metadata"])

    def values(self):
        return self.values_resource


class FakeSheetsService:
    def __init__(self, state):
        self.state = state
        self.spreadsheets_resource = FakeSpreadsheetsResource(state)

    def spreadsheets(self):
        return self.spreadsheets_resource


def test_extract_spreadsheet_id() -> None:
    sheet_id = extract_spreadsheet_id("https://docs.google.com/spreadsheets/d/abc123XYZ/edit#gid=0")
    assert sheet_id == "abc123XYZ"


def test_resolve_header_mapping_understands_aliases() -> None:
    mapping = resolve_header_mapping(["Company", "Title", "Notes", "Duplicate Key"])
    assert mapping["company"] == 0
    assert mapping["role_title"] == 1
    assert mapping["notes"] == 2
    assert mapping["duplicate_key"] == 3


def test_project_headers_adds_missing_canonical_columns() -> None:
    headers = project_headers(["Company", "Role Title"], {"company": "Acme", "posting_url": "https://example.com"})
    assert headers == ["Company", "Role Title", "Posting URL"]


def test_render_row_values_preserves_existing_notes() -> None:
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

    values = render_row_values(headers, merged_row, existing_row=existing_row)

    assert values == ["Acme", "Solutions Engineer", "existing note\nnew note", "acme::solutions engineer::remote"]


def test_rows_match_hybrid_prefers_duplicate_key() -> None:
    existing = {"company": "Acme", "role_title": "Solutions Engineer", "location": "Remote", "duplicate_key": "same"}
    candidate = {"company": "Other", "role_title": "Other", "location": "NY", "duplicate_key": "same"}
    assert rows_match(existing, candidate, match_strategy="hybrid") is True


def test_upsert_tracker_row_updates_existing_row(monkeypatch) -> None:
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

    result = upsert_tracker_row_impl(
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


def test_upsert_tracker_row_preserves_existing_status_when_update_defaults_to_new(monkeypatch) -> None:
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

    result = upsert_tracker_row_impl(
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


def test_upsert_tracker_row_appends_and_extends_headers(monkeypatch) -> None:
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

    result = upsert_tracker_row_impl(
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
