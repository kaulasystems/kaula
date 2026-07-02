# kaula-sandbox-local

The reference `Sandbox` implementation: runs repair candidates and their
tests in a local Docker container with no network, no inherited environment
variables or credentials, memory/CPU/pid limits, a read-only filesystem, and
a hard timeout.

## Honest boundary — read this before production use

This sandbox is **single-tenant and not escape-hardened**. It is designed for
a team running their own agents on their own machines: it contains
accidental damage from a bad repair candidate, not a determined adversary in
a hostile multi-tenant environment. Escape-hardened, multi-tenant-safe
execution is the commercial `kaula-sandbox-hardened` implementation of the
same `kaula.core.Sandbox` Protocol.

Requirements: a local Docker daemon and a Python image
(default `python:3.11-slim`).

Maturity: `[MVP]`.
