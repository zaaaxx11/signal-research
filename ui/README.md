# Ztockz cockpit

The cockpit is a static presentation layer served by the same FastAPI process as
the market engine. It does not query providers, calculate prices, or invent a
cell: every row comes from `POST /screens/select`, and row names/source context
come from `GET /screens?source=tradingview`.

## Run

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8099
```

Open `http://127.0.0.1:8099/ui/`.

## Interaction map

- `⌘ K` / `Ctrl K`: command palette
- `/`: focus the row filter
- `1` / `2` / `3`: Discover, Watchlist, Signals
- `R`: refresh the scan
- `C`: compact density
- Click a row: open its evidence in the right rail
- Star: persist the symbol in the local watchlist
- Compare: collect up to three names, then open the comparison panel
- Columns: hide or restore Fwd P/E, RSI, and YTD
- News: fetch source-backed headlines for the selected symbol

Missing values render as `--`. The UI never substitutes zero for an unknown.

## Files

- `index.html`: shell and accessible structure
- `styles.css`: dark rose-pink visual system, responsive layout
- `app.js`: API loading, state, interactions, local persistence
