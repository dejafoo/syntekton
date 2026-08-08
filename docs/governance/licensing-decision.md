# Licensing decision (open)

**Status:** decision pending — no repository `LICENSE` file until approved.  
**Owner:** product + legal  
**Date opened:** 2026-08-08 (SD7)

## Options under consideration

| Option | Implications |
| --- | --- |
| Permissive (MIT / Apache-2.0) | Broad reuse; weak copyleft; Apache adds patent grant and NOTICE handling. |
| Strong copyleft (GPL-3.0) | Downstream distributors must share corresponding source under GPL. |
| Weak copyleft (MPL-2.0 / LGPL) | File- or library-level sharing obligations; easier proprietary embedding than GPL. |
| Open-core | Core under an open license; proprietary connectors, hosted features, or packs remain closed. Requires a clear public/private boundary. |
| Commercial / source-available | Limits redistribution or production use; may conflict with community contribution norms. |

## Constraints recorded in SD7

- Do **not** add `LICENSE` / SPDX declarations to the repo root until this
  decision is signed off.
- Dependency license audit and SBOM procedures remain part of release
  engineering (see SD5 evidence and `SECURITY.md`).
- Third-party eval corpora (e.g. DeepSWE) may carry separate license terms;
  those do not set the Product Factory license by implication.

## Decision record

```text
Chosen license: <pending>
Rationale: <pending>
Approved by: <pending>
Effective commit: <pending>
Follow-ups: add LICENSE, NOTICE if needed, CONTRIBUTING clarification
```
