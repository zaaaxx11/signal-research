import { afterEach, beforeAll, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";

import { clearPageCache } from "@/data/screens";

// React 19 refuses to flush state updates outside `act` unless the test
// environment says it is a test environment.
beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    true;
});

// The page cache is a module-level fact, exactly as it is in the browser: one
// tab keeps the pages it has seen. Tests render a fresh panel per case, so the
// cache has to start empty or the second case silently inherits the first
// one's rows — and with them, the requests it never made.
beforeEach(() => {
  clearPageCache();
});

// Vitest runs with `globals: false`, so Testing Library cannot register its own
// auto-cleanup; doing it here keeps each test's DOM isolated.
afterEach(() => {
  cleanup();
});
import '@testing-library/jest-dom/vitest';
