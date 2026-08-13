# n8n vs Dify: DeerFlow-centered comparison

Research snapshot: 2026-07-14 (Asia/Shanghai). Sources are limited to official documentation, official repositories, official release metadata, and product license text. Dify's latest GitHub release at the time of review is `1.15.0`, published 2026-06-25. n8n evidence reuses and cross-checks the official-source study in [`n8n-integration-primary-sources.md`](./n8n-integration-primary-sources.md).

## Executive conclusion

1. **n8n and Dify are not direct substitutes.** n8n is primarily a business automation and integration runtime. Dify is primarily an LLM application platform with AI Workflow/Chatflow, model management, RAG, agents, publishing APIs, and LLMOps.
2. **For the current DeerFlow architecture, n8n is the stronger complement.** DeerFlow already owns conversation, agent runtime, tools, models, memory, and an emerging knowledge plane. n8n adds deterministic workflows, connectors, triggers, and business-system orchestration with less product overlap.
3. **Dify is a credible DeerFlow replacement candidate, not a natural third runtime.** It overlaps with DeerFlow on chat applications, agent execution, model configuration, knowledge management, workflow APIs, end-user identity, and observability. Running DeerFlow + Dify + n8n creates three execution/control surfaces unless Dify has a narrowly bounded role.
4. **Dify is better when the primary requirement is low-code AI application delivery.** It offers a more productized model/knowledge/prompt/chatflow experience than n8n and can publish chat or workflow apps with streaming APIs and web UIs.
5. **Neither product should become the canonical cross-system control plane.** Identity, tenant policy, global model policy, knowledge ACL, action catalog, secret references, and cross-system audit should remain in the separate middle/control layer discussed in the architecture report.
6. **Licensing is material for a ToC platform.** Dify's modified Apache license requires a commercial license for unauthorized multi-tenant operation and restricts removal/modification of Dify frontend branding. n8n's Sustainable Use License restricts paid white-label/hosted offerings whose value substantially derives from n8n. Product/legal review is required before exposing either platform as customer-facing infrastructure.

## Product center of gravity

| Dimension | n8n | Dify |
| --- | --- | --- |
| Primary role | General workflow automation and system integration | LLM application development and delivery |
| Native execution unit | Workflow composed of triggers, application nodes, code, branching, waits, and sub-workflows | AI Workflow, Chatflow, Agent, Chatbot, or text-generation app |
| Conversation state | Available through chat/AI nodes, but not the platform's primary data model | First-class Chatflow/app conversations, variables, messages, feedback, and end-user APIs |
| Model management | Model nodes reference n8n credentials; model choice is stored in workflow nodes | Workspace-level model providers, credentials, defaults by model type, custom models, and paid-plan key load balancing |
| Knowledge/RAG | Composable loaders, embeddings, retrievers, and vector-store nodes | Productized knowledge bases, document/chunk management, metadata, retrieval testing, knowledge pipelines, and external knowledge APIs |
| Business connectors | Core strength; broad built-in/community node ecosystem and credential model | Plugins/tools/data sources/triggers exist, but AI-app composition remains the center |
| App publishing | Webhooks, forms/chat triggers, APIs, and workflow endpoints | Built-in chat/workflow WebApps plus chat/completion/workflow service APIs |
| LLMOps | Execution logs and metrics; AI usage is not the sole focus | App logs, token usage, annotations/feedback, prompt/model iteration, and external observability integrations |

