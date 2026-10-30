# shadcn/ui ecosystem inventory — what is worth vendoring for a 116-column screener

Surveyed **2026-10-30**. Stars, push dates and licences come from `api.github.com/repos/<owner>/<repo>`;
framework constraints come from each repo's `package.json`; runtime React support comes from the
`peerDependencies` of the published npm package. Every number is reproducible from the URL beside it.
No claim below is from memory.

**Bottom line:** the list is a *discovery index*, not a quality signal — 23.9 % of its GitHub-linked
entries have not been pushed in 18 months and 22.4 % carry no licence at all. For this interface the
correct posture is **shadcn/ui's own components plus its own five npm dependencies**, and near-zero
third-party surface. Every dashboard "template" on the list is a liability, because each one ships a
design system and a primitive layer that we would then have to fight.

---

## 1. What `awesome-shadcn-ui` actually is

| Property | Value | Source |
|---|---|---|
| Repo | `birobirobiro/awesome-shadcn-ui` | https://github.com/birobirobiro/awesome-shadcn-ui |
| Stars / forks | 20,727 / 1,338 | `api.github.com/repos/birobirobiro/awesome-shadcn-ui` |
| Licence | MIT | same |
| Last push | 2026-10-29 | same |
| Open issues + PRs | 19 | same |
| Created | 2024-02-05 | same |
| Entries (data rows) | **548** | counted from `README.md` |
| Unique outbound links | 569 | issue #710 link-check report |
| README commits, 2026-09-19 → 2026-10-23 | 100 | `api.github.com/repos/.../commits?path=README.md&per_page=100` |

### 1.1 Composition

| Section | Rows | Share |
|---|---:|---:|
| Libs and Components | 219 | 40.0 % |
| Boilerplates / Templates | 71 | 13.0 % |
| Registries | 67 | 12.2 % |
| Platforms | 43 | 7.8 % |
| Animations | 42 | 7.7 % |
| Tools | 38 | 6.9 % |
| Ports | 21 | 3.8 % |
| Colors and Customizations | 19 | 3.5 % |
| Websites and Portfolios | 13 | 2.4 % |
| Plugins and Extensions | 10 | 1.8 % |
| Design System | 5 | 0.9 % |
| **Total** | **548** | |

The distribution is the first warning. 40 % of the list is undifferentiated component libraries, and
only 19 rows (3.5 %) are theming. There is no "data-dense application UI" section — the categories are
organised by *distribution mechanism* (registry, template, port), not by *problem solved*. A screener
team searching this list for "table" finds 9 table entries scattered across two sections and has to
rank them by hand.

### 1.2 How it is maintained

Maintenance is real but narrow. `CONTRIBUTING.md` states the five acceptance rules verbatim:

> "It is about shadcn/ui. … **It is free to use.** … **The link works** … **The description says what
> it is.** … **It isn't already listed.**"
> — https://raw.githubusercontent.com/birobirobiro/awesome-shadcn-ui/main/CONTRIBUTING.md

**Notice what is absent: there is no recency, popularity, or maintenance criterion.** A project that
was abandoned in 2023 is admissible as long as its URL returns 200. Three CI workflows run —
*Format README* (dedupe + link present), *Add Dates* (stamps the Date column on merge), *Link Check*
(weekly HTTP status). All three test **the row**, none test **the project**.

The same file settles what the Date column means, and it is not what a reader assumes:

> "**Don't write a Date cell.** … A workflow appends it when your change lands on `main`, and it only
> fills cells that are empty, so a date you write yourself survives the merge and permanently records
> the wrong day."

So **Date = date the row was added to the list**, not the project's last commit. The evidence is in the
file: **227 of 548 rows (41.4 %) carry the identical stamp `2024-12-27T12:56:05.000Z`** — a bulk
import. Two entries that share that stamp prove the column is meaningless in both directions:

| Entry | Date column says | Actual last push | Error |
|---|---|---|---|
| `sadmann7/shadcn-table` | 2024-12-27 | 2026-10-30 | looks 22 months stale, is alive |
| `danielagg/shadcn-data-table-advanced-col-opions` | 2024-12-27 | 2024-06-01 | looks current-ish, dead 28 months |

### 1.3 Are entries ever removed?

