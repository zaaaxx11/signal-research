/**
 * The footer: the word.
 *
 * No version, no build stamp, no "as of", no health line — the operator asked
 * for a plain footer and the product's other rule is that internal status is
 * never product chrome. It is a `<footer>` landmark so it can be skipped to,
 * and 32 px so it reads as an edge rather than a section.
 */

export function HomeFooter() {
  return (
    <footer
      data-testid="home-footer"
      className="flex h-8 items-center rounded-md border border-line bg-surface px-3 text-[11px] lowercase tracking-[0.08em] text-fg-subtle"
    >
      ztockz
    </footer>
  );
}
