/**
 * The screener's masthead: one heading, one centred row of controls, one
 * filter disclosure, one bar of built-in column tabs.
 *
 *   heading + Columns / Order by / Signal / ticker     what you are looking at
 *   Filter (right)                                      which rows survive
 *   [the group panel, when open]                        one field per control
 *   built-in column tabs                                which columns are drawn
 *
 * Three different ideas live here and are kept apart on purpose:
 *
 * * **Columns** is the reader's own set — named, saved in this browser and
 *   managed in a dialog. It is not a tab.
 * * **The tabs** are the product's built-in column sets: eighteen of them,
 *   always mounted. Choosing one cannot clear a filter, because it writes
 *   `?view=` (or `?cols=`) and leaves `?f=` alone.
 * * **Filter** is the server's vocabulary: one labelled field per control,
 *   drawn from `GET /screens/filters` and nothing else.
 *
 * The whole band is one filled bar: `--accent-strong` with `--accent-fg` ink
 * (5.43:1 dark, 6.79:1 day), centred across the content width.
 */

import { ChevronDown, SlidersHorizontal } from "lucide-react";
import * as React from "react";

import {
  SORTS,
  SORT_LABELS,
  sortDirection,
  sortKey,
  sortLabel,
  type ViewDefinition,
} from "@/data/screens";
import type { ActiveFilter, FilterOp, WireFilter } from "@/filters/types";
import { columnSpec } from "@/grid/columnMeta";
import { useReducedMotion } from "@/grid/pending";
import { SymbolSearch } from "@/shell/SymbolSearch";
import { cn } from "@/lib/utils";

import { ColumnPresetsDialog } from "./ColumnPresetsDialog";
import {
  ALL_GROUPS,
  isFilterControl,
  optionFilter,
  optionKey,
  type FilterControl,
  type FilterGroup,
  type FiltersDocumentResult,
} from "./filtersDocument";
import { activePresetId, resolvePresets, type ResolvedPreset } from "./columnPresets";
import { DEFAULT_PRESET_ID, type UserPresetsApi } from "./userPresets";

/** The panel the Filter button controls; one id, named once. */
export const FILTER_PANEL_ID = "screener-filter-panel";

export interface ScreenerHeaderProps {
  /** The product's own name for this screen: "Stock screener" / "ETF screener". */
  title: string;
  /** The views the schema served; a built-in tab may be one of them. */
  views: readonly ViewDefinition[];
  /** Every column key the schema serves: the guard for a preset of its own. */
  catalogue: readonly string[];
  /** The view being drawn, and the columns it is drawing now. */
  view: string;
  columns: readonly string[];
  /** A built-in tab was chosen: the shell switches view or applies its columns. */
  onSelectPreset: (preset: ResolvedPreset) => void;
  /** The reader asked for their own columns (the Custom tab). */
  onOpenColumnPicker?: () => void;
  /** The reader's saved column sets, and the operations the dialog offers. */
  userPresets: UserPresetsApi;
  /** Apply one of the reader's saved sets to the screen. */
  onApplyColumns: (columns: readonly string[]) => void;
  /**
   * The ordering, as one wire token: `market_cap` (highest first) or
   * `-country` (lowest first). The header's own buttons and the select below
   * both write this one value through `onSortChange`.
   */
  sort: string;
  onSortChange: (sort: string) => void;
  signal: string | null;
  onSignalChange: (signal: string | null) => void;
  /** The one filter state; the panel only writes through these two. */
  filters: readonly ActiveFilter[];
  onAddFilter: (filter: WireFilter) => void;
  onClearFilters: () => void;
  /**
   * The served filter document and its load state.
   *
   * Injected rather than read here so the screen owns one subscription (and so
   * a test can hand the header a fixture without a network stub); the header
   * still draws nothing but what this object carries.
   */
  vocabulary: FiltersDocumentResult;
  /** A ticker search hit: the shell docks that symbol's detail panel. */
  onSelectSymbol: (symbol: string) => void;
  /** The all-columns destination, when the schema serves one. */
  wide?: {
    label: string;
    count: number;
    active: boolean;
    onToggle: () => void;
  } | null;
}

