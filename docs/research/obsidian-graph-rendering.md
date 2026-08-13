# Obsidian graph rendering: primary-source findings

Date: 2026-07-15

## Scope and evidence policy

This note answers a narrow question: what can Obsidian's graph experience teach us about a
Canvas-based global graph containing roughly 2,000-5,000 nodes?

Claims are separated into three evidence classes:

- **Official behavior**: documented by Obsidian Help or official developer documentation.
- **Verified implementation**: directly observable in the locally installed Obsidian desktop
  bundle, version 1.12.7 (bundle 0.14.8). This is version-specific and is not a supported API.
- **Inference**: an engineering conclusion derived from the first two classes. It is not an
  Obsidian claim.

Only primary sources were used. The official Help repository was inspected at commit
`b3d7ff02f49c07c32cc8ca5503dd5fc7862ea18e` (2026-07-14). The installed application was
inspected under `/Applications/Obsidian.app`; its extracted resources were used only to verify
runtime facts.

## Executive findings

1. **Official behavior:** Obsidian treats the global graph as an overview of the whole vault and
   the local graph as a depth-limited inspection tool around the active note. Search syntax,
   visibility toggles, and colored query groups reduce the visible problem before rendering.
2. **Verified implementation:** the inspected desktop build renders graph primitives into an
   HTML canvas through a bundled `pixi.js-legacy` asset requested as version 7.2.4. Its layout
   runs in a dedicated worker and uses a WebAssembly path when available, with a JavaScript
   force-simulation fallback.
3. **Verified implementation:** the renderer uses viewport checks, bounded progressive graphics
   creation, zoom-dependent label opacity, animation-frame scheduling, and idle-frame stopping.
   These are complementary controls: no single optimization explains the experience.
4. **Inference:** a 2,000-5,000-node energy-domain graph should adopt the rendering and workload
   separation principles, but should not copy the information architecture. A force-directed
   hairball is not an adequate operational view; clustering, typed edges, local drill-down, and
   domain filters must be first-class.

## 1. Officially documented functionality

### Global and local graph

**Official behavior.** The global Graph view visualizes relationships between notes: circles are
notes and lines are internal links. Node size grows with the number of inbound references.
Hovering highlights connections; clicking opens a note; panning and zooming are supported.

**Official behavior.** The global graph shows all notes in the vault. The local graph shows notes
connected to the active note and adds a configurable depth. Each depth level expands from nodes
revealed by the previous level.

Primary source: [Obsidian Help - Graph view][help-graph].

### Filtering, search, and groups

**Official behavior.** The graph filter accepts Obsidian search terms. Separate controls can show
or hide tags, attachments, unresolved links, and orphan notes. Files matching the application's
excluded-file patterns do not appear.

**Official behavior.** Graph groups are search queries paired with colors. Because they reuse the
Search grammar, groups can be based on filenames, paths, tags, properties, exact phrases,
boolean combinations, negation, and regular expressions.

Primary sources: [Graph view][help-graph] and [Search][help-search].

### Display and force controls

**Official behavior.** Users can toggle directional arrows and tune text fade, node size, link
thickness, center force, repel force, link force, and link distance. The global graph also offers
a chronological animation.

**Official behavior.** Obsidian exposes graph colors as themeable CSS variables, including text,
line, normal node, unresolved node, focused node, tag, attachment, and group colors.

Primary sources: [Graph view][help-graph] and
[Graph CSS variables][developer-graph-css].

### Data source

**Official behavior.** Obsidian maintains a local metadata cache that powers Graph view and other
features. IndexedDB preserves this cache between application sessions. This separates metadata
extraction/indexing from graph interaction.

Primary source: [How Obsidian stores data][help-storage].

## 2. Verifiable implementation facts in Obsidian 1.12.7

The following details are observations of the inspected build, not public API guarantees.

### Rendering technology

**Verified implementation.** `app.js` loads `/lib/pixi.min.js?7.2.4`. The bundled library
identifies the legacy Pixi build and includes both WebGL/WebGL2 and Canvas renderer paths. The
graph creates an HTML `canvas` and constructs:

```text
PIXI.Application({ view: canvas, antialias: true,
                   backgroundAlpha: 0, autoStart: false })
```

