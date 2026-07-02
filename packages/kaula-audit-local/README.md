# kaula-audit-local

The reference `AuditSink` implementation plus the rollback index, backed by
SQLite (file or in-memory).

- **Append-only, hash-chained:** every event's hash covers its content and
  the previous event's hash; `verify()` recomputes the whole chain, so
  tampering — edits, deletions, reordering — is detectable, not just
  discouraged. The trail is verifiable, not merely readable.
- **PII by reference:** payloads carry fingerprints and source hashes, never
  raw argument values or personal data (enforced by the loop, expected here).
- **Rollback index (`ToolVersionStore`):** every tool version is
  version-addressable; reverting to the previous version is a single, logged
  action.

Fleet-scale, tamper-evident storage as a service is the commercial
`kaula-audit-cloud` implementation of the same Protocol.

Maturity: `[MVP]`.
