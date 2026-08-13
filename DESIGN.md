# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-14
- Primary product surfaces: authenticated workspace, with the knowledge workbench as the current visual reference surface.
- Evidence reviewed: `frontend/src/styles/globals.css`, workspace shell components, knowledge page/components/tests, and `/Users/sw-jooder/Downloads/知识库工作台-离线版.html`.

## Brand
- Personality: calm, operational, precise, and technically credible.
- Trust signals: explicit service state, honest processing state, durable actions, and visible recovery paths.
- Avoid: marketing composition, decorative gradients, nested card stacks, oversized type, fake operational metrics, and hidden failure states.

## Product goals
- Goals: make repeated knowledge operations fast to scan; keep document location, ingestion state, and available actions visible together; present a complete cross-document graph overview and retrieval evidence directly from the configured LightRAG workspace.
- Non-goals: post-creation Scope administration, operator diagnostics, or fabricated graph/retrieval results.
- Success signals: users can locate a document, understand its state, move/retry it, and identify the next available action without changing screens.

## Personas and jobs
- Primary personas: operators and domain specialists managing an organization-wide knowledge corpus.
- User jobs: upload and organize documents, monitor ingestion, diagnose failures, scan the global graph, locate entities, inspect relationships and source documents, and prepare retrieval queries.
- Key contexts of use: repeated desktop operations with occasional mobile inspection.

## Information architecture
- Primary navigation: workspace sidebar, then page-local Documents / Knowledge graph / Retrieval test tabs.
- Core routes/screens: `/workspace/knowledge` singleton Scope creation or the document workbench.
- Content hierarchy: page identity and data-plane state; Scope summary; mode tabs; active work surface; graph groups/search/global canvas or document context; contextual actions and diagnostics.

## Design principles
- Keep context and action adjacent: directory selection, document selection, and document state share one workbench.
- Show real state only: derive progress and graph/retrieval evidence from accepted backend responses without illustrative fallback data.
- Optimize for scanning: restrained color, compact rows, tabular numerals, short labels, and stable panel dimensions.
- Global before local: the graph opens without a center entity, preserves every document component, and uses group focus, entity search, or node selection for drill-down without replacing the global dataset.
- Tradeoffs: visual fidelity to the supplied reference yields to accessibility, responsive behavior, and existing API/test contracts.

## Visual language
- Color: warm neutral application background; white work surfaces; near-black primary controls; green/amber/red only for state.
- Typography: system sans; compact 12-15px operational copy; 24-28px page title; tabular/monospace numerals for metrics and identifiers.
- Spacing/layout rhythm: 4px base; 12-16px compact controls; 20-24px section separation; desktop two-column document workbench.
- Shape/radius/elevation: 8-12px radius; 1px warm-neutral borders; minimal shadow reserved for overlays.
- Motion: 150-220ms opacity/translate or height reveals tied to tabs, selection, and contextual panels; honor reduced motion.
- Imagery/iconography: Lucide icons with text labels for domain actions; no decorative illustration.

## Components
- Existing components to reuse: workspace shell, Shadcn Button/Input/Tabs/Alert, knowledge queries/mutations, and directory tree.
- New/changed components: compact knowledge header, summary strip, upload toolbar, directory pane, document status metrics, responsive document detail, retrieval mode selector, global Canvas graph, top-20 graph groups, and entity search.
- Variants and states: loading, empty, error, selected, processing, ready, failed, retry-wait, and disabled.
- Token/component ownership: reuse global semantic tokens; knowledge-specific layout stays inside `frontend/src/app/workspace/knowledge`.

## Accessibility
- Target standard: WCAG 2.1 AA for core workflows.
- Keyboard/focus behavior: visible focus, labeled icon buttons, keyboard-operable tabs/tree/actions, and focus restoration after dialogs or inline edits.
- Contrast/readability: status always includes text and shape, never color alone.
- Screen-reader semantics: meaningful regions, status/live announcements, a labeled Canvas summary, keyboard-operable search/group/reset controls, selected-node details, labeled graph/retrieval controls, and preserved form labels.
- Reduced motion and sensory considerations: disable nonessential transitions, pulsing, and graph simulation movement when `prefers-reduced-motion` is set.

## Responsive behavior
- Supported breakpoints/devices: desktop from 1024px, tablet from 768px, mobile down to 320px.
- Layout adaptations: two-column document workbench becomes stacked; toolbar controls wrap; graph details become an in-flow panel; horizontal tabs remain scrollable.
- Touch/hover differences: essential actions cannot depend on hover; touch targets remain at least 36px where space is constrained and 44px for primary actions.

## Interaction states
- Loading: stable panel skeleton or inline spinner without resizing the surrounding workbench; global graph loading reports that multiple components may be merged.
- Empty: one clear explanation and the next available action.
- Error: preserve surrounding context and provide a scoped retry.
- Success: concise confirmation without blocking continued work.
- Disabled: state reason remains visible next to the unavailable action.
- Offline/slow network: retain last successful document data while refresh/retry state is visible.

## Content voice
- Tone: direct, operational, and specific.
- Terminology: Scope is a singleton organization knowledge space; directory, document, ingestion, graph, group, entity search, and retrieval keep distinct meanings; do not expose a required "center entity" control.
- Microcopy rules: describe current state and next action; avoid implementation diagnostics and unsupported promises.

## Implementation constraints
- Framework/styling system: Next.js App Router, React, Tailwind CSS, Shadcn UI, Lucide icons.
- Design-token constraints: use semantic theme variables; do not introduce a second token system.
- Performance constraints: no new dependencies; the Canvas graph supports up to 5,000 logical nodes, uses an O(V+E) deterministic layout rather than pairwise force calculations, draws labels by zoom/priority, batches redraws through animation frames, performs no per-node DOM rendering, and cleans up observers/listeners.
- Compatibility constraints: preserve existing knowledge API contracts, singleton Scope behavior, polling/idempotency guarantees, and E2E testids.
- Test/screenshot expectations: frontend check plus focused knowledge E2E; screenshot comparison at 1440x960 and a 390px mobile viewport; visual-verdict target is 90+.

## Open questions
- [x] Connect graph and retrieval surfaces to Gateway endpoints and render normalized LightRAG evidence without fabricated answers.
- [x] Replace center-entity exploration with a global cross-document Canvas graph, top-20 group focus, and entity search.