Graph nodes use `PIXI.Graphics` and `PIXI.Text`. Links use Pixi containers, a sprite based on the
white texture, and graphics for arrows. Therefore, describing this version as "SVG" is wrong,
and describing it as "WebGL only" is too strong: the shipped legacy bundle contains a Canvas
fallback path.

### Layout and worker boundary

**Verified implementation.** The graph creates `new Worker("/sim.js", { name: "Graph Worker" })`.
The worker owns force-layout state rather than running the simulation in the UI event loop.

**Verified implementation.** `sim.js` first attempts a WebAssembly simulation path and contains
the explicit fallback diagnostic `Using fallback d3 simulator`. The fallback implements the
usual force-layout ingredients visible in the bundle: centering, link distance/attraction,
many-body repulsion with a theta-controlled spatial approximation, and collision handling.

**Verified implementation.** Position results use `SharedArrayBuffer` when the environment allows
it and transferable `ArrayBuffer` messages otherwise. Worker updates are paced at approximately
one frame interval; the renderer consumes them through `requestAnimationFrame`.

### Rendering workload and level of detail

**Verified implementation.** The renderer checks graph primitives against viewport bounds before
drawing them. Text uses a larger culling region than the node glyph so labels can enter smoothly.

**Verified implementation.** Graphics are not all initialized in one unbounded pass. The renderer
selects a bounded set of not-yet-rendered nodes near the viewport and creates at most 50 in a
render cycle. This reduces frame spikes when a large graph first appears or the viewport moves.

**Verified implementation.** Label opacity is derived from logarithmic zoom and a configurable
text-fade multiplier. Labels therefore have a distinct zoom level of detail rather than behaving
like permanently visible DOM labels.

**Verified implementation.** Node radius is based on graph weight/degree and clamped. View scale
is also clamped (the inspected code uses a lower bound of `1/128` and an upper bound of `8`).

**Verified implementation.** Rendering is demand-driven. User input and worker results queue a
frame; after more than 60 idle frames, the renderer stops continuously scheduling work until a
new interaction or update reheats it.

### Incremental graph changes

**Verified implementation.** Simulation input is updated incrementally when nodes and links
change. New nodes are seeded near connected neighbors when possible, otherwise in a randomized
annulus, and the simulation is reheated through its alpha value. This avoids treating every graph
change as a visually unrelated cold start.

**Verified implementation.** The local graph is produced by filtering metadata-cache-derived
graph data around a root note using depth and link-direction options. It is not a separate
renderer technology.

## 3. Evidence boundary and limitations

- The desktop JavaScript is proprietary, minified, and version-specific. Class names and private
  method shapes are not stable integration points.
- The Pixi asset is evidence of Obsidian's implementation choice, not permission to copy
  Obsidian code and not a recommendation to depend on its private bundle.
- WebAssembly function names and detailed internal data structures are intentionally omitted
  where minification makes their semantic identity uncertain.
- `SharedArrayBuffer` is conditional in browsers because it normally requires cross-origin
  isolation. A production design needs a transferable-buffer fallback.
- Official Help documents product behavior, not performance limits. No official source found in
  this review promises a particular vault size, frame rate, or node-count ceiling.

For reproducibility, the inspected extracted files had these SHA-256 hashes:

| File | SHA-256 |
| --- | --- |
| `app.js` | `cc6d0c363cba3935207fb70e7d78736c9f0c2024545787871095288a1b185851` |
| `sim.js` | `549be2f69710af360d521c92b80c83718f673594d3dc2255a65e251199eee25d` |
| `lib/pixi.min.js` | `842814b22c348dcdab381ffb79da3763ff0672271f60f09dababa52656fd31b8` |
| `package.json` | `927286d1cfa49b97ea36ff59f95d4270ff7e06d017cb010350c9f2adc5142aab` |

## 4. Lessons for a 2,000-5,000-node Canvas global graph

### Adopt

The statements in this section are **inferences**, not documented Obsidian requirements.

1. **Use a retained Canvas/WebGL-capable renderer.** Thousands of individually managed DOM or SVG
   nodes and labels create avoidable style, layout, and event overhead. Keep semantic graph state
   separate from render objects.
2. **Move simulation off the main thread.** The main thread should own input, selection, panels,
   and compositing. A worker should own force iterations and return packed typed-array positions.
