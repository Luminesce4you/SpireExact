# SpireBoard local launcher and live dashboard

Start it with `powershell -NoProfile -File dashboard/start.ps1 -PythonExe python -Port 8765`
after `tools/setup_source.py` and `tools/prepare_local_entry.py` (see
[docs/BUILD.md](../docs/BUILD.md)). The launcher starts the backend and opens
http://127.0.0.1:8765/. Repeated launches reuse the backend of the same workspace
and refuse a port held by another service or another SpireBoard copy.
`-NoBrowser` starts the service without opening a browser. No administrator
privileges or execution-policy changes are needed. Direct server startup is
`python dashboard/server.py --port 8765`.

Click **新建求解** (top right), enter a seed and choose a time limit; 60 minutes
is preselected and 1–180 minutes are accepted. Submitting starts a real solver
job: IRONCLAD, A10, all unlocks, a fresh initial game. Jobs run in the snapshot
named by `solver-profile.json` (`experiments/frozen-i100r`, made by
`tools/prepare_local_entry.py`), independently of later source edits. The
settings are those of iteration-105 arm A (`configs/i054a-shm.toml`): seven
workers, focus scheduler with the online prior, `escalate` gate plans, 10k
ordinary-fight nodes, Server GC with a large gen0, pick/elo root policies,
cluster cap 2, final gate plan `open`, shared worker memory, CombatSolver
4d2c550, no plugins. User runs are registered in the local seed ledger.

The current-run controls are **暂停求解 / 继续求解** and **退出求解**. Exit
terminates the owned Job and keeps its evidence. Pause is offered only for a job
whose pause clock passed the compatibility check (gate preset `none` or
`escalate-evaluate` with a recorded zero-action pause-clock report); i100-r jobs
use `escalate` and therefore offer exit only. PID creation identities and a
named Windows Job prevent controlling an unrelated process.

Jobs wait on existing native-job/process handles when the P-core partition is
busy; queued time does not consume the solve budget. Work uses eight P-core
logical CPUs and a 14 GiB Job plus 2 GiB reserve, rather than inheriting the
dashboard's two E cores. Results live under the artifact root given to `tools/prepare_local_entry.py`.
The API accepts only validated seed/time fields and requires same-origin JSON
plus a per-service session token. It never accepts shell commands or arbitrary
workspaces from the browser.

Windows directory notifications trigger result updates over server-sent events;
one local sampler measures the owned process tree once per second. This does
not require repeated model polling. The service uses E cores 30–31 so it stays
outside the standard eight P-core solver partition. These sampled CPU/private
memory values are separate from authoritative Windows Job CPU/peak accounting.

Windows readers explicitly allow rename/delete sharing, so reading a snapshot
cannot prevent the solver's atomic replacement. Keep the bounded retry in the
solver too: external editors or indexers may still open files without that flag.

Live `result.json` may keep only the latest 64 records. `evaluations.jsonl`
retains the complete evaluation ledger; the dashboard reads it for historical
plots. A final normal solver report contains the complete record set again.

The **胜利轨迹** tab reads the complete winning candidate and its independent
replay. It has two modes; the choice is remembered per browser.

**跟打** (default) is for a person replaying the route in the real game:

- **开局设置** lists seed (with copy), character, ascension, mode, unlocks and
  custom modifiers.
- The outline groups the route by act and floor; each floor is split into combat
  turns or stages (event, rewards, shop, rest site, map move).
- Each step is one instruction with one-based positions, for example
  `打出 痛击+ → 树枝史莱姆（小） · 手牌左起第 3 张（共 3 张）`. The current step
  shows **此刻游戏里应是** (HP, block, energy or gold, the whole hand with the
  played card marked, enemies with the target marked, potions) and every step
  shows **之后应变为**, the recorded changes up to the next decision point. That
  boundary can include enemy actions and automatic resolution.
- One cursor tracks progress. `Space`/`Enter`/`→` advance, `←` goes back,
  `Shift+←/→` jump by turn or stage. Progress is stored per run in this browser's
  `localStorage` and resumes on return; a `step` in the URL takes precedence.
- **与游戏不一致…** records what differed at a step. Notes stay in the browser,
  can be copied as plain text, and are personal notes, never solver evidence.
- **专注模式** hides navigation and header so the window can sit beside the game
  (`Esc` exits). **证据** on a step opens the same step in the evidence view.

Limits of the follow-along text, all stated in the page where they apply:

- Positions are the recorded zero-based indices plus one. Whether the normal
  game screen shows hand, enemies and map nodes in that order is not certified.
- Card-selection steps name the cards from `observation.selection.cards`, the
  candidates in the order the recorded indices refer to. The native host writes
  this field since 2026-10-02 (`CampaignReplay.cs`, `Observe()`); it is a record
  only and is not read by the search. Pile screens also list every candidate,
  because the game may show a pile in a different order than the record.
- Evidence written by earlier hosts, which includes every frozen workspace up to
  `frozen-i054a` and all routes verified before that date, has no such field and
  is not rewritten. There, selections from the draw or discard pile (Headbutt,
  Liquid Memories, Droplet of Precognition) and generated-card choices made with
  an empty hand show only a position, and the step says so.
- Where a name is derived from the hand or deck before and after the choice
  (Armaments, generated cards, deck screens in older records), the step says so.
