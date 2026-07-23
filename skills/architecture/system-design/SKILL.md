# System Design
Produce a request-specific ARCHITECTURE.md, not a generic template.

Required sections (use these exact `##` headings): Objective, Scope, Assumptions,
Functional requirements, Nonfunctional requirements, Components, Data flows,
Security, Testing, Trade-offs, Open questions, Acceptance criteria.

Rules:
- Address the user's concrete domain entities, flows, and constraints.
- If must-cover topics or acceptance criteria are provided, cover each explicitly
  using the topic's wording (or clear synonyms such as tenant_id/RLS for
  "tenant isolation").
- Prefer concrete controls and failure modes over slogans.
- Include a mermaid data-flow diagram when components interact.
- Mark assumptions explicitly; list open questions that block implementation.
- Reject empty section stubs and boilerplate phrases like "MVP scope as requested".
- For multi-tenant designs, state isolation mechanism, shared vs dedicated
  resources, and cross-tenant failure modes in Security and Components.
