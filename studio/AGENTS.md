# Studio Development Instructions

Confirmed product architecture (2026-09-12): the four primary spaces are Experiment, Agent, Market, Setting. Experiment owns experiment composition, runs, monitoring and analysis. Agent owns reusable Agent Groups and reserves behavioral-finance tests. Market owns mechanisms, asset packs, data synchronization and environment snapshots. Setting owns model/data-source credentials and extensible global defaults. Its current P0 is the real local simulation loop: configure a Run, start it, observe persisted RuntimeEvent progress, and inspect real results. Run controls and the Tushare settings, market synchronization, immutable snapshots and snapshot selection workflows are active. Other resource-management write workflows remain planned. Preserve existing simulations and the research landing page.

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

UI framework decision (2026-10-18): Studio uses Radix Themes (accent teal, gray slate) instead of Animal Island UI. Button roles: primary `solid` (at most one per area), secondary `soft` gray, stop/delete `soft` red, inline `ghost`; run controls use one tone each (2026-10-23): run `solid` teal, resume `solid` blue, stop `soft` red, completed-run results `soft` iris; selection states use `SegmentedControl`/`Tabs`, never a primary button. Experiment space has two levels only: a list (table/card toggle, user-selectable columns including created/updated time) and a per-run workspace that merges running and results into tabs. Creating an experiment is a standalone page with breadcrumb and a safe back path (cancel returns to the origin; unsaved edits prompt before leaving). Agent views in the Agent space and the run workspace share one `AgentExplorer` component with a side detail panel instead of stacked drawers.

Responsive preference: in portrait/mobile layouts, the primary space switcher is a fixed bottom navigation bar. Do not place the space-switching controls above the workspace content.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