- The new field changes the recorded observation at `select_cards` steps, so
  evidence from a host with it and a host without it differs at those steps.
  A replay with `expected_evidence` must use the host that wrote the evidence.

**证据核查** is the evidence view. Every recorded decision is available through
pagination, floor/action filters, search, direct step selection and
previous/next navigation. Each step shows the chosen action, every recorded
legal option, hand, enemies, potions, deck, relics and raw evidence. The next
recorded observation is explicitly a boundary after any automatic resolutions,
not necessarily the immediate effect of one action. Native IDs and target
indices remain visible for manual checking.

Action titles, menu labels and state items use an offline Chinese name snapshot
from STS2 Wiki (`route_zh_data.js`, sources and retrieval date included). Refresh
it with `python tools/build_route_zh.py --refresh`. Unknown names retain their
IDs; raw actions, replay evidence and JSON exports are untouched. Search accepts
both Chinese names and native IDs.

The route selector lists only verified single-seed wins. Entering the route tab
from another run selects a verified win automatically. Full certificate/replay
checks must also pass before its route is displayed. The run table has
**验证通过** and **运行中** status filters, combined with protocol and search
filters; clicking a row opens that run and **胜利轨迹** in a row opens its route.
Tab changes update `view` in the URL and remove route step parameters when
leaving the route; refreshing restores the current tab.

The **关口模型** tab displays the saved `gate_models` snapshot from the selected
single-seed run. It shows entries versus fitted entries, paired samples, recorded
survival counts, best outcome, training residual and the saved coefficient tails.
Chinese names and feature search reuse the Wiki snapshot. Final bosses remain
separate gates. Coefficients are allocation signals; observed survival ratios
are not calibrated predictions. The growth chart only contains snapshots seen
since opening the page and resets on refresh. Synthetic probes stay separate.
No model is refitted by the dashboard; normal file events update the existing
SSE snapshots. Snapshot provenance and elapsed time remain inspectable.

Since i082 the tab also shows, when the snapshot has them:

- `gate_models.f2_joint_model` as an extra list item **两个首领联合** (real
  final-act F1 outcome + mean of full-HP synthetic F2 samples, separate life
  totals, 0–4 score). Its detail shows rows, sample groups, life totals,
  rejections, coefficients and `tier_sources`. When `replaces_gate` is set, the
  real gate it takes over is tagged and says its rows keep being collected and
  fitted but no longer supply tiers.
- `search.f2_readiness` (state counts, completed sample groups with per-sample
  wins, worker share against the stated 1/8 target, joint-focus and dead-retry
  counts) and `search.paired_card_probes` (third-act card arms against
  card_skip, merged as the snapshot's `merge` says: mean for i082, latest table
  for older runs, which then matches their tiers) in a panel below the gates.

All of these are synthetic allocation signals. The page says so where they
appear: UNKNOWN is not a loss, ten full-HP losses do not prove a deck cannot
win, full-HP F2 differs from the HP a real route carries, and none of it enters
real gate rows, trajectories, checkpoints, caches or win evidence. Runs without
these fields render as before. Checked in the local browser on
`i082-60-524130501` (light/dark, 1400 px and 390 px) and `i080-all45-524130501`;
the pre-change files are in `dashboard/runtime/web-backup-20261004-pre-i082-model`.

A sampled dead job with no final report is shown as **已停止（缺少结束报告）**
in the selected run and its experiment-table row. Its clock uses the last saved
search elapsed time. This presentation does not rewrite experiment artifacts or
turn an interrupted search into a completed experiment. Closed EventSources
cannot replace the currently selected run with a late snapshot.

The verified badge requires matching certificate, complete replay, native process
identity and replay isolation checks; a summary win flag alone is insufficient.
The evidence scope remains offline native DLL TestMode. Missing or inconsistent
replay evidence excludes a run from the route display; original records remain
on disk. Large integers such as RNG state are displayed as decimal strings
to avoid JavaScript precision loss; the complete JSON download preserves the
original numeric types and evidence without truncation.

Read-only API: `/api/winning-route?run=RUN_ID` returns all step summaries,
`&step=0` returns one complete zero-based step, and `&export=1` downloads the
complete evidence. Only indexed runs are accepted; arbitrary file paths are not.
Deep links use `/?run=RUN_ID&view=route&step=1` with a one-based step number.
`tools/check_dashboard_routes.py` checks all steps of the two retained real wins
through HTTP against their original files, plus full exports and served assets.

Backend checks cover local HTTP, SSE plumbing, concurrent file access and full
winning-route evidence. JavaScript checks are `node tests/dashboard_route_ui.cjs`,
`node tests/dashboard_navigation.cjs`, `node tests/dashboard_gate_model.cjs` and
`node tests/dashboard_guide.cjs` (follow-along outline, grouping, instructions,
expected changes, mismatch report); they alone do not establish browser
layout or interaction validation. The 2026-10-02 display update was additionally
checked in the local browser for Chinese names/search, verified filters and tab
restoration after refresh.

The 2026-10-02 redesign (light/dark theme, plain-language labels, launch dialog,
follow-along mode) changed only files in `dashboard/web`; the backend and API are
unchanged. It was checked in the local browser on every tab in both themes, at
wide and narrow widths, with the launch dialog opened and closed but never
submitted. Follow-along text was generated for every step of the 26 verified
routes without errors. No route was replayed in the real game as part of this
change. The previous frontend is kept in
`dashboard/runtime/web-backup-20261002-pre-redesign`.
