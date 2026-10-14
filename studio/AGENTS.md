# Studio Development Instructions

Confirmed product architecture (2026-09-12): the four primary spaces are Experiment, Agent, Market, Setting. Experiment owns experiment composition, runs, monitoring and analysis. Agent owns reusable Agent Groups and reserves behavioral-finance tests. Market owns mechanisms, asset packs, data synchronization and environment snapshots. Setting owns model/data-source credentials and extensible global defaults. Studio is an active frontend. Its current P0 is the real local simulation loop: configure a Run, start it, observe persisted RuntimeEvent progress, and inspect real results. The Run path may call the existing backend create/start/stop/resume APIs; all other resource-management workflows remain planned and must not mutate existing simulations or the research landing page.

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Responsive preference: in portrait/mobile layouts, the primary space switcher is a fixed bottom navigation bar. Do not place the space-switching controls above the workspace content.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