Primary sources: [n8n integrations](https://docs.n8n.io/integrations/), [n8n Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/), [Dify README](https://github.com/langgenius/dify), [Dify Workflow & Chatflow](https://docs.dify.ai/en/self-host/use-dify/build/workflow-chatflow), [Dify API index](https://docs.dify.ai/llms.txt).

## Advantages and disadvantages

### n8n advantages

- **Better deterministic business automation.** Its main runtime is designed around application triggers, credentials, item processing, branching, retries, waits, sub-workflows, webhooks, schedules, and worker execution.
- **Stronger system-integration fit.** It is the more natural owner for CRM, ERP, ticketing, messaging, email, database, payment, and operations workflows.
- **Cleaner complement to DeerFlow.** DeerFlow can call selected n8n workflows through authenticated production Webhooks or MCP without giving up its own conversation and agent runtime.
- **Mature horizontal execution model.** Queue mode has a documented main/Redis/worker/database contract and separately scalable webhook processors.
- **Useful administration and agent interfaces.** Public REST API manages resources; instance-level and workflow-scoped MCP can expose approved workflows to agents.
- **Source-control/environment support exists.** Business/Enterprise plans can promote workflow definitions and related stubs across environments, although the official docs describe important conflict and secret-value limitations.

Sources: [queue mode](https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode/), [public API](https://docs.n8n.io/connect/), [instance MCP](https://docs.n8n.io/connect/connect-to-n8n-mcp-server/), [source control/environments](https://docs.n8n.io/administer/use-source-control-and-environments/).

### n8n disadvantages

- **AI governance is fragmented across nodes and credentials.** It is not a canonical model catalog, model gateway, or knowledge-management product.
- **Knowledge is compositional rather than productized.** Teams must design loaders, chunking, embeddings, metadata, retrieval, deletion, and tenancy contracts themselves.
- **End-user AI product UX is weaker than Dify.** n8n can expose webhooks, forms, or chat-triggered workflows, but does not center the product on publishing managed LLM apps.
- **Community authorization is limited.** Projects/RBAC are unavailable in Community; scoped API keys, custom roles, external secrets, and several governance features are plan-gated.
- **Credentials remain n8n-owned.** Its encryption key and credential tables cannot safely become the shared platform secret manager.
- **License limits some customer-facing models.** Hosted/white-label use and collecting customer credentials for an n8n-backed product may require commercial terms.

Sources: [Community feature exclusions](https://docs.n8n.io/deploy/host-n8n/community-edition-features/), [RBAC](https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/), [external secrets](https://docs.n8n.io/administer/manage-credentials/use-external-secret-stores/), [Sustainable Use License](https://docs.n8n.io/privacy-and-security/sustainable-use-license/).

### Dify advantages

- **Complete AI application lifecycle.** Dify combines AI workflows, Chatflows, agents, prompt tooling, model management, RAG, APIs, WebApps, and LLMOps in one product.
- **Stronger model-management experience.** Models and provider credentials are workspace-shared; admins can define defaults for reasoning, embedding, rerank, speech-to-text, and text-to-speech. Professional/Team plans add credential load balancing.
- **First-class knowledge platform.** It supports multiple knowledge bases, ingestion, documents/chunks, metadata, retrieval testing, embedding/retrieval settings, knowledge pipelines, and a broad Knowledge API.
- **Can use an external knowledge service.** Dify documents a retrieval API contract that lets the platform query externally owned RAG systems without taking over their content.
- **AI Workflow/Chatflow is now substantial.** Current docs include trigger-based Workflow, conversational Chatflow, conditions, loops, iterations, code, tools, knowledge retrieval, Agent nodes, Human Input pauses, streaming runs, cancellation, and published-version execution.
- **Fast ToC/ToB AI app delivery.** Chat and workflow apps can be published as web applications and service APIs, reducing frontend and API work for conventional assistants and RAG apps.
- **Extensible plugin system.** Official plugin types include model providers, tools, agent strategies, data sources, triggers, and endpoint extensions.

Sources: [Dify README](https://github.com/langgenius/dify), [model providers](https://docs.dify.ai/en/self-host/use-dify/workspace/model-providers), [knowledge](https://docs.dify.ai/en/self-host/use-dify/knowledge/readme), [external knowledge](https://docs.dify.ai/en/self-host/use-dify/knowledge/connect-external-knowledge-base), [Workflow API](https://docs.dify.ai/en/api-reference/guides/workflow), [plugin types](https://docs.dify.ai/en/develop-plugin/getting-started/choose-plugin-type).

### Dify disadvantages

- **Heavy overlap with DeerFlow.** Both would own chat state, agent definitions, model selection, tools/plugins, knowledge integration, workflow/run state, publishing APIs, and user-facing AI application behavior.
- **Less natural as a general business-automation backbone.** Dify's Workflow is explicitly centered on models, tools, knowledge, and AI app reliability. Its plugin trigger and integration ecosystem does not remove the need for a connector-centric automation platform in integration-heavy environments.
- **More operational components.** The official Compose topology includes API, WebSocket API, workers, beat scheduler, web, PostgreSQL/MySQL, Redis, sandbox, plugin daemon, agent backend, SSRF proxy, Nginx, and a selectable vector/search backend. A minimal deployment still has more moving parts than a simple single-node n8n installation.
- **Workspace-level model keys can be too broad.** Official docs state connected model keys are shared across the workspace and managed by owner/admin. A cross-tenant platform still needs an external policy and secret boundary.
- **Built-in RBAC is coarse in the base product.** Self-hosted docs list Owner, Admin, Editor, and Normal roles; custom granular roles are Enterprise.
- **Multi-tenant licensing is explicit.** The repository license says operating a multi-tenant environment, where a tenant is a workspace, requires written authorization/commercial licensing. Using the Dify frontend also preserves its logo/copyright unless separately licensed.
- **Using Dify only for models/knowledge carries a full runtime.** If DeerFlow remains the actual agent/chat engine, Dify becomes a second AI control and execution plane whose app/workflow state must be synchronized or intentionally ignored.

Sources: [Docker Compose](https://github.com/langgenius/dify/blob/main/docker/docker-compose.yaml), [member roles](https://docs.dify.ai/en/self-host/use-dify/workspace/team-members-management), [Dify license](https://github.com/langgenius/dify/blob/main/LICENSE).

## Current workflow capability comparison

The older shorthand "n8n has workflows; Dify only has simple AI chains" is no longer accurate.

| Capability | n8n | Dify 1.15-era docs |
| --- | --- | --- |
| Webhook trigger | Yes | Yes |
| Schedule trigger | Yes | Yes for Workflow |
| Integration/plugin trigger | Broad node ecosystem | Yes through trigger plugins/integrations |
| Human input / pause | Forms, waits, chat and approval patterns | First-class Human Input node and resume API |
| Streaming execution | Webhook/chat/MCP paths depending on node | Workflow/Chatflow SSE and resumable run stream |
| Published version execution | Active workflow and deployment/source-control model | Run current published workflow or a specific published `workflow_id` |
| Conversation-native flow | Secondary | First-class Chatflow |
| LLM/knowledge nodes | Available | Core product primitives |
| Business SaaS connectors | Core strength | Plugin/tool ecosystem, generally less central |
| Cross-environment Git promotion | Business/Enterprise feature | DSL/version workflows exist; no equivalent official Git environment system was established in this review |

Sources: [Dify Workflow & Chatflow](https://docs.dify.ai/en/self-host/use-dify/build/workflow-chatflow), [Dify Workflow API](https://docs.dify.ai/en/api-reference/guides/workflow), [n8n workflow docs](https://docs.n8n.io/workflows/), [n8n Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/).

## Fit with DeerFlow

### DeerFlow + n8n

This is the recommended combination for the current direction.

- DeerFlow owns ToC login experience, conversation, thread state, advanced agent behavior, sandbox, skills, memory, and tool use.
- n8n owns deterministic workflows, connector credentials, business triggers, retries, schedules, and integration execution.
- The independent control plane owns identity/tenant policy, logical model policy, knowledge ACL, approved action catalog, secret references, and audit correlation.
- DeerFlow calls a logical action through the control plane, which maps it to an authenticated n8n Webhook or allow-listed MCP workflow.
- n8n calls DeerFlow through its Gateway run APIs for steps that genuinely require an Agent.

### DeerFlow + Dify

Use only with a sharply bounded reason.

Reasonable narrow roles:

- Dify is temporarily used as an external knowledge/RAG backend exposed through its official Knowledge API.
- A separate business team publishes conventional Dify chat/RAG apps that do not share runtime state with DeerFlow.
- Dify is evaluated as a migration/replacement target for simpler AI applications.

Poor roles:

- Dify as the canonical model manager while DeerFlow independently constructs and invokes models.
- Dify Chatflow wrapping DeerFlow chat, producing two conversation histories and two run state machines.
- Synchronizing Dify apps, DeerFlow agents, and n8n workflows as if they were one object type.

### Dify + n8n instead of DeerFlow

This can be attractive when the main goal is low-code delivery rather than a deeply customized super-agent runtime.

- Dify owns AI apps, model providers, knowledge, prompts, Chatflows, and user-facing AI APIs.
- n8n owns enterprise connectors and business automation.
- A thin control plane still owns cross-product identity, policy, action catalog, and audit.

This alternative trades DeerFlow's code-level customization, sandbox/tool middleware, and agent-runtime control for faster low-code AI product delivery.

## Decision score for the current project

Scores are architectural fit estimates for the described DeerFlow-centered ToC platform, not vendor benchmarks.

| Criterion | Weight | n8n | Dify |
| --- | ---: | ---: | ---: |
| Complements DeerFlow without duplication | 25% | 5.0 | 2.0 |
| Business systems and connector workflows | 20% | 5.0 | 3.0 |
| Model and AI-app management | 15% | 2.5 | 5.0 |
| Knowledge/RAG product maturity | 15% | 3.0 | 5.0 |
| ToC AI app publishing | 10% | 2.5 | 5.0 |
| Operational simplicity in this architecture | 10% | 4.0 | 2.5 |
| Customer-facing license fit without negotiation | 5% | 2.5 | 2.0 |
| **Weighted result** | **100%** | **3.85** | **3.45** |

The result changes if DeerFlow is removed. In a greenfield low-code AI application platform, Dify's model, knowledge, Chatflow, and publishing scores become decisive.

## Recommendation

For the current plan:

1. Keep **DeerFlow** as the ToC conversation/agent runtime.
2. Choose **n8n** as the business workflow and connector engine.
3. Keep the **middle/control layer independent** and thin.
4. Do not introduce Dify as a third general-purpose runtime unless a POC proves a specific missing capability is cheaper to obtain through Dify than through the planned model gateway or knowledge service.
5. If the team values low-code AI app creation more than DeerFlow-specific agent/sandbox behavior, run a separate replacement evaluation: **Dify + n8n vs DeerFlow + n8n**.

## Verification gates before commitment

- Pin exact n8n and Dify versions and deployment editions.
- Confirm whether the product is internal, single-tenant, or customer-facing multi-tenant.
- Obtain legal/commercial confirmation for customer-facing workflow/app hosting and customer-supplied credentials.
- Build the same vertical slice in both products: ingest one document, classify a request with an LLM, retrieve evidence, request human approval, update a CRM/ticket system, and expose a production API.
- Measure build time, failed-run recovery, credential isolation, version promotion, audit completeness, operator usability, and infrastructure cost.
