Add a GET /health endpoint that returns {"status":"ok"} with HTTP 200.

Acceptance criteria:
- Route is registered on the existing API app
- Response body is JSON with status=ok
- Existing tests still pass

Non-goals: no authentication, no new dependencies.
Constraints: stay within src/api/.
