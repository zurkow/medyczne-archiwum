## Test Writing

### Test Behavior
Focus on what code does, not how it does it, to allow safe refactoring.

### Clear Names
Use descriptive behaviour-sentence names explaining what's tested and expected (`test_non_owner_and_admin_cannot_read_an_unshared_document`).

### Replace External Dependencies with Fakes
Isolate tests from OCR, embeddings, LM Studio and other external services using hand-written fakes (see "Hand-written fakes, tests run offline"); tests use a real, migrated temporary SQLite database rather than a mocked one.

### Fast Execution
Keep unit tests fast (milliseconds) so developers run them frequently.

### Risk-Based Testing
Prioritize testing based on business criticality and likelihood of bugs.

### Balance Coverage and Velocity
Adjust test coverage based on project needs and team workflow; required negative security cases are never optional.

### Critical Path Focus
Ensure core user workflows and critical business logic are well-tested.

### Appropriate Depth
Match edge case testing to the risk profile of the code.

## Project-specific standards (discovered)

### Test-first: confirm RED
Every task starts with a test that must fail first ("Potwierdź red"), then the implementation makes it pass. If RED cannot be reproduced because the work already exists, record that explicitly instead of manufacturing a failing state.

### Hand-written fakes, tests run offline
Infrastructure (OCR, embeddings, LM Studio generation, retrievers) is injected via constructor args or `app.state` and replaced in tests by small hand-written `Fake*` classes — no mock library. Simulate missing tools with `monkeypatch`. No real network or model calls in the suite; target-machine acceptance testing is a separate step.
```python
class FakeGenerator:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls = 0
```

### Isolated, migrated test database
pytest runs with `pythonpath = ["src"]`. `tests/conftest.py` provides the core fixtures: `auth_engine` (`tmp_path` SQLite upgraded via Alembic to head), `auth_session_factory`, `auth_db`, `app` (`create_app(testing=True)` with `get_session` overridden), `client` (`TestClient` with https base URL), `admin_user`, `member_user`, `extract_csrf_token`. Clear `MEDICAL_ARCHIVE_DATABASE_URL` so developer overrides cannot redirect setup. Test modules add small private helpers (`_login`, `_create_user`) and local fixtures.

### Behaviour-named tests with required negative cases
Tests are module-level `def test_<behaviour_sentence>()` — no classes, no docstrings. Every security-relevant feature has negative tests: no cross-account leaks, grant and revocation, admin and non-owner get 404, grantees read-only. Also required: OCR failure, assistant citations / no-data / model failure, and backup wrong password / corruption / no destructive overwrite.
```python
def test_non_owner_and_admin_cannot_read_an_unshared_document(...):
```

### Plain asserts and synthetic data
Use bare `assert` on `response.status_code` and `response.json()` fields, `pytest.raises` for service errors and `pytest.mark.parametrize` for input variants. Test data is synthetic (`@example.test` emails, "Synthetic value").

### Prove risky dependencies with a technical test first
Before building on a risky native dependency, prove it with a real compatibility test (e.g. `tests/rag/test_sqlite_vec_compatibility.py` for partitioned KNN). If it fails, stop and revisit the design instead of working around it (e.g. by post-filtering other users' results).