3. **Use a scalable repulsion algorithm.** Barnes-Hut/quadtree-style many-body approximation plus
   collision handling is a reasonable baseline; pair it with hard iteration, alpha, and time
   budgets.
4. **Make LOD explicit.** Cull offscreen primitives, create render objects in bounded batches,
   fade or suppress labels by zoom, and simplify hit testing when zoomed out.
5. **Stop work after convergence.** Re-render on simulation changes, camera changes, hover,
   selection, or data updates; do not maintain a permanent 60 Hz loop for a static graph.
6. **Separate graph indexing from rendering.** Build and cache normalized nodes, typed edges,
   degrees, clusters, source-document references, and search indexes before the viewport asks for
   them.
7. **Make local graph the operational default.** A selected entity with a 1-3-hop neighborhood is
   usually more useful than the full graph for inspection, diagnosis, and traceability.
8. **Treat search as graph visibility control.** Search should locate and reveal entities, while
   saved domain filters/groups should control which node and edge classes participate.

### Do not copy blindly

1. **Do not make force layout the information architecture.** It is a spatial index and discovery
   aid, not a substitute for hierarchy, ontology, or task-specific views.
2. **Do not collapse domain relations into generic lines.** Energy operations need visible edge
   semantics such as supplies, controls, depends-on, alarms-on, located-at, and derived-from.
3. **Do not expose all 5,000 nodes at equal visual priority.** Add cluster/aggregate nodes, document
   and asset filters, progressive disclosure, and a local drill-down path.
4. **Do not rely on color alone.** Pair color with shapes, edge styles, labels, legends, and a text
   detail view for accessibility and exact interpretation.
5. **Do not assume SharedArrayBuffer.** Design the protocol around typed arrays with both shared
   memory and transferable-buffer implementations.
6. **Do not couple business data to renderer objects.** A renderer replacement should not require
   rebuilding ingestion, search, selection, or domain filtering.

## 5. Recommended target shape

This is an **inference** for the DeerFlow/LightRAG knowledge graph, not a description of Obsidian.

```text
LightRAG/Gateway normalized graph
  -> graph index (IDs, typed edges, degree, cluster, source document)
  -> query/filter/local-neighborhood controller
  -> worker simulation (typed-array positions)
  -> Canvas/WebGL renderer (culling, batched creation, label LOD)
  -> accessible DOM side panel (selection, relation details, sources)
```

Suggested acceptance targets for the 2,000-5,000-node global mode:

- Initial interaction remains available while nodes are progressively materialized.
- Pan and zoom do not require React re-rendering per node or per simulation tick.
- Labels are bounded by screen-space density, not node count.
- A local 1-3-hop view opens from any selected node without rerunning full ingestion.
- Search can locate an offscreen node, move the camera to it, and preserve the active filters.
- Global mode exposes cluster and truncation status so absence is never mistaken for no data.
- Keyboard and screen-reader users can inspect the selected node and its relations in a DOM panel.

## Primary sources

- [Obsidian Help repository at the pinned commit][help-repo]
- [Graph view][help-graph]
- [Search][help-search]
- [How Obsidian stores data][help-storage]
- [Official developer documentation: Graph CSS variables][developer-graph-css]
- Local application resources from `/Applications/Obsidian.app`, version 1.12.7, bundle 0.14.8

[help-repo]: https://github.com/obsidianmd/obsidian-help/tree/b3d7ff02f49c07c32cc8ca5503dd5fc7862ea18e
[help-graph]: https://github.com/obsidianmd/obsidian-help/blob/b3d7ff02f49c07c32cc8ca5503dd5fc7862ea18e/en/Plugins/Graph%20view.md
[help-search]: https://github.com/obsidianmd/obsidian-help/blob/b3d7ff02f49c07c32cc8ca5503dd5fc7862ea18e/en/Plugins/Search.md
[help-storage]: https://github.com/obsidianmd/obsidian-help/blob/b3d7ff02f49c07c32cc8ca5503dd5fc7862ea18e/en/Files%20and%20folders/How%20Obsidian%20stores%20data.md
[developer-graph-css]: https://docs.obsidian.md/Reference/CSS+variables/Plugins/Graph
