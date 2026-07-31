# Product Factory — Concept Description

**Audience:** product management, finance, and other non-technical stakeholders

## What it is

Product Factory is an orchestration layer for software development work. It
helps teams use multiple AI models, development tools, and repeatable workflows
together to take work from an early idea through research, planning,
implementation, testing, release preparation, and maintenance.

It is not intended to replace existing developer tools such as OpenCode.
Instead, it adds a coordinated “product factory” behind those tools. A developer
or product person can continue using their preferred interface while Product
Factory breaks a larger request into controlled tasks, selects appropriate
models and tools, records evidence, and shows what happened.

The central idea is to use smaller, locally hosted models where they are
effective, with carefully controlled use of frontier cloud models only when
needed. This could reduce cost, improve privacy, and give organizations more
control than relying entirely on one expensive external AI service.

## The problem it addresses

Most AI coding tools are optimized for a single developer interaction: “make
this change,” “explain this code,” or “fix this test.” Real product delivery is
broader and less linear:

- An idea may need domain research and feasibility analysis before it becomes a
  requirement.
- A technical plan should be grounded in the existing codebase and external
  constraints.
- Code changes need testing, independent review, and repair when validation
  fails.
- Release and operations work needs evidence, approval, auditability, and cost
  control.
- Organizations need to understand which model used which data, tools, and
  budget.

Today, teams often stitch these activities together manually across chat tools,
coding assistants, issue trackers, CI systems, and human review. Product
Factory aims to make that work repeatable, visible, and governed without
pretending that AI can replace expert responsibility.

## Intended users and value

| User | Potential value |
| --- | --- |
| Developers | Less repetitive investigation, planning, test triage, and documentation work; continued use of familiar CLIs. |
| Product managers | More structured feasibility research, assumptions, trade-offs, and decision records before development begins. |
| Engineering leaders | Visibility into work in progress, quality evidence, model cost, and where human review is still needed. |
| Security/compliance teams | Controlled tools, data boundaries, durable evidence, and fewer uncontrolled AI interactions. |
| Finance/operations | Ability to compare local-model infrastructure cost with cloud-model spending and measure cost per completed outcome. |

The valuable unit is not an individual model response. It is a completed,
inspectable outcome: for example, a feasibility dossier, technical plan,
validated code change, release-readiness packet, or incident handoff.

## High-level architecture

```text
People and existing development tools
(OpenCode first; other CLIs and interfaces later)
                         |
                         v
                 Product Factory
          workflow selection and coordination
                         |
     +-------------------+-------------------+
     |                   |                   |
     v                   v                   v
Workflows            Model routing       Governance
Research             Local models        Permissions
Planning             Cloud fallback      Budgets
Implementation       Cost tracking       Data policy
Testing              Quality checks      Approval rules
Release/operations
     |                   |                   |
     +-------------------+-------------------+
                         |
                         v
              Controlled task execution
     +-------------------+-------------------+
     |                   |                   |
     v                   v                   v
Code/workspace       Approved tools     External systems
repositories         tests, Git,        web research, CI,
and artifacts        validators         issue trackers, etc.
                         |
                         v
             Evidence, history, and dashboard
     task status, results, costs, sources, repairs,
     model usage, validation evidence, and handoffs
```

### How a request flows

A user might ask OpenCode: “Assess whether we should build a FHIR integration
façade.” Product Factory would then:

1. Choose a research and feasibility workflow.
2. Break the work into bounded tasks, such as source research, option
   comparison, architecture review, and evidence checking.
3. Use local models for suitable tasks and an approved cloud model only if the
   policy allows it.
4. Use only approved tools and sources—for example, public standards
   documentation, repository files, or a controlled test environment.
5. Produce an evidence-backed recommendation, including uncertainty and
   required human review.
6. Record the cost, model usage, sources, and outputs for later review.

The same pattern can later support a code change, where the workflow plans the
change, applies it in a confined workspace, runs registered tests, requests an
independent review, and creates a repair task if validation fails.

## Why the architecture matters commercially

The architecture is designed around three possible differentiators.

### Cost and deployment flexibility

Organizations can use local models for routine work while retaining access to
frontier models for harder tasks. This is especially attractive where
cloud-model cost, latency, privacy, or vendor dependence is a concern.

### Governed AI execution

The system does not simply give an AI agent unrestricted access to a shell,
repository, cloud account, or production environment. It controls tools,
budgets, data, and approvals. This is likely more valuable to organizations
than another general-purpose coding chatbot.

### Outcome-level observability