export function ScreenerHeader({
  title,
  views,
  catalogue,
  view,
  columns,
  onSelectPreset,
  onOpenColumnPicker,
  userPresets,
  onApplyColumns,
  sort,
  onSortChange,
  signal,
  onSignalChange,
  filters,
  onAddFilter,
  onClearFilters,
  vocabulary,
  onSelectSymbol,
  wide,
}: ScreenerHeaderProps) {
  const [open, setOpen] = React.useState(false);
  const [mounted, setMounted] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);
  // "All" is a view of the panel, not a sixth group: it shows every group's
  // controls at once. A single group narrows the panel to that group.
  const [scope, setScope] = React.useState<string>(ALL_GROUPS);
  const [managerOpen, setManagerOpen] = React.useState(false);
  const [fieldError, setFieldError] = React.useState<string | null>(null);
  const buttonRef = React.useRef<HTMLButtonElement>(null);
  const reducedMotion = useReducedMotion();

  const resolved = React.useMemo(() => resolvePresets(views, catalogue), [views, catalogue]);
  const active = React.useMemo(
    () => activePresetId(resolved, view, columns),
    [resolved, view, columns],
  );

  const close = React.useCallback((returnFocus: boolean) => {
    setOpen(false);
    setScope(ALL_GROUPS);
    setFieldError(null);
    if (returnFocus) buttonRef.current?.focus();
  }, []);

  /**
   * The open transition, in two frames.
   *
   * The panel is mounted collapsed (`grid-rows-[0fr]`), then expanded on the
   * next frame so the browser has a start value to animate from. A reader who
   * asked for reduced motion gets the final frame immediately — the stylesheet
   * also collapses every duration, but the class is not applied at all, so
   * what a test can see is what the browser does.
   */
  React.useEffect(() => {
    if (!open) {
      setMounted(false);
      setExpanded(false);
      return;
    }
    setMounted(true);
    if (reducedMotion) {
      setExpanded(true);
      return;
    }
    setExpanded(false);
    // `requestAnimationFrame` is the correct primitive; a test environment (or
    // an old webview) without it still gets the expanded frame, one task later.
    if (typeof window.requestAnimationFrame !== "function") {
      const timer = window.setTimeout(() => setExpanded(true), 0);
      return () => window.clearTimeout(timer);
    }
    const frame = window.requestAnimationFrame(() => setExpanded(true));
    return () => window.cancelAnimationFrame(frame);
  }, [open, reducedMotion]);

  /**
   * Escape, from anywhere the panel has focus.
   *
   * On the window rather than the panel's own `onKeyDown` because a native
   * `<select>`'s open menu swallows keys in some browsers: a reader who tabs
   * into "Sector", presses Escape and gets a closed menu but an open panel has
   * been told the wrong thing twice. The first Escape always means "close this".
   */
  React.useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      close(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  const groups = vocabulary.doc?.groups ?? [];
  const shownGroups = scope === ALL_GROUPS ? groups : groups.filter((group) => group.id === scope);
  const signals = (vocabulary.doc?.signal?.options ?? []).filter((option) => option.available);

  /**
   * The Order by menu: the three named token sorts, then every column the view
   * draws (the columns are real sort keys, and a reader who just sorted by a
   * header expects to find it here), then — if the URL names something this
   * view does not draw — the current token itself, so the control never shows
   * a value it does not list.
   */
  const activeSortKey = sortKey(sort);
  const orderOptions = React.useMemo(() => {
    const seen = new Set<string>();
    const options: Array<{ value: string; label: string }> = [];
    const push = (value: string, label: string) => {
      if (seen.has(value)) return;
      seen.add(value);
      options.push({ value, label });
    };
    for (const token of SORTS) push(token, SORT_LABELS[token]);
    for (const key of columns) {
      if (key === "no" || key === "identity") continue;
      push(key, columnSpec(key).label);
    }
    if (!seen.has(activeSortKey)) push(activeSortKey, sortLabel(activeSortKey));
    return options;
  }, [columns, activeSortKey]);

  /**
   * A built-in tab.
   *
   * The Columns select and the tab bar are different vocabularies on purpose —
   * the select is the reader's own sets, the tabs are the product's — so this
   * handler exists only for the tabs.
   */
  const choosePreset = (entry: ResolvedPreset) => {
    if (entry.preset.custom && onOpenColumnPicker) {
      onOpenColumnPicker();
      return;
    }
    onSelectPreset(entry);
  };

  /** A saved set: remember the choice, and put its columns on screen. */
  const chooseUserPreset = (id: string) => {
    const preset = userPresets.presets.find((candidate) => candidate.id === id);
    if (!preset) return;
    userPresets.select(id);
    if (preset.columns.length > 0) onApplyColumns(preset.columns);
  };

  /**
   * Which saved set the control shows.
   *
   * Derived from the columns on screen, not from the last click: choosing a
   * built-in tab replaces the columns, so the select must stop claiming a saved
   * set that is no longer displayed — and must let the reader pick it again.
   */
  const savedMatch = userPresets.presets.find(
    (preset) =>
      preset.columns.length > 0 &&
      preset.columns.length === columns.length &&
      preset.columns.every((key, index) => key === columns[index]),
  );
  const columnsValue = savedMatch?.id ?? DEFAULT_PRESET_ID;

  const addFilter = (filter: WireFilter | null) => {
    if (!filter) return;
    setFieldError(null);
    onAddFilter(filter);
  };

  return (
    <header data-testid="screener-header" className="shrink-0">
      {/* The filled bar: the heading and its controls centred on the content
          width, the filter trigger on the right. `--accent-strong` is the fill
          weight of the one rose ramp and `--accent-fg` is its ink; the pair is
          5.43:1 in the dark set and 6.79:1 in the day set. */}
      <div className="bg-accent-strong text-accent-fg">
        <div className="mx-auto flex w-full max-w-[1680px] items-center gap-3 px-4 py-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-center gap-x-4 gap-y-2">
            <h1
              data-testid="screener-title"
              className="bg-accent-strong text-title font-semibold tracking-tight text-accent-fg"
            >
              {title}
            </h1>

            <div
              role="group"
              aria-label="Screener controls"
              className="flex min-w-0 flex-wrap items-center justify-center gap-3"
            >
              <div className="flex items-center gap-1.5">
                <label className="flex items-center gap-1.5">
                  <span className="font-mono text-label font-semibold uppercase tracking-[0.08em] text-accent-fg">
                    Columns
                  </span>
                  <select
                    aria-label="Your column presets"
                    data-testid="columns-select"
                    value={columnsValue}
                    onChange={(event) => chooseUserPreset(event.target.value)}
                    title="Your own saved column sets. The built-in sets are the tabs below."
                    className="h-7 max-w-[13rem] rounded-sm border border-line-strong bg-surface px-1.5 text-data text-fg"
                  >
                    {userPresets.presets.map((preset) => (
                      <option key={preset.id} value={preset.id}>
                        {preset.name}
                        {preset.saved ? ` · ${preset.columns.length}` : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  data-testid="manage-column-presets"
                  onClick={() => setManagerOpen(true)}
                  title="Create, rename, delete or apply your own column sets."
                  className="h-7 rounded-sm border border-line-strong bg-surface px-2 text-data text-fg transition-colors hover:border-fg"
                >
                  Manage
                </button>
              </div>

              <label className="flex items-center gap-1.5">
                <span className="font-mono text-label font-semibold uppercase tracking-[0.08em] text-accent-fg">
                  Order by
                </span>
                <select
                  aria-label="Order by"
                  data-testid="order-select"
                  value={activeSortKey}
                  onChange={(event) => onSortChange(event.target.value)}
                  title={`Order the whole screen by this column, highest first. Currently: ${sortLabel(sort)}, ${sortDirection(sort) === "desc" ? "highest first" : "lowest first"}.`}
                  className="h-7 max-w-[13rem] rounded-sm border border-line-strong bg-surface px-1.5 text-data text-fg"
                >
                  {orderOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <span
                  aria-hidden="true"
                  data-testid="order-direction"
                  data-direction={sortDirection(sort)}
                  title={sortDirection(sort) === "desc" ? "Highest first" : "Lowest first"}
                  className="font-mono text-data text-accent-fg"
                >
                  {sortDirection(sort) === "desc" ? "↓" : "↑"}
                </span>
              </label>

              <label className="flex items-center gap-1.5">
                <span className="font-mono text-label font-semibold uppercase tracking-[0.08em] text-accent-fg">
                  Signal
                </span>
                <select
                  aria-label="Signal"
                  data-testid="signal-select"
                  value={signal ?? ""}
                  onChange={(event) =>
                    onSignalChange(event.target.value.length > 0 ? event.target.value : null)
                  }
                  title="A named screen the API compiles for you; it orders its own rows."
                  className="h-7 max-w-[12rem] rounded-sm border border-line-strong bg-surface px-1.5 text-data text-fg"
                >
                  <option value="">No signal</option>
                  {signals.map((option) => (
                    <option key={option.id} value={option.id} title={option.predicate ?? undefined}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              {/* The one search box on this screen that looks a symbol up rather
                  than filtering the table: a hit docks that symbol's detail
                  panel. Its ids and its name are its own, so the nav's box and
                  this one do not answer to the same name. */}
              <SymbolSearch
                className="w-[14rem] max-w-full"
                label="Ticker search"
                idPrefix="ticker-search"
                placeholder="Ticker"
                showHint={false}
                onSelect={(hit) => onSelectSymbol(hit.symbol)}
              />
            </div>
          </div>

          <button
            ref={buttonRef}
            type="button"
            data-testid="filter-button"
            aria-expanded={open}
            aria-controls={FILTER_PANEL_ID}
            onClick={() => (open ? close(false) : setOpen(true))}
            className={cn(
              "flex h-8 shrink-0 items-center gap-2 rounded-sm border border-accent-fg bg-accent-fg px-3 text-ui font-semibold text-accent-strong transition-colors",
              !open && "hover:brightness-95",
            )}
          >
            <SlidersHorizontal aria-hidden="true" className="size-3.5" />
            Filter
            {filters.length > 0 ? (
              <span
                aria-hidden="true"
                className="rounded-sm bg-accent-strong px-1 font-mono text-label text-accent-fg"
              >
                {filters.length}
              </span>
            ) : null}
            <ChevronDown
              aria-hidden="true"
              data-testid="filter-chevron"
              data-open={open}
              className={cn(
                "size-3.5 transition-transform duration-200 ease-out",
                open && "rotate-180",
              )}
            />
          </button>
        </div>
      </div>

      {/* The disclosure. Mounted collapsed, expanded one frame later; the
          reader who asked for stillness gets the expanded frame at once. */}
      {mounted ? (
        <div
          id={FILTER_PANEL_ID}
          data-testid="filter-panel"
          role="region"
          aria-label="Filter by group"
          data-motion={reducedMotion ? "instant" : "animated"}
          className={cn(
            "grid overflow-hidden border-b border-line bg-surface-raised",
            !reducedMotion && "transition-[grid-template-rows,opacity] duration-200 ease-out",
            expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
          )}
        >
          <div className="min-h-0 overflow-hidden">
            <div className="mx-auto w-full max-w-[1680px] px-4 py-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <div
                  role="group"
                  aria-label="Filter groups"
                  className="flex flex-wrap items-center gap-1"
                >
                  <ScopeButton
                    label="All"
                    pressed={scope === ALL_GROUPS}
                    onPress={() => setScope(ALL_GROUPS)}
                    title="Show every group's controls at once."
                  />
                  {groups.map((group) => (
                    <ScopeButton
                      key={group.id}
                      label={group.label}
                      pressed={scope === group.id}
                      onPress={() => setScope(group.id)}
                      title={`Only the ${group.label} controls.`}
                    />
                  ))}
                </div>

                {vocabulary.status === "loading" ? (
                  <span aria-busy="true" className="text-data text-fg-subtle">
                    Reading the filter document…
                  </span>
                ) : null}
                {vocabulary.status === "ready" ? (
                  <span className="text-data text-fg-subtle">
                    {countControls(groups)} controls · {countOptions(groups)} values
                  </span>
                ) : null}
                {filters.length > 0 ? (
                  <button
                    type="button"
                    onClick={onClearFilters}
                    className="ml-auto rounded-sm px-1.5 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle transition-colors hover:text-fg"
                  >
                    Clear filters
                  </button>
                ) : null}
              </div>

              {fieldError ? (
                <p role="alert" className="mb-2 text-data text-alert">
                  {fieldError}
                </p>
              ) : null}

              {vocabulary.status === "error" ? (
                <div
                  role="alert"
                  className="flex items-start justify-between gap-3 rounded-sm border border-line border-l-2 border-l-alert bg-surface px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="text-ui font-medium text-fg">The filter list did not load</p>
                    <p className="mt-0.5 break-words text-data text-fg-muted">{vocabulary.error}</p>
                  </div>
                  <button
                    type="button"
                    onClick={vocabulary.retry}
                    className="shrink-0 rounded-sm border border-line-strong px-2 py-1 text-ui text-fg-muted hover:text-fg"
                  >
                    Retry
                  </button>
                </div>
              ) : null}

              {vocabulary.status === "ready" ? (
                <div className="max-h-[52vh] overflow-y-auto pr-1">
                  {shownGroups.map((group) => (
                    <GroupFields
                      key={group.id}
                      group={group}
                      onAdd={addFilter}
                      onError={setFieldError}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {/* The built-in tabs. Mounted whether the panel is open or shut: a tab is
          columns, and the columns are on screen either way. */}
      <div className="border-b border-line bg-surface-sunken">
        <div className="mx-auto flex w-full max-w-[1680px] items-stretch gap-2 px-2">
          <div
            role="toolbar"
            aria-label="Built-in column presets"
            data-testid="column-presets"
            className="flex min-w-0 flex-1 flex-wrap items-stretch justify-center gap-y-0.5"
          >
            {resolved.map((entry) => (
              <PresetTab
                key={entry.preset.id}
                entry={entry}
                active={entry.preset.id === active}
                onSelect={() => choosePreset(entry)}
              />
            ))}
          </div>

          {wide ? (
            <button
              type="button"
              onClick={wide.onToggle}
              aria-pressed={wide.active}
              data-testid="all-columns-toggle"
              title={`${wide.label}: ${wide.count} keys, fetched in chunks of at most 40 columns.`}
              className={cn(
                "-mb-px shrink-0 self-center rounded-sm border px-2 py-1 font-mono text-label uppercase tracking-[0.08em] transition-colors",
                wide.active
                  ? "border-brand bg-row-selected text-fg"
                  : "border-transparent text-fg-muted hover:text-fg",
              )}
            >
              {wide.label}
              <span aria-hidden="true" className="ml-1.5 text-fg-subtle">
                {wide.count}
              </span>
            </button>
          ) : null}
        </div>
      </div>

      <ColumnPresetsDialog
        open={managerOpen}
        onClose={() => setManagerOpen(false)}
        presets={userPresets.presets}
        currentColumns={columns}
        onSave={(name) => userPresets.save(name, columns)}
        onRename={(id, name) => userPresets.rename(id, name)}
        onDelete={(id) => userPresets.remove(id)}
        onUse={(id) => {
          chooseUserPreset(id);
          setManagerOpen(false);
        }}
      />
    </header>
  );
}

/**
 * One group's controls, under a small heading.
 *
 * A `select` per control is the panel's whole shape: twenty-odd controls in one
 * list would ask the reader to scroll a menu of five hundred values to find
 * "Market cap". One labelled field per control is a form you can scan.
 */
function GroupFields({
  group,
  onAdd,
  onError,
}: {
  group: FilterGroup;
  onAdd: (filter: WireFilter | null) => void;
  onError: (message: string | null) => void;
}) {
  const controls = group.controls.filter(isFilterControl);
  if (controls.length === 0) return null;

  return (
    <section aria-label={group.label} className="mb-3 last:mb-0">
      <h3 className="mb-1.5 flex items-baseline gap-2 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        {group.label}
        <span aria-hidden="true" className="text-fg-disabled">
          {controls.length}
        </span>
      </h3>
      <div className="grid grid-cols-1 gap-x-3 gap-y-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
        {controls.map((control) => (
          <ControlField key={control.id} control={control} onAdd={onAdd} onError={onError} />
        ))}
      </div>
    </section>
  );
}

/**
 * One control, in the shape its own entry asks for.
 *
 * * options → a `<select>`, one option per published value;
 * * `text` → a text input, `contains` on Enter or the Set button;
 * * `number` → a number input with the control's operator;
 * * unavailable, or a select with nothing published → a disabled control that
 *   states why, rather than an empty menu that looks broken.
 */
function ControlField({
  control,
  onAdd,
  onError,
}: {
  control: FilterControl;
  onAdd: (filter: WireFilter | null) => void;
  onError: (message: string | null) => void;
}) {
  const [draft, setDraft] = React.useState("");
  const label = (
    <span className="truncate text-data text-fg-muted" title={control.label}>
      {control.label}
    </span>
  );

  if (!control.available) {
    return (
      <label className="flex min-w-0 flex-col gap-0.5" data-testid={`control-${control.id}`}>
        {label}
        <select
          aria-label={control.label}
          disabled
          title={control.reason ?? "This control has no source yet."}
          className="h-7 min-w-0 rounded-sm border border-line bg-surface-sunken px-1.5 text-data text-fg-disabled"
        >
          <option>Unavailable</option>
        </select>
      </label>
    );
  }

  if (control.options.length > 0) {
    return (
      <label className="flex min-w-0 flex-col gap-0.5" data-testid={`control-${control.id}`}>
        {label}
        <select
          aria-label={control.label}
          data-testid={`control-select-${control.id}`}
          value=""
          title={control.hint ?? control.note ?? undefined}
          onChange={(event) => {
            if (event.target.value.length === 0) return;
            const option = control.options.find(
              (candidate) => optionKey(control, candidate) === event.target.value,
            );
            if (option) onAdd(optionFilter(control, option));
            event.target.value = "";
          }}
          className="h-7 min-w-0 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
        >
          <option value="">Any</option>
          {control.options.map((option) => (
            <option
              key={optionKey(control, option)}
              value={optionKey(control, option)}
              disabled={!option.available}
              title={option.reason ?? option.predicate ?? undefined}
            >
              {option.available ? option.label : `${option.label} — unavailable`}
            </option>
          ))}
        </select>
      </label>
    );
  }

  if (control.control === "text" || control.control === "number") {
    const numeric = control.control === "number";
    const apply = () => {
      const text = draft.trim();
      if (text.length === 0) {
        onError(`${control.label} needs a value.`);
        return;
      }
      if (numeric) {
        const value = Number(text);
        if (!Number.isFinite(value)) {
          onError(`${control.label} needs a number, not “${text}”.`);
          return;
        }
        onAdd({ key: control.key!, op: (control.op ?? "gte") as FilterOp, value });
        setDraft("");
        return;
      }
      onAdd({ key: control.key!, op: (control.op ?? "contains") as FilterOp, value: text });
      setDraft("");
    };

    return (
      <label className="flex min-w-0 flex-col gap-0.5" data-testid={`control-${control.id}`}>
        {label}
        <span className="flex items-center gap-1">
          <input
            aria-label={control.label}
            data-testid={`control-input-${control.id}`}
            type={numeric ? "number" : "text"}
            inputMode={numeric ? "decimal" : "text"}
            value={draft}
            placeholder={numeric ? control.unit ?? "number" : "contains…"}
            title={control.hint ?? undefined}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              apply();
            }}
            className="h-7 min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
          />
          <button
            type="button"
            onClick={apply}
            aria-label={`Apply ${control.label}`}
            className="h-7 shrink-0 rounded-sm border border-line-control px-1.5 text-data text-fg-muted transition-colors hover:text-fg"
          >
            Set
          </button>
        </span>
      </label>
    );
  }

  return (
    <label className="flex min-w-0 flex-col gap-0.5" data-testid={`control-${control.id}`}>
      {label}
      <select
        aria-label={control.label}
        disabled
        title={control.optionsSource ?? "No options are published for this control yet."}
        className="h-7 min-w-0 rounded-sm border border-line bg-surface-sunken px-1.5 text-data text-fg-disabled"
      >
        <option>No values published</option>
      </select>
    </label>
  );
}

function ScopeButton({
  label,
  pressed,
  onPress,
  title,
}: {
  label: string;
  pressed: boolean;
  onPress: () => void;
  title: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onPress}
      title={title}
      className={cn(
        "rounded-sm border px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] transition-colors",
        pressed
          ? "border-brand bg-row-selected text-fg"
          : "border-line text-fg-muted hover:border-line-strong hover:text-fg",
      )}
    >
      {label}
    </button>
  );
}

/**
 * One built-in tab.
 *
 * The toolbar is one tab stop, not eighteen: the selected tab carries
 * `tabIndex={0}` and its siblings `-1`, and the arrow keys move between them.
 */
function PresetTab({
  entry,
  active,
  onSelect,
}: {
  entry: ResolvedPreset;
  active: boolean;
  onSelect: () => void;
}) {
  const ref = React.useRef<HTMLButtonElement>(null);

  const move = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const bar = ref.current?.parentElement;
    if (!bar) return;
    const buttons = [...bar.querySelectorAll<HTMLButtonElement>("button[data-preset]")];
    const at = buttons.indexOf(ref.current as HTMLButtonElement);
    let next = at;
    switch (event.key) {
      case "ArrowRight":
        next = (at + 1) % buttons.length;
        break;
      case "ArrowLeft":
        next = (at - 1 + buttons.length) % buttons.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = buttons.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    buttons[next]?.focus();
  };

  return (
    <button
      ref={ref}
      type="button"
      data-preset={entry.preset.id}
      data-testid={`preset-${entry.preset.id}`}
      aria-pressed={active}
      aria-label={`${entry.preset.label} — ${entry.description}`}
      title={`${entry.preset.job} ${entry.note}`}
      tabIndex={active ? 0 : -1}
      onClick={onSelect}
      onKeyDown={move}
      className={cn(
        "relative -mb-px shrink-0 border-b-2 px-2 pb-1.5 pt-2 text-ui transition-colors",
        active
          ? "border-brand bg-surface-raised font-medium text-fg"
          : "border-transparent text-fg-muted hover:text-fg",
      )}
    >
      {entry.preset.label}
    </button>
  );
}

/** How many filter controls the panel can draw. */
function countControls(groups: readonly FilterGroup[]): number {
  return groups.reduce(
    (total, group) => total + group.controls.filter(isFilterControl).length,
    0,
  );
}

/** How many values those controls publish. */
function countOptions(groups: readonly FilterGroup[]): number {
  return groups.reduce(
    (total, group) =>
      total +
      group.controls
        .filter(isFilterControl)
        .reduce((sum, control) => sum + control.options.length, 0),
    0,
  );
}
