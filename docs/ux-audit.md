# UX audit of the web console

Method: ran the app against the live API (NVIDIA provider) and as the recorded demo under `/text2sql-guard/`, read every
screen at 1440 px and 390 px in both themes, then ran accessibility audits on each route. Each row is an issue found and
what was done about it.

## Console

| Issue found | Fix |
| --- | --- |
| The eight-stage trace came first and pushed the SQL and the result below the fold. | Order is now verdict, explanation (if refused), SQL, result, then the trace. |
| Four different outcomes (allowed, refused, empty, provider failure) shared two looks; a rate-limited model call looked like a database error. | Four distinct states with an icon, a heading and a colour each. Provider failures get their own panel, labelled "model or network problem, not a policy decision". |
| Rate limiting (HTTP or `llm.rate_limited`) was an opaque error with no way forward. | Live countdown from `retry_after_s`, a locked button that unlocks at zero, plain advice per failure kind (bad key, bad model, network, server). Not written to history. |
| A blocked result showed a rule id and a sentence, nothing about what to do. | "Explain this block": rule, plain words, which stage, what the guard saw, whether anything ran, what to try. |
| The stage text said "mock provider" even with a live model. | The generation stage names the live model when one is configured. |
| No starting points for a new role; the example list was fixed and assumed the mock model. | Question chips built from the role's visible columns (from `/policy/{role}`), a masked-column hint where it applies, and refusal probes. |
| Result table: no type alignment, long cells forced wide rows, masked columns were only coloured, row cap was invisible. | Numbers right-aligned, dates in tabular figures, cells over 48 characters truncated with a per-cell "more", a focusable "masked" tag with a tooltip, sticky header, a notice when the 1000-row cap is hit. |
| No way to take a result away or copy the SQL. | Copy SQL, CSV and JSON export (masked values, notice in the file, formula-injection guard). |
| Loading was a single grey block. | Result-shaped skeleton and an explanatory line; motion is dropped under reduced motion. |
| Trace rows were heavy; status was colour plus a tiny square. | One line per stage with a distinct glyph per state (check, cross, dash, dot), details in a native disclosure, the refusing stage opens itself, calm staggered entrance. |
| Verdict was announced only through a polite live region around the whole result. | A short status message ("Blocked at Input guard, rule ...") is announced; the result region is not re-read. |

## Everyday use (new)

* History (last 100, per role, search, re-run, delete, clear all with confirmation) and saved queries. Stored in
  localStorage: question or SQL, role, region, the SQL the service produced, decision. Never result rows. The page says so.
* The demo records to history too and can export the recorded rows, so the hosted demo shows the whole workflow.
* Settings: theme (system, light, dark, saved, applied before first paint), density, provider status with privacy note,
  read-only model list, how to configure, local data controls.

## Navigation and structure

| Issue found | Fix |
| --- | --- |
| Header mixed navigation, two status badges and no theme control; on a phone it wrapped into three rows. | Nav row scrolls horizontally on a phone, status cluster (mode, provider linking to Settings, theme) is compact. |
| Every route had the same document title. | Per-route titles; focus moves to the page heading on navigation. |
| Only the About page had a footer. | Every page has a `footer` landmark. |
| Audit filters were lost on reload and could not be shared. | Filters live in the URL (`?decision=block&role=analyst&q=...`). |
| Audit and Policy errors were bare text with no retry; empty states differed by page. | One error box with Try again, one empty state with a next action. |
| Policy page: invalid `dl` markup (found by the audit), skipped heading level. | Fixed. |

## Accessibility and quality

* Contrast of every text/background token pair was computed for both themes: all at least 4.6:1.
* Audits (axe-based, accesslint) on console, audit, policy, safety, about, history and settings report no violations after
  fixing the scrollable model list (not keyboard reachable) and the two Policy findings above. A transient contrast
  finding during the stage entrance animation was a mid-fade measurement and clears once settled.
* No hardcoded absolute asset paths: favicon uses `%BASE_URL%`, data and API URLs are built from `import.meta.env.BASE_URL`.

## Not done / needs the backend

* The model cannot be switched from the UI. It is read from the environment when the API starts, so the picker is a
  read-only list. Switching would need a per-request `model` field (or an admin endpoint) on `/query`.
* `/query` has no streaming, so the running state is a skeleton rather than live stage progress.