Rarely, and on the evidence, not at all. Across the last 100 README commits (2026-09-19 → 2026-10-23)
the only commit whose message mentions removal is `chore: remove shadcnblocks sponsor and resource`
(2026-10-04) — and it removed a **sponsor block, not a table row**: `shadcn-blocks-com` is still listed
at line 180 of the current README. **No table row was removed in that 34-day window.**

The policy keeps it that way — removal follows a *dead link*, never a dead project:

> "A weekly job checks every link in this list and opens an issue for the broken ones; entries that
> stay broken are removed."

The list is therefore **append-only with respect to abandonment**. Link Check currently reports
568/569 links resolving (issue #710, 1× 404) — a project can be a fossil and still pass.

### 1.4 Does listing imply quality? Measured, not asserted

I resolved every entry whose link points at a GitHub repo — **268 of 548**, 100 % API success — and
measured the population on 2026-10-30:

| Metric | Count | Share of 268 |
|---|---:|---:|
| Archived by owner | 5 | 1.9 % |
| **No licence detectable** (GitHub `license: null`) | **60** | **22.4 %** |
| **No push in ≥ 18 months** (before 2025-04-30) | **64** | **23.9 %** |
| Under 100 stars | 103 | 38.4 % |
| Under 20 stars | 38 | 14.2 % |
| Median stars | 194 | — |

Five archived projects are still listed: `nyxb-ui/ui` (838 ★), `mehrabmp/cut-it` (272 ★),
`itsjavi/glasscn-ui` (99 ★), `BramSuurdje/proxmox-helper-scripts` (69 ★), `Type-zero-labs/ouroboros-ui` (8 ★).

**Conclusion: listing is evidence of existence and a working URL, nothing more.** Roughly one in four
entries is stale and one in five is unlicensed — and an unlicensed repo is legally *all rights
reserved*, so it cannot be vendored at all, however good the code looks.

---

## 2. Shortlist — 11 candidates that matter for this interface

Selection rule: the resource must either (a) be a library we install, or (b) be a *pattern source* we
read and re-implement against our own tokens. Anything that would replace our design system was
excluded regardless of quality.

| # | Group | Resource | Repo | ★ | Last push | Licence | React | Next-only? | What it gives us that plain shadcn/ui does not |
|---:|---|---|---|---:|---|---|---|---|---|
| 1 | Data table (engine) | TanStack Table | `TanStack/table` | 28,538 | 2026-10-21 | MIT | peer `>=18` | **No** — framework-agnostic | The only thing that makes a 116-column catalogue tractable: `columnPinningFeature`, `manualSorting`, `manualPagination`, `manualFiltering`, column sizing/resizing — none of which shadcn's doc exposes. |
| 2 | Data table (pattern) | tablecn (`shadcn-table`) | `sadmann7/shadcn-table` | 6,367 | 2026-10-30 | MIT | 19.3 | **No** — monorepo ships *both* a Next 16 and a Vite 8 app | A worked server-side implementation: `manualSorting` + `manualFiltering` + `manualPagination` wired to a query layer, which shadcn's guide deliberately omits. |
| 3 | Data table (filters + detail) | data-table-filters | `openstatusHQ/data-table-filters` | 2,285 | 2026-10-02 | MIT | 19.2.3 | **Yes** — Next 16 (`apps/web/next.config.mjs`) | The filter-drawer + row-detail-sheet interaction model, with faceted filters, chips and URL state — the closest published analogue to our 74-filter drawer. |
| 4 | Virtualisation | TanStack Virtual | `TanStack/virtual` | 7,178 | 2026-10-29 | MIT | peer `16.8 – 19` | **No** — framework-agnostic | The one capability shadcn's data-table doc explicitly does *not* provide (§4), and the official companion TanStack names for row/column virtualisation. |
| 5 | Dashboard / blocks | shadcn-admin | `satnaing/shadcn-admin` | 16,323 | 2026-09-10 (default-branch commit 2026-06-11) | MIT | 19.2.5 | **No** — Vite 8 + React 19 (`package.json`) | A *structure* reference we can read in an hour: collapsible sidebar, command palette, theme switch, `@tanstack/react-table` list pages — on Vite, so it transplants without a Next runtime. |
| 6 | Dashboard / blocks | next-shadcn-dashboard-starter | `Kiranism/next-shadcn-dashboard-starter` | 7,203 | 2026-10-30 | MIT | 19.3 | **Yes** — Next 16.4 | Composes `react-resizable-panels` + `cmdk` + `recharts` + `@tanstack/react-table` v9 in one `package.json` — a working dependency-compatibility proof for exactly our stack, which is worth more than its pages. |
| 7 | Charts | Recharts | `recharts/recharts` | 27,675 | 2026-10-30 | MIT | peer `16.8 – 19` | **No** — framework-agnostic | Nothing over shadcn — shadcn's `chart` component **is** a Recharts wrapper. Listed because it is the actual install target and the thing whose version we must pin. |
| 8 | Command palette | cmdk | `pacocoursey/cmdk` | 13,054 | 2025-10-29 (12 months, 80 open issues) | MIT | peer `18 / 19` | **No** — framework-agnostic | Again the real dependency behind shadcn's `command`; needed for a symbol jump across a 116-column, multi-thousand-ticker universe. **Feature-frozen at 12 months with 80 open issues — stable, but no upstream fixes are landing.** |
| 9 | Layout / resizable | react-resizable-panels | `bvaughn/react-resizable-panels` | 5,433 | 2026-10-30 | MIT | peer `18 / 19` | **No** — framework-agnostic | The dependency behind shadcn's `resizable`; gives us the table/detail-panel split and a collapsible filter rail with persisted sizes. |
| 10 | Filter / form controls | *(covered by #3)* | — | — | — | — | — | — | No list entry beats shadcn's own `Form` + `react-hook-form` for a 74-control numeric drawer; a Zod-schema form generator (e.g. `vantezzen/auto-form`, 3,494 ★, MIT) is the wrong shape for min/max pairs. |
| 11 | Theming | tweakcn | `jnsahaj/tweakcn` | 10,499 | 2026-09-03 | **Apache-2.0** | n/a — web tool | n/a | An OKLCH token editor that emits `globals.css` for our dark default; produces CSS, ships no runtime code, so it cannot fight our system. |

**Two structural observations from building this table.**

- Five of the eleven (#1, #4, #7, #8, #9) are not "shadcn ecosystem" projects at all — they are the
  npm dependencies shadcn's own docs tell you to install. The ecosystem's real contribution to a
  screener is **two pattern repos and two scaffolds to read**, not a library.
- **#10 is deliberately empty.** The list has 219 "Libs and Components" and not one is a
  range-filter control for numeric data. That is a gap, not an oversight on my part: the closest thing
  (`shadcn-number-scrubber`, 34 ★, no licence, last push 2025-01-02) is disqualified twice over.

---

## 3. Rejection list — 9 entries that look right and are not

All nine are on the list, all are described in terms that map onto our requirements, and all fail on
a fact I verified rather than inferred. "No licence" means GitHub's detector returns `null` **and** a
listing of the repo root shows no `LICENSE`/`COPYING`/`UNLICENSE` file — verified per repo via
`/repos/<owner>/<repo>/contents/`. Under the Berne Convention that defaults to all rights reserved:
we cannot copy a line of it.

| Entry | Listed as | ★ | Last push | Licence | Disqualifying fact |
|---|---|---:|---|---|---|
| `ardasisbot/linked-chart` | "Chart component linked with data-table" | 70 | 2025-05-05 | **none** | The single most on-point entry in the whole list — its README advertises "Interactive chart selection filters table data", i.e. exactly a breadth-strip-to-table brush. 17 months stale, 70 stars, **no licence file in the repo root**. Cannot be vendored. Its own README credits `zoom-chart-demo`, which is also dead (below). |
| `aryanvichare/stocks` | "Stock Picker with Next.js charts" | 340 | 2024-07-07 | **none** | The only equity-domain entry that looks like us. Dead 27 months, **no licence**, and it is an application, not a component — nothing to extract. |
| `danielagg/shadcn-data-table-advanced-col-opions` | "DataTable with column resizing" | 92 | 2024-06-01 | **none** | Directly on the pinned/resizable-column requirement. Dead 28 months, 92 stars, **no licence**. The repo name misspells "options", which is a fair proxy for its maintenance. |
| `drefahl/tanstack-ui-table` | "Customizable table with @tanstack/table and shadcn/ui" | 34 | 2025-02-23 | **none** | Exactly our stack, exactly our problem. 34 stars, dead 20 months, **no licence**. |
| `olliethedev/dnd-dashboard` | "Dashboard with drop-to-swap layouts using Next.js, shadcn/ui, and swapy" | 440 | 2024-09-03 | **none** | Looks like a dashboard scaffold; is a Next-only demo with a drag-swap gimmick. Dead 25 months, **no licence**, and `swapy` gives us nothing a screener needs. |
| `zenoncao/shadcn-drag-table` | "Table with drag-and-drop rows" | 89 | **2023-08-03** | **none** | Dead **39 months** — predates shadcn/ui's Tailwind v4 rewrite entirely, so its class names are from a different era of the library. **No licence.** |
| `shelwinsunga/zoom-chart-demo` | "Zoomable Charts with shadcn/ui" | 629 | 2024-07-14 | MIT | 629 stars makes it the most attractive chart entry on the list. It is a **demo** — repo name and structure — dead 27 months, and it is the upstream that `linked-chart` forked from. Licence is clean; the code is simply abandoned. |
| `supabase-shadcn-database-example` | "supabase + shadcn/ui datatable" | 43 | 2025-01-17 | MIT | A tutorial repo tying the table to Supabase's client. Dead 21 months, 43 stars, and architecturally useless to us — our data comes from FastAPI `POST /screens/select`, not a Supabase JS client. |
| `shadcn-ui-dashboard` | "Multipurpose and powerful admin dashboard template compatible with shadcn/ui" | — (site, no repo) | — | **paid** | Not a code problem but a procurement one: the site sells a **$79 one-time Pro** and **$199 one-time Premium** licence behind "Buy Now". The row does not say so, which violates the list's own rule — *"If your project has a paid tier, say so in the description rather than describing the paid catalog as if all of it were free."* |

Read together, these are not random failures. **Six of the nine closest matches to a financial
screener are unlicensed**, which is the specific hazard of this ecosystem: a component library is easy
to start and easy to abandon, and abandonment plus a missing `LICENSE` is the default end state.

---

## 4. Data-table verdict: the shadcn `data-table` pattern is a *tutorial*, not a component

**Question:** does shadcn/ui's `data-table` doc pattern already cover sticky headers, pinned columns,
virtualised rows and server-side sorting, or does it need a third-party add-on?

**Answer: it covers none of the four, and it is not supposed to.** The doc says so in its own
introduction, then delivers eight recipes and stops.

Source of truth: `apps/v4/content/docs/components/radix/data-table.mdx` in `shadcn-ui/ui`
(126,988 ★, MIT, pushed 2026-10-30) —
https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/v4/content/docs/components/radix/data-table.mdx

> "It doesn't make sense to combine all of these variations into a single component. … So instead of a
> data-table component, I thought it would be more helpful to provide a **guide** on how to build your
> own."

Its complete Table of Contents, verbatim: *Set up Table Features, Basic Table, Row Actions, Pagination,
Sorting, Filtering, Visibility, Row Selection, Reusable Components, RTL.*

That is ten sections. **Column pinning, virtualisation, and sticky headers are not among them.** A
case-insensitive grep of the 24,316-byte source returns **zero matches for `pinning`, `virtual` and
`sticky`**, and exactly **one** match for `manual` — a parenthetical link to TanStack's pagination docs
("implementing manual pagination") that the guide never acts on. The absence is total, not partial.

| Requirement | Covered by the doc? | Where it actually lives |
|---|---|---|
| **Sticky header** | **No.** Not in the TOC, zero occurrences of `sticky`. The shipped `Table` primitive is `<table class="w-full …">` inside `<div class="relative w-full overflow-x-auto">`, and `TableHeader` is `<thead class="[&_tr]:border-b">` — no `position: sticky`, no `top-`, no `z-`. | CSS you write. `TableHead` ships at `h-10` (40 px) and `TableCell` at `p-2` + `whitespace-nowrap` — both must be overridden to reach a 20–24 px row. |
| **Pinned column** | **No.** Zero occurrences of `pinning`. | **TanStack Table core.** `columnPinningFeature` from `tableFeatures()`; the guide documents `columnPinning.start` / `.end` as "logical pinning regions" and links three examples including **"Sticky Column Pinning"** — https://raw.githubusercontent.com/TanStack/table/main/docs/framework/react/guide/column-pinning.md |
| **Virtualised rows** | **No** — and the engine refuses to do it too. | **A separate package.** TanStack Table's own guide: *"The TanStack Table packages do not come with any virtualization APIs or features built in. Virtualization is a rendering strategy, not a table feature."* It then names the companion: *"Install and import the React virtualizer adapter from `@tanstack/react-virtual`."* — https://raw.githubusercontent.com/TanStack/table/main/docs/framework/react/guide/virtualization.md |
| **Server-side sorting** | **No.** The only occurrence of `manual` is a link to TanStack's pagination docs, never a configured option. The doc registers `createSortedRowModel()` and `createPaginatedRowModel()`, i.e. it sorts and paginates **whatever is in memory**. | **TanStack Table core**, via options the doc never mentions: `manualSorting: true`, `manualPagination: true`, `manualFiltering: true`. TanStack warns why this matters: *"If the server returns only a page or filtered subset, client-side sorting sorts only those loaded rows, not the full dataset."* — https://raw.githubusercontent.com/TanStack/table/main/docs/framework/react/guide/sorting.md |

**Verdict.** The doc's own `data-table-demo.tsx` is 324 lines of client-side table over a local array.
Following it literally for a 116-column screener produces a table that sorts the current page, cannot
freeze the symbol column, and grows a 40 px header — the opposite of the brief. It is a **teaching
artifact**: correct, well-written, and three features short.

**What that actually costs us.** Adopting the pattern means writing, ourselves: the sticky-header CSS,
the pinning column styles (TanStack gives state and order, not pixels), the virtualiser glue, and the
manual-* option wiring. That is **one third-party package (`@tanstack/react-virtual`) plus roughly
200–300 lines of our own code** — not an add-on library. There is no shadcn-registry component that
ships this correctly; if there were, it would be in the shortlist.

---

## 5. Licensing — everything we would vendor, checked one by one

shadcn/ui itself: `shadcn-ui/ui`, **MIT**, 126,988 ★, pushed 2026-10-30. MIT is copy-in: we own the
copy, and no upstream obligation survives except retaining the notice.

| Component / package | Licence | Verified from | Vendored how | Obligation |
|---|---|---|---|---|
| shadcn/ui `ui/*.tsx` (table, dialog, sheet, input, select, tabs, chart, command, resizable, form) | **MIT** | `api.github.com/repos/shadcn-ui/ui` → `license.spdx_id = MIT` | copy-in source | keep copyright notice |
| `@tanstack/react-table` 9.3.1 | **MIT** | `registry.npmjs.org/@tanstack/react-table/latest` → `license` | npm dep | notice only |
| `@tanstack/react-virtual` 3.14.24 | **MIT** | same registry query | npm dep | notice only |
| `recharts` 3.10.1 | **MIT** | same | npm dep | notice only |
| `cmdk` 1.1.1 | **MIT** | same | npm dep | notice only |
| `react-resizable-panels` 4.15.3 | **MIT** | same | npm dep | notice only |
| `sadmann7/shadcn-table` (pattern only) | **MIT** | `/repos/sadmann7/shadcn-table` | read, re-implement | none if re-implemented |
| `openstatusHQ/data-table-filters` (pattern only) | **MIT** | `/repos/openstatusHQ/data-table-filters/license` → MIT text | read, re-implement | keep notice if copied |
| `satnaing/shadcn-admin` (structure only) | **MIT** | `/repos/satnaing/shadcn-admin` | read | keep notice if copied |
| tweakcn (theme source) | **Apache-2.0** | `/repos/jnsahaj/tweakcn` | *output* is CSS we author | Apache-2.0 applies to the tool, **not** to the `globals.css` it emits — but do not copy its UI code without the NOTICE file |

**Do not vendor (no licence, all rights reserved):** `linked-chart`, `dnd-dashboard`,
`shadcn-data-table-advanced-col-opions`, `tanstack-ui-table`, `crypto-charts`, `shadcn-drag-table`,
`aryanvichare/stocks`. Seven repos, zero permission.

**The copy-in consequence for a 116-column product — this is the real cost, not the licence.** Because
shadcn is copy-paste, our `components/ui/*.tsx` are a **private fork with no upstream**. The CLI has no
upgrade path for them: `shadcn@latest` exposes `init, add, apply, preset, view, search, build, docs,
info, migrate, eject`, and `migrate` only does `cn`, `icons`, `base-color`, `radix`, `rtl` — mechanical
rewrites, never "replace this component with the current implementation".
(https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/v4/content/docs/(root)/cli.mdx)

Three practical consequences:

1. **Security fixes do not arrive.** A CVE in a Radix primitive reaches us only if we notice and
   re-vendor by hand. Our exposure here is small — a screener renders text in a table and does not
   accept uploaded HTML — but the two npm deps that *do* get patched automatically are `recharts` and
   `cmdk`, which is a reason to keep them as npm deps rather than copying their source.
2. **Every column we add is a bespoke renderer.** At 116 catalogue columns and 6 views, we will hold
   ~12 view definitions × 10 columns of `ColumnDef` — this is the largest single piece of frontend
   code in the product, and it is ours alone. Upstream will never fix a bug in it.
3. **Tailwind v4 / Base UI drift is unmanaged.** shadcn now ships Radix, Base UI *and* React Aria
   variants (`apps/v4/content/docs/components/{radix,base,aria}/`). Our vendored copy is pinned to
   whichever base we pick on day one; the ecosystem is already moving to Base UI (§6d). Budget a
   planned re-vendor, not a trickle of updates.

---

## 6. Adversarial checks

### (a) Three recommendations re-verified at commit level — one is slowing down

Push dates lie (a push to any branch counts). I pulled `commits?per_page=1` on the default branch of
each:

| Repo | `pushed_at` | Last default-branch commit | Verdict |
|---|---|---|---|
| `sadmann7/shadcn-table` | 2026-10-30 | 2026-10-30 | **Alive**, 33 open issues, commits today |
| `openstatusHQ/data-table-filters` | 2026-10-02 | 2026-10-02 | **Alive**, 10 open issues, weekly cadence |
| `satnaing/shadcn-admin` | 2026-09-10 | **2026-06-11** | **Slowing** — the 3-month gap is pushes to non-default branches; 25 open issues. Safe to *read* as a structure reference; do not depend on it as a maintained dependency. |
| `jnsahaj/tweakcn` | 2026-09-03 | 2026-09-03 | Alive, but 56 open issues — a tool, not a dependency. |

No shortlisted item is abandoned. One (`shadcn-admin`) is drifting and is used only as a pattern.

### (b) Copy-in means no upstream — stated in §5, and it is the dominant risk in this document

The mitigation is architectural, not procedural: **vendor the smallest possible surface** (shadcn's own
components), and take every capability that has an independent maintainer as an **npm dependency**
rather than a copy. That is exactly why the minimal stack in §7 is five packages and not a template.

### (c) Paid products wearing free clothes

Three from the list, one of them undisclosed:

| Entry | Reality | Evidence |
|---|---|---|
| `shadcn-ui-dashboard` | **$79 Pro / $199 Premium**, one-time, licensed per developer | `shadcnuidashboard.com` — pricing block renders `$79 one-time`, `$199 one-time`; FAQ: "The Pro license covers one developer. The Premium license covers up to 20 members. Each member gets their own sign-in, their own GitHub repository access and their own license keys" |
| `paceui` | Free tier + **$27** paid Basic/Pro | `paceui.com/pricing`; the list entry itself says "paid Basic and Pro licenses for the full catalog" |
| `shadcnblocks.com` | Premium registry — 1,429 blocks behind a paid plan; it was a README **sponsor** until 2026-10-04, i.e. the list has a commercial relationship with entries it ranks | list row: "**Premium** shadcn/ui registry with 1429 blocks…"; commit `chore: remove shadcnblocks sponsor and resource` (2026-10-04) |

Note the conflict of interest: the two README sponsors are `shadcnstudio.com` and `shadcnuikit.com`,
both commercial registries. Sponsorship is not disclosed per-row.

### (d) The template trap — found, and it is the recommended-looking one

**`shadcnstore/shadcn-dashboard-landing-template`** (1,244 ★, MIT, pushed 2026-10-28) is the trap. It
looks ideal: free, MIT, Vite + React 19, Tailwind v4, `@tanstack/react-table` 9, `recharts`, `cmdk`,
`react-resizable-panels`, `react-hook-form`, `zod`, and 38 dependencies total. Its `vite-version/package.json`
lists **`@base-ui/react`** and **no `@radix-ui/*` at all**.

Its README makes this explicit:

> "**2.0 is out.** The template now runs on **Base UI** instead of Radix UI, with every dependency on its
> latest release. Upgrading a customized copy? See the changelog."

That is a **different primitive layer** from the shadcn/ui we would vendor. Adopting it means either
(a) running two primitive libraries side by side, or (b) migrating our own components to Base UI on day
one, or (c) forking the template back to Radix and owning that fork. All three are worse than writing
a sidebar ourselves. It also ships a marketing landing page, an auth flow and a mail/chat/calendar app
suite — none of which a screener has, all of which we would delete.

Second, milder instance: **`marmelab/shadcn-admin-kit`** (1,139 ★, MIT, 2026-10-21, Vite) is also
Base UI, and its README declares "Admin Framework: **Ra-Core**". It is react-admin with shadcn
clothes: it brings a resource/data-provider abstraction, its own routing and CRUD lifecycle. Our backend
is a single `POST /screens/select` with a `columns` list and ETags; bending `ra-core` around that buys
nothing and costs a data-provider adapter plus a second mental model.

Third: **Tremor** (3,685 ★, Apache-2.0, pushed 2025-10-10). Its chart components are re-implementations
on top of Recharts — `src/components/AreaChart/AreaChart.tsx` imports `AreaChart as RechartsAreaChart`
from `recharts` and `@remixicon/react` for icons, and ships its own `AvailableChartColors` palette.
Adopting it means adopting the Remix Icon set and a second colour ramp on top of the `--chart-1…5`
tokens shadcn already defines. Two chart abstractions over one chart library, for zero capability.

**The pattern in all three:** a "dashboard template" is a *finished design*, and adopting a finished
design is how you inherit someone else's decisions. What we want from a template is *structure* —
which is why `shadcn-admin` (plain Vite, Radix, no Base UI, no admin framework) is the only one worth
reading, and even then only to read.

---

## 7. Minimal stack — 5 packages, each tied to a capability

The recommendation is deliberately not "a template plus a table library". It is **shadcn/ui's own
components, vendored, plus exactly the five npm packages its own documentation tells you to install**.
Four of the five are the documented dependency of a shadcn component we are copying anyway; the fifth
is the one genuine gap. All five are MIT, installable, and therefore **patched automatically** — which
is the whole point of keeping the surface this small in a copy-in architecture.

| # | Package | Version | Licence | Capability it buys | Why it and nothing else |
|---:|---|---|---|---|---|
| 1 | `@tanstack/react-table` | 9.3.1 | MIT | 116-column catalogue, six views, pinned symbol column, server-side sort/filter/paginate | It is the only engine that gives `columnPinningFeature` + `manualSorting`/`manualPagination` on the same table state, and it is already what shadcn's doc builds on — so the doc's recipes stay applicable. |
| 2 | `recharts` | 3.10.1 | MIT | Market breadth strip (advancing/declining) and per-symbol sparklines | shadcn's `chart` component *is* a Recharts wrapper over `--chart-1…5`; using anything else means either a second chart library or writing the wrapper ourselves. |
| 3 | `cmdk` | 1.1.1 | MIT | ⌘K symbol jump across thousands of tickers and 116 columns | It is the dependency behind shadcn's `command`; a screener's worst navigation problem is finding one symbol among thousands, and this is the cheapest fix in the stack. |
| 4 | `react-resizable-panels` | 4.15.3 | MIT | Table ↔ detail-panel split with persisted sizes, collapsible filter rail | It is the dependency behind shadcn's `resizable`; without it the detail panel is a fixed width we regret, with it the user owns the split. |
| 5 | `@tanstack/react-virtual` | 3.14.24 | MIT | Row (and column) virtualisation — **conditional** | The only capability shadcn's doc and TanStack Table both explicitly disclaim; at 100 rows × 10 columns we do not need it on day one, so adopt it when a view exceeds ~200 rows, not before. |

**Explicitly not adopted:** every dashboard template (design-system liability — §6d), every blocks
registry (67 entries, every one a second design system we would have to reconcile), `shadcn-admin-kit`
(`ra-core` architecture), Tremor (second chart abstraction), and `data-table-filters`/`shadcn-table` as
*dependencies* — they are read as patterns only, because both are Next.js applications and ours is Vite.

**What we still write ourselves, and should budget for:** sticky-header CSS, pinning column styles,
the 74-filter numeric drawer, and the 116-column `ColumnDef` catalogue. No package on this list does
those well, and the ones that look like they might are unlicensed (§3).

---

## 8. What I could not verify

- **Framework compatibility for `openstatusHQ/data-table-filters` and `satnaing/shadcn-admin` beyond
  `package.json`.** I confirmed build tooling (`next.config.mjs` vs `vite` + `@vitejs/plugin-react`) but
  did not attempt a port; "Vite-compatible" means "its dependencies and file conventions allow it", not
  "I ran it".
- **Worst-case performance at 12 × 100 with 116 definable columns.** No package publishes benchmarks;
  the virtualisation advice in §7 is derived from TanStack's own statement that it is a rendering
  strategy, not from a measurement I took.
- **`Linked chart` licence status could theoretically change** if the author adds a `LICENSE` after
  2026-10-30; the rejection is correct as of the scan.
- **Section counts and staleness percentages** are counts over `README.md` as fetched on 2026-10-30 and
  over 268 GitHub repos, all of which resolved. They will drift — the list took 100 README commits in
  34 days.

---

## 9. Provenance — the exact URL behind each class of claim

| Claim class | URL pattern used | Example |
|---|---|---|
| Stars, push date, archived, licence, default branch | `https://api.github.com/repos/<owner>/<repo>` | `https://api.github.com/repos/TanStack/table` |
| Last **commit** on default branch (§6a) | `https://api.github.com/repos/<owner>/<repo>/commits?per_page=1` | `https://api.github.com/repos/satnaing/shadcn-admin/commits?per_page=1` |
| Licence **text** where the detector was null | `https://api.github.com/repos/<owner>/<repo>/license` and `/contents/` | `https://api.github.com/repos/Semkoo/niko-table-registry/license` |
| Runtime React support | `https://registry.npmjs.org/<pkg>/latest` → `peerDependencies`, `license` | `https://registry.npmjs.org/@tanstack%2Freact-table/latest` |
| Framework constraint | `https://api.github.com/repos/<owner>/<repo>/contents/package.json` | `https://api.github.com/repos/openstatusHQ/data-table-filters/contents/apps/web/package.json` |
| List membership, section counts, Date column | `https://raw.githubusercontent.com/birobirobiro/awesome-shadcn-ui/main/README.md` | — |
| Acceptance rules, Date semantics, removal policy | `https://raw.githubusercontent.com/birobirobiro/awesome-shadcn-ui/main/CONTRIBUTING.md` | — |
| Link-check result (issue #710) | `https://api.github.com/repos/birobirobiro/awesome-shadcn-ui/issues/710` | — |
| shadcn data-table guide (TOC, missing features) | `https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/v4/content/docs/components/radix/data-table.mdx` | 24,316 bytes |
| shadcn `Table` primitive (no sticky, `h-10`, `p-2`) | `https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/v4/registry/new-york-v4/ui/table.tsx` | — |
| shadcn CLI commands (`migrate` has no component upgrade) | `https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/v4/content/docs/(root)/cli.mdx` | — |
| shadcn chart = Recharts wrapper | `.../content/docs/components/radix/chart.mdx` + `.../registry/new-york-v4/ui/chart.tsx` | — |
| shadcn command = cmdk; resizable = react-resizable-panels | `.../content/docs/components/radix/{command,resizable}.mdx` | `npm install cmdk` / `npm install react-resizable-panels` |
| TanStack column pinning / virtualisation / manual sorting | `https://raw.githubusercontent.com/TanStack/table/main/docs/framework/react/guide/{column-pinning,virtualization,sorting}.md` | — |
| Tremor = Recharts + Remix Icon + own palette | `https://raw.githubusercontent.com/tremorlabs/tremor/main/src/components/AreaChart/AreaChart.tsx` | — |
| shadcnstore template on Base UI, not Radix | `https://api.github.com/repos/shadcnstore/shadcn-dashboard-landing-template/contents/vite-version/package.json` | `@base-ui/react`, no `@radix-ui/*` |
| Paid tiers | `https://shadcnuidashboard.com`, `https://paceui.com/pricing` | `$79` / `$199` / `$27` |

The 268-repo staleness scan is reproducible: extract every `github.com/<owner>/<repo>` link from
`README.md`, hit `/repos/<owner>/<repo>` for each, and compare `pushed_at` against 2025-04-30 (18 months
before the scan date). All 268 requests returned 200 on 2026-10-30.
