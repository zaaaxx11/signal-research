# Third-party notices — Ztockz web

## shadcn/ui (MIT)

Files under `src/components/ui/` are copied from `shadcn-ui/ui` at commit
`b8d7baa15b1dca5716f13153e63795fc3d741f6b`
(<https://github.com/shadcn-ui/ui/tree/b8d7baa15b1dca5716f13153e63795fc3d741f6b/apps/v4/registry/new-york-v4/ui>),
fetched 2026-10-30 by `scripts/vendor-shadcn.mjs`. Each file's header records the
exact URL and commit. The copy is verbatim except for two module specifiers, as
described in that header.

The MIT licence text below covers those files.

```
MIT License

Copyright (c) 2023 shadcn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## lightweight-charts 5.2.1 (Apache-2.0)

`lightweight-charts` draws the bar charts on Home's index strip, `/charts` and
`/stock/{symbol}`. It is installed from the public npm registry, but unlike a
dev-only dependency its code is **bundled into the shipped artefact** (the
lazily imported `assets/ChartBars-*.js`), so Apache-2.0 §4 requires the licence
and the upstream NOTICE to travel with the distribution.

* `legal/lightweight-charts-LICENSE.txt` — the Apache License 2.0 text, copied
  verbatim from the published package
  (`node_modules/lightweight-charts/LICENSE`).
* `legal/lightweight-charts-NOTICE.txt` — the upstream NOTICE, copied verbatim
  from `tradingview/lightweight-charts` at tag `v5.2.1`
  (<https://github.com/tradingview/lightweight-charts/blob/v5.2.1/NOTICE>).
  The npm tarball omits this file, so it is taken from the tagged source rather
  than dropped.

No source file from the library is modified or copied into `src/`; it is used
through its public API only, and every colour it paints is one of this
product's own CSS tokens.

## npm dependencies

Every direct dependency is listed with its licence in `README.md`. They are
installed from the public npm registry and their licence texts ship inside
`node_modules/` as published; no licence file is copied into this directory
because nothing here is redistributed as source — the one exception is
`lightweight-charts`, whose code is bundled and whose Apache-2.0 notice is
therefore carried in `legal/` (see above).

## What is *not* here

No theme file from tweakcn, no dashboard template, no blocks registry. The plan
allowed a tweakcn ramp (Apache-2.0, NOTICE required); the token file was authored
instead, so there is no third-party licence to carry. This is deliberate: a
copied theme is a design system we would have to reconcile, and the provenance
obligation would outlive the phase.
