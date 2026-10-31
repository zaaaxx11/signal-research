import { afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";

// React 19 refuses to flush state updates outside `act` unless the test
// environment says it is a test environment.
beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    true;
});

// Vitest runs with `globals: false`, so Testing Library cannot register its own
// auto-cleanup; doing it here keeps each test's DOM isolated.
afterEach(() => {
  cleanup();
});
