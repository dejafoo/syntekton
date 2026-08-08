# SD1 operator notes

- Feature-pack expansion remains frozen until G1 is accepted.
- MockGateway runs set connector broker `mock=True`; enabling `git_ci_read` / `ops_read` in tests exercises fixture backends, not live GitHub/ops APIs.
- `staging_deploy` remains disabled by default; deployment still requires durable approval binding (G0).
- Task results now carry `executor_mode`, `executor_adapter_id`, `execution_mode` (`live`|`deterministic_mock`), and `activity_receipt`.
- PM5 documentation/status: hermetically implemented; do not treat fake-live connector receipts as operational proof.