Teams can see the workflow, task status, evidence, repair loops, model choice,
and cost. That supports operational trust and makes it possible to improve the
system over time.

## Commercial potential

The strongest commercial opportunity is probably not selling “an AI coding
agent” by itself. That market is crowded and increasingly commoditized. The
stronger position is a governed orchestration and operating layer for
organizations that need to use AI-assisted development reliably across models,
tools, and environments.

Potential customers could include:

- Software organizations with meaningful AI coding spend.
- Regulated or security-conscious companies that cannot freely send source
  code or data to external models.
- Enterprises that want to run models on their own hardware or private cloud.
- Platform engineering teams that want consistent workflows, budgets,
  auditability, and integrations across multiple development tools.
- Consultancies or internal innovation teams building repeatable AI-assisted
  delivery practices.

Potential revenue models:

| Model | Commercial logic |
| --- | --- |
| Hosted control plane | Charge for orchestration, observability, policy, workflow execution, and team management while customers connect their own models and tools. |
| Enterprise self-hosted edition | Charge for secure private deployment, identity integration, audit retention, support, and regulated-environment features. |
| Managed remote execution | Operate model-serving and isolated execution infrastructure for customers that do not want to run it themselves. |
| Premium connectors and workflow packs | Offer maintained integrations for CI, source control, deployment, compliance, operations, and specialized domains. |
| Support and professional services | Help customers design workflows, evaluate models, integrate internal systems, and establish governance. |

The economic case must be proven with measurements, not assumed. The most
useful measures are:

- Cost per completed and accepted engineering outcome.
- Time from request to validated result.
- Human review and rework time.
- Local-model versus cloud-model usage and quality.
- Failure, repair, and escalation rates.
- Adoption by developers who could otherwise use a simpler AI tool.

## Open-source strategy

Open-sourcing can be strategically useful because trust, extensibility, and
self-hosting are central to the proposition. Organizations may be reluctant to
adopt an orchestration layer that can access repositories, tools, and model
endpoints if they cannot inspect or operate it themselves.

A practical approach would be:

| Area | Likely open-source candidate | Potential commercial offering |
| --- | --- | --- |
| Core runtime | Workflow engine, task model, tool broker, local execution, basic CLI adapters | Supported enterprise distribution and managed operation |
| Model support | Local-model adapters and routing interfaces | Managed model gateway, evaluated model profiles, cost optimization |
| Integrations | Basic public connectors and SDKs | Maintained enterprise connectors and integration support |
| Observability | Basic dashboard and run history | Team analytics, governance reporting, retention, compliance controls |
| Workflow packs | Reference packs and examples | Curated vertical packs, policy packs, enterprise customization |
| Enterprise controls | — | SSO, RBAC, audit exports, advanced policy, tenancy, secure remote runners |

This is an “open core plus managed and enterprise services” direction, but the
boundary needs care. If too much of the operationally valuable product is
closed, adoption may suffer. If everything valuable is open and easy to
self-host, monetization may rely mainly on support and infrastructure services.

## Open-source benefits and risks

### Benefits

- Builds trust for a system that handles code, tools, and model routing.
- Encourages integrations with CLIs, model servers, and developer platforms.
- Creates a contributor ecosystem around workflow packs, skills, and
  connectors.
- Makes self-hosting credible for privacy-sensitive customers.
- Can establish the project as a neutral layer rather than another proprietary
  coding assistant.

### Risks

- Large vendors or hosting providers could package the project with their own
  infrastructure.
- Community support and compatibility obligations can become expensive.
- The product may be seen as an engineering framework rather than a business
  product unless the hosted or enterprise value is clear.
- Local-model users may have limited willingness to pay for software alone.
- A poorly chosen license can either discourage adoption or make commercial
  differentiation difficult.

The defensible value is likely to come from trusted operation: enterprise
governance, evaluated workflows, secure integrations, remote execution,
model-cost optimization, support, and organizational learning—not from keeping
the basic orchestration concept secret.

## Recommended commercial hypothesis to test

The initial hypothesis worth testing is:

> Teams that want AI-assisted software delivery, but need more control than a
> standalone coding assistant provides, will pay for a system that orchestrates
> local and cloud models, connects safely to their development environment, and
> produces auditable, measurable outcomes.

The first proof should be a small number of real teams using a narrow
workflow—for example feasibility discovery, validated repository changes, or
release readiness—and comparing it with their normal AI-assisted process. If
it saves meaningful engineering time while preserving or improving quality and
governance, the broader platform opportunity becomes credible.
