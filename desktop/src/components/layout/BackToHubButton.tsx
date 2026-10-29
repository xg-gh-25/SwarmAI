/**
 * BackToHubButton — the way from one Brain's page back to the Brain Hub home, shared by
 * a Brain's detail header (BrainHub) and the General Brain's page (GeneralBrainView).
 *
 * A bordered button with an arrow, not a muted text link: it is the page's only way
 * home besides the host title, and a muted "‹ All brains" line was easy to miss.
 *
 * @exports BackToHubButton
 */
export function BackToHubButton({ onClick, testid }: { onClick: () => void; testid: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      aria-label="Back to Brain Hub"
      className="inline-flex items-center gap-1 h-7 pl-1.5 pr-2.5 mb-1 rounded-md border border-[var(--color-border)] text-small font-medium text-[var(--color-text)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] hover:bg-[var(--color-hover)] transition-colors"
    >
      <span className="material-symbols-outlined icon-sm" aria-hidden>arrow_back</span>
      Brain Hub
    </button>
  );
}
