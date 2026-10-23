/**
 * Tests for ArtifactsOverlay — the TIME-GROUPED retrieval list (run_cb127db1, mockup v2).
 *
 * Paradigm (Knowledge/Designs/2026-09-30-artifacts-overlay-v2-mock.html): the overlay is a
 * scannable retrieval LIST, not a thumbnail gallery. The active role (default Deliverables)
 * is the PRIMARY, shown time-grouped (This week / Last week / Earlier-fold). The other three
 * roles are DEMOTED to collapsible drawers below. Type chips switch the primary role; the
 * time-window chip defaults to "past 2 weeks" (Earlier folded); typing in search IGNORES the
 * window and matches across all time. Zero thumbnails — each row is badge + friendly title +
 * full dir + absolute timestamp. Star/favorite is deferred (next round).
 *
 * The fetch is injected via the `fetchProducts` test seam (boundary mock).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import type { Product } from '../../services/radar';
import {
  ArtifactsContent,
  timeBucket,
  groupByTime,
  relativeTime,
  absoluteTime,
  dirDisplay,
  friendlyTitle,
  fileBadge,
  parentRunLabel,
  OPEN_FILE_EVENT,
} from './ArtifactsOverlay';

// Fixed "now" so time buckets are deterministic across machines/timezones.
const NOW = new Date('2026-09-30T12:00:00Z').getTime();
const daysAgo = (d: number) => new Date(NOW - d * 86400000).toISOString();

function prod(path: string, role: Product['role'], extra: Partial<Product> = {}): Product {
  const base = path.replace(/\\/g, '/').split('/').filter(Boolean).pop() ?? path;
  return {
    path,
    role,
    kind: 'content',
    gitignored: false,
    firstProduced: daysAgo(1),
    lastTouched: daysAgo(1),
    displayLabel: base,
    ...extra,
  };
}

// A fixture spanning time buckets + all four roles.
const FIXTURE: Product[] = [
  prod('Projects/AIDLC/assets/2026-09-29-ai-native-deck.html', 'Deliverables', { gitignored: true, lastTouched: daysAgo(1) }),   // This week
  prod('Knowledge/Reports/pipeline-weekly.md', 'Deliverables', { lastTouched: daysAgo(2) }),                                      // This week
  prod('Knowledge/Designs/2026-09-20-artifact-lifecycle-tech-design.md', 'Deliverables', { lastTouched: daysAgo(10) }),          // Last week
  prod('Knowledge/Library/2026-08-30-ai-native-ee-oe-deck.html', 'Deliverables', { lastTouched: daysAgo(40) }),                  // Earlier
  prod('Projects/SwarmAI/2-understanding/TECH.md', 'Knowledge', { kind: 'knowledge', lastTouched: daysAgo(3) }),
  prod('Projects/SwarmAI/.artifacts/runs/run_2274b401/REPORT.md', 'Pipeline', { kind: 'knowledge', displayLabel: 'Artifacts data root-fix', lastTouched: daysAgo(1) }),
  prod('Knowledge/DailyActivity/2026-09-30.md', 'Activity', { lastTouched: daysAgo(1) }),
];

function renderWithClient(node: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

afterEach(() => cleanup());

// ─────────────────────────── pure helpers ───────────────────────────

describe('timeBucket (pure) — AC3', () => {
  it('buckets by age: <7d This week, 7-14d Last week, >14d Earlier', () => {
    expect(timeBucket(daysAgo(1), NOW)).toBe('This week');
    expect(timeBucket(daysAgo(6), NOW)).toBe('This week');
    expect(timeBucket(daysAgo(8), NOW)).toBe('Last week');
    expect(timeBucket(daysAgo(13), NOW)).toBe('Last week');
    expect(timeBucket(daysAgo(40), NOW)).toBe('Earlier');
  });
  it('unparseable / missing date → Earlier (never dropped, AC6)', () => {
    expect(timeBucket('not-a-date', NOW)).toBe('Earlier');
    expect(timeBucket('', NOW)).toBe('Earlier');
  });
});

describe('groupByTime (pure) — AC3', () => {
  it('groups in fixed order, newest-first within a bucket, non-empty only', () => {
    const groups = groupByTime(FIXTURE.filter((p) => p.role === 'Deliverables'), NOW);
    expect(groups.map((g) => g.bucket)).toEqual(['This week', 'Last week', 'Earlier']);
    // This week newest-first: 1d before 2d
    expect(groups[0].products[0].path).toContain('ai-native-deck');
    expect(groups[0].products).toHaveLength(2);
    expect(groups[1].products).toHaveLength(1);
    expect(groups[2].products).toHaveLength(1);
  });
  it('returns [] for empty input', () => {
    expect(groupByTime([], NOW)).toEqual([]);
  });
  it('an unparseable-date row lands in Earlier, never dropped', () => {
    const groups = groupByTime([prod('x/y.md', 'Deliverables', { lastTouched: 'bogus' })], NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0].bucket).toBe('Earlier');
  });
});

describe('dirDisplay (pure) — AC4 (full path; …/ only when too long)', () => {
  it('shows the FULL dir path when it fits', () => {
    expect(dirDisplay('Projects/AIDLC/assets/deck.html')).toBe('Projects/AIDLC/assets');
    expect(dirDisplay('Knowledge/Designs/mock.html')).toBe('Knowledge/Designs');
  });
  it('elides with a leading …/ only when the dir path is too long', () => {
    const out = dirDisplay('Projects/AIDLC/assets/2026-07-28-cntech-allhands-slides/lightweight.html');
    expect(out.startsWith('…/')).toBe(true);
    expect(out).toContain('2026-07-28-cntech-allhands-slides');
    expect(out.length).toBeLessThan(48);
  });
  it('bare filename → empty', () => {
    expect(dirDisplay('deck.html')).toBe('');
  });
});

describe('fileBadge (pure) — AC4 (extension-typed badge, incl. MD/DOCX)', () => {
  it('classifies by extension: DECK / IMG / PDF / MD / HTML / DOCX / DOC', () => {
    expect(fileBadge('Projects/AIDLC/assets/x.pptx').badge).toBe('DECK');
    expect(fileBadge('Projects/AIDLC/assets/2026-08-30-ai-native-deck.html').badge).toBe('DECK'); // -deck name
    expect(fileBadge('Knowledge/Designs/pic.png').badge).toBe('IMG');
    expect(fileBadge('Knowledge/Library/report.pdf').badge).toBe('PDF');
    expect(fileBadge('Knowledge/Reports/pipeline-weekly.md').badge).toBe('MD'); // .md → MD (mockup)
    expect(fileBadge('Knowledge/Library/page.html').badge).toBe('HTML');
    expect(fileBadge('Knowledge/Docs/spec.docx').badge).toBe('DOCX');
    expect(fileBadge('Knowledge/Notes/plain.txt').badge).toBe('DOC'); // default
  });
  it('every badge is non-empty and carries a color', () => {
    for (const p of ['a.pptx', 'b.png', 'c.pdf', 'd.md', 'e.html', 'f.docx', 'g.txt']) {
      const b = fileBadge(p);
      expect(b.badge.length).toBeGreaterThan(0);
      expect(b.color.length).toBeGreaterThan(0);
    }
  });
});

describe('friendlyTitle (pure) — AC4', () => {
  it('strips a leading YYYY-MM-DD date + extension, de-slugs, title-cases', () => {
    expect(friendlyTitle('2026-08-30-ai-native-ee-oe-deck.html')).toBe('Ai Native Ee Oe Deck');
  });
  it('no date prefix: just de-slug + title-case, preserves acronym caps', () => {
    expect(friendlyTitle('SecDLC-flywheel.png')).toBe('SecDLC Flywheel');
  });
  it('strips ONLY the last extension (dots in the middle survive)', () => {
    expect(friendlyTitle('v1.2-deck.html')).toBe('V1.2 Deck');
  });
  it('all-date name → basename fallback, never empty', () => {
    expect(friendlyTitle('2026-09-30.md')).toBe('2026-09-30.md');
  });
});

describe('absoluteTime (pure) — AC4', () => {
  it('renders YYYY-MM-DD HH:MM in local time', () => {
    expect(absoluteTime('2026-06-04T01:13:16+00:00')).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  });
  it('unparseable → empty string (never crashes a row)', () => {
    expect(absoluteTime('not-a-date')).toBe('');
    expect(absoluteTime('')).toBe('');
  });
});

describe('relativeTime (pure) — hover title', () => {
  it('unparseable → empty string', () => {
    expect(relativeTime('not-a-date', NOW)).toBe('');
  });
});

describe('parentRunLabel (pure) — Pipeline row fallback', () => {
  it('labels a REPORT.md by its parent run dir', () => {
    expect(parentRunLabel('Projects/SwarmAI/.artifacts/runs/run_2274b401/REPORT.md')).toBe('run_2274b401');
  });
});

// ─────────────────────────── render contract ───────────────────────────

describe('ArtifactsContent — time-grouped list', () => {
  let fetchProducts: ReturnType<typeof vi.fn>;
  let closeSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchProducts = vi.fn().mockResolvedValue(FIXTURE);
    closeSpy = vi.fn();
  });

  it('a failed first load shows the error with a Retry that fetches again (OverlayState)', async () => {
    fetchProducts = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValue(FIXTURE);
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    expect(await screen.findByTestId('artifacts-error')).toHaveTextContent(/Couldn't load products/);
    fireEvent.click(screen.getByTestId('artifacts-retry'));
    await waitFor(() => expect(screen.getByText('This week')).toBeInTheDocument());
    expect(fetchProducts).toHaveBeenCalledTimes(2);
  });

  it('AC3: renders the primary role (Deliverables) as a TIME-GROUPED list with This week / Last week headers', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByText('This week')).toBeInTheDocument());
    expect(screen.getByText('Last week')).toBeInTheDocument();
    // primary rows shown as list rows (not thumbnail cards)
    expect(screen.getAllByTestId('artifacts-row').length).toBeGreaterThan(0);
    expect(screen.queryByTestId('artifacts-card-thumb')).toBeNull(); // no thumbnails
  });

  it('AC4: a row shows badge + friendly title + full dir + absolute timestamp', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByText('Ai Native Deck')).toBeInTheDocument()); // friendly title
    expect(screen.queryByText('2026-09-29-ai-native-deck.html')).toBeNull();             // not raw filename
    const badges = screen.getAllByTestId('artifacts-row-badge');
    expect(badges.some((b) => /DECK/i.test(b.textContent ?? ''))).toBe(true);
    expect(screen.getByText('Projects/AIDLC/assets')).toBeInTheDocument();               // FULL dir
    const times = screen.getAllByTestId('artifacts-row-time');
    expect(times[0].textContent).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);              // absolute time
  });

  it('AC5: type chips switch the primary role; the time-window chip defaults to past-2-weeks', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    // wait for DATA (the rolefilter renders immediately during loading, so it's not a data anchor)
    await waitFor(() => expect(screen.getByText('Ai Native Deck')).toBeInTheDocument());
    for (const role of ['Deliverables', 'Knowledge', 'Pipeline', 'Activity']) {
      expect(screen.getByTestId(`artifacts-chip-${role}`)).toBeInTheDocument();
    }
    // window chip defaults to the 2-week label
    expect(screen.getByTestId('artifacts-window-chip').textContent).toMatch(/两周|2\s*week/i);
    // switch primary to Knowledge → its item becomes a primary time-grouped row
    // (friendlyTitle strips the extension: TECH.md → "TECH")
    fireEvent.click(screen.getByTestId('artifacts-chip-Knowledge'));
    await waitFor(() => expect(screen.getByText('TECH')).toBeInTheDocument());
  });

  it('AC6: rows beyond the 2-week window fold into a "▸ N more" row that expands', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByText('This week')).toBeInTheDocument());
    // the 40-day-old deck is Earlier → folded, not shown
    expect(screen.queryByText('Ai Native Ee Oe Deck')).toBeNull();
    expect(screen.getByTestId('artifacts-fold')).toBeInTheDocument();
    // expand the fold → Earlier rows appear
    fireEvent.click(screen.getByTestId('artifacts-fold'));
    await waitFor(() => expect(screen.getByText('Ai Native Ee Oe Deck')).toBeInTheDocument());
  });

  it('AC5: search IGNORES the time window — a match older than 2 weeks appears', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByText('This week')).toBeInTheDocument());
    // "ee-oe" only matches the 40-day-old (Earlier, folded) deck
    fireEvent.change(screen.getByTestId('artifacts-search'), { target: { value: 'ee-oe' } });
    await waitFor(() => expect(screen.getByText('Ai Native Ee Oe Deck')).toBeInTheDocument());
    // and the window chip reads as ignored while searching
    expect(screen.getByTestId('artifacts-window-chip').textContent).toMatch(/忽略|ignore/i);
  });

  it('demoted drawers: the non-primary roles render as collapsible drawers below', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByText('This week')).toBeInTheDocument());
    // Knowledge/Pipeline/Activity are demoted drawers (Deliverables is primary)
    expect(screen.getByTestId('artifacts-drawer-Knowledge')).toBeInTheDocument();
    expect(screen.getByTestId('artifacts-drawer-Pipeline')).toBeInTheDocument();
    expect(screen.getByTestId('artifacts-drawer-Activity')).toBeInTheDocument();
    // a drawer is collapsed by default → its row not shown until toggled
    expect(screen.queryByText('Artifacts data root-fix')).toBeNull();
    fireEvent.click(screen.getByTestId('artifacts-drawer-toggle-Pipeline'));
    await waitFor(() => expect(screen.getByText('Artifacts data root-fix')).toBeInTheDocument());
  });

  it('AC8: a Pipeline row shows the server displayLabel (task name), not the raw run-id', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByText('This week')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('artifacts-drawer-toggle-Pipeline'));
    await waitFor(() => expect(screen.getByText('Artifacts data root-fix')).toBeInTheDocument());
    expect(screen.queryByText('run_2274b401')).toBeNull();
  });

  it('a gitignored primary row surfaces a `local` badge (the blind-spot fix)', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByText('Ai Native Deck')).toBeInTheDocument());
    expect(screen.getByTestId('artifacts-gitignored-badge')).toBeInTheDocument();
  });

  it('clicking a row dispatches swarm:open-file with the path AND calls close()', async () => {
    const onOpen = vi.fn();
    document.addEventListener(OPEN_FILE_EVENT, onOpen as EventListener);
    try {
      renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
      await waitFor(() => expect(screen.getByText('Ai Native Deck')).toBeInTheDocument());
      fireEvent.click(screen.getByText('Ai Native Deck'));
      expect(onOpen).toHaveBeenCalledTimes(1);
      const ev = onOpen.mock.calls[0][0] as CustomEvent;
      expect(ev.detail).toEqual({ path: 'Projects/AIDLC/assets/2026-09-29-ai-native-deck.html' });
      expect(closeSpy).toHaveBeenCalledTimes(1);
    } finally {
      document.removeEventListener(OPEN_FILE_EVENT, onOpen as EventListener);
    }
  });

  it('empty data renders the friendly empty state (no crash)', async () => {
    fetchProducts.mockResolvedValue([]);
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByTestId('artifacts-empty')).toBeInTheDocument());
    expect(screen.getByText(/No products yet/i)).toBeInTheDocument();
  });

  it('search with no match renders the no-match empty state', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} now={NOW} />);
    await waitFor(() => expect(screen.getByText('This week')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('artifacts-search'), { target: { value: 'zzzznomatch' } });
    await waitFor(() => expect(screen.getByTestId('artifacts-empty')).toBeInTheDocument());
    expect(screen.getByText(/No products match/i)).toBeInTheDocument();
  });
});

// ─────────────────────────── star / favorite (run_2b7230be) ───────────────────────────

describe('ArtifactsContent — star/favorite', () => {
  let fetchProducts: ReturnType<typeof vi.fn>;
  let setStarred: ReturnType<typeof vi.fn>;
  let closeSpy: ReturnType<typeof vi.fn>;

  // A fixture with a starred row that is OLD (>14d → Earlier) so the starred view
  // must ignore the time window to show it.
  const STARRED_FIXTURE: Product[] = [
    prod('Projects/AIDLC/assets/2026-09-29-ai-native-deck.html', 'Deliverables', { lastTouched: daysAgo(1) }),
    prod('Knowledge/Library/2026-06-01-old-flagship-deck.html', 'Deliverables', { lastTouched: daysAgo(120), starred: true }),
    prod('Projects/SwarmAI/2-understanding/TECH.md', 'Knowledge', { kind: 'knowledge', lastTouched: daysAgo(3), starred: true }),
  ];

  beforeEach(() => {
    fetchProducts = vi.fn().mockResolvedValue(STARRED_FIXTURE);
    setStarred = vi.fn().mockResolvedValue(undefined);
    closeSpy = vi.fn();
  });

  it('AC5: renders the ★ Starred chip FIRST in the chip row, with the starred count', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} setStarred={setStarred} now={NOW} />);
    // wait for DATA to load (the chip renders during loading with count 0)
    await waitFor(() => expect(screen.getByTestId('artifacts-chip-Starred').textContent).toMatch(/2/));
    const chipRow = screen.getByTestId('artifacts-rolefilter');
    // Starred chip is the FIRST child of the chip row
    expect(chipRow.firstElementChild).toBe(screen.getByTestId('artifacts-chip-Starred'));
  });

  it('AC5: clicking the Starred chip shows ONLY starred rows AND ignores the time window (a 120-day-old starred deck appears)', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} setStarred={setStarred} now={NOW} />);
    await waitFor(() => expect(screen.getByText('Ai Native Deck')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('artifacts-chip-Starred'));
    // the 120-day-old starred deck (Earlier) is now visible — window ignored
    await waitFor(() => expect(screen.getByText('Old Flagship Deck')).toBeInTheDocument());
    // the unstarred recent deck is gone (starred-only)
    expect(screen.queryByText('Ai Native Deck')).toBeNull();
    // the starred Knowledge row (cross-role) also shows
    expect(screen.getByText('TECH')).toBeInTheDocument();
    // window chip reads as ignored
    expect(screen.getByTestId('artifacts-window-chip').textContent).toMatch(/收藏时忽略|ignore/i);
  });

  it('AC5: a per-row star toggle calls setStarred with the negated state and optimistically lights up', async () => {
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} setStarred={setStarred} now={NOW} />);
    await waitFor(() => expect(screen.getByText('Ai Native Deck')).toBeInTheDocument());
    // the recent deck is unstarred → its row star toggles it ON
    const rows = screen.getAllByTestId('artifacts-row');
    const deckRow = rows.find((r) => r.textContent?.includes('Ai Native Deck'))!;
    const star = deckRow.querySelector('[data-testid="artifacts-row-star"]') as HTMLElement;
    fireEvent.click(star);
    expect(setStarred).toHaveBeenCalledWith('default', 'Projects/AIDLC/assets/2026-09-29-ai-native-deck.html', true);
  });

  it('AC5: clicking a row star does NOT also open the file (stopPropagation — RP20)', async () => {
    const onOpen = vi.fn();
    document.addEventListener(OPEN_FILE_EVENT, onOpen as EventListener);
    try {
      renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} setStarred={setStarred} now={NOW} />);
      await waitFor(() => expect(screen.getByText('Ai Native Deck')).toBeInTheDocument());
      const rows = screen.getAllByTestId('artifacts-row');
      const deckRow = rows.find((r) => r.textContent?.includes('Ai Native Deck'))!;
      const star = deckRow.querySelector('[data-testid="artifacts-row-star"]') as HTMLElement;
      fireEvent.click(star);
      expect(onOpen).not.toHaveBeenCalled(); // star click did not open the file
      expect(closeSpy).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener(OPEN_FILE_EVENT, onOpen as EventListener);
    }
  });

  it('AC5: an optimistic star lights immediately, then ROLLS BACK when the endpoint rejects', async () => {
    setStarred = vi.fn().mockRejectedValue(new Error('network down'));
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} setStarred={setStarred} now={NOW} />);
    await waitFor(() => expect(screen.getByText('Ai Native Deck')).toBeInTheDocument());
    const rows = screen.getAllByTestId('artifacts-row');
    const deckRow = rows.find((r) => r.textContent?.includes('Ai Native Deck'))!;
    const star = deckRow.querySelector('[data-testid="artifacts-row-star"]') as HTMLButtonElement;
    // unstarred initially
    expect(star.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(star);
    expect(setStarred).toHaveBeenCalledWith('default', 'Projects/AIDLC/assets/2026-09-29-ai-native-deck.html', true);
    // after the rejection settles, the optimistic patch is rolled back → back to unstarred
    await waitFor(() => {
      const r = screen.getAllByTestId('artifacts-row').find((x) => x.textContent?.includes('Ai Native Deck'))!;
      const s = r.querySelector('[data-testid="artifacts-row-star"]') as HTMLButtonElement;
      expect(s.getAttribute('aria-pressed')).toBe('false');
    });
  });

  it('AC6: Starred view with 0 favorites shows the star guide, not the generic empty state', async () => {
    fetchProducts.mockResolvedValue([
      prod('Projects/AIDLC/assets/deck.html', 'Deliverables', { lastTouched: daysAgo(1) }), // none starred
    ]);
    renderWithClient(<ArtifactsContent close={closeSpy} fetchProducts={fetchProducts} setStarred={setStarred} now={NOW} />);
    await waitFor(() => expect(screen.getByText('Deck')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('artifacts-chip-Starred'));
    await waitFor(() => expect(screen.getByTestId('artifacts-starred-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('artifacts-empty')).toBeNull();
    expect(screen.getByText(/click ☆|favorites/i)).toBeInTheDocument();
  });
});
