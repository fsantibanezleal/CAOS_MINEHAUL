# ADR-0004 — Package name: `minehaulsim`

- Status: Accepted · Date: 2026-07-02 (recorded at U12; decision made at U1)

## Context

The package needed a PyPI-available, descriptive, durable name. Candidates were screened for
PyPI availability, descriptiveness (a stranger should guess the domain from the name), and
neutrality (no product branding that would age badly).

## Decision

`minehaulsim` — mine + haulage + simulation. Repo `CAOS_MINEHAUL`; import name, distribution
name and CLI entry point all `minehaulsim`.

## Consequences

- One name across pip / import / CLI (no alias drift).
- The CAOS repo prefix stays a repo-hosting convention only; nothing in the published package
  depends on it.
