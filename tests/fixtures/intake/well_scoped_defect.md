Fix bug: the sample API returns 500 when the request body is empty JSON {}.

Acceptance criteria:
- Empty object body returns 400 with a clear error message
- Valid payloads still succeed
- Add or update a regression test

Given that callers sometimes POST {}, this must be handled before business logic.
