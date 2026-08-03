/**
 * FileViewer tabScopeKey — chat-tab scope clearing (run_0fb40bbc, guard fixed run_6619da3b).
 *
 * WHAT THIS FILE TESTS (and, honestly, what it does NOT):
 *
 *  1. Scope-clearing wiring — the `tabScopeKey` prop effect (FileViewer.tsx:179):
 *     when tabScopeKey CHANGES, the internal tab list is cleared (tab A's file
 *     doesn't bleed into tab B); on the FIRST render it must NOT wipe a fresh open.
 *     Observed via the mocked FileViewerTabBar (which renders the internal tab list).
 *
 *  2. No-remount / content-cache survival — the PROPERTY that motivated the fix.
 *     The first cut used key={activeTabId} on the panel, which REMOUNTED FileViewer
 *     on every chat-tab switch → wiped the `contentCache` useRef → re-fetched every
 *     file. The fix passes tabScopeKey as a PROP (no React key), so the SAME
 *     FileViewer instance persists and contentCache survives. The observable that
 *     SEPARATES the two designs is a re-fetch: with the surviving instance, re-opening
 *     an already-loaded path is a cache HIT → NO second `/workspace/file` GET for it.
 *
 * WHY NOT count FileEditorCore mounts (the previous, vacuous approach): FileEditorCore
 * is rendered `key={filePath}` (FileViewer.tsx:441), so switching a.md→b.md remounts
 * it legitimately (count=2) under BOTH the prop design AND the old key-remount — the
 * editor-mount counter cannot tell the designs apart. The content-cache re-fetch can.
 *
 * The remount DECISION itself lives one level up (ChatPage.tsx:3293 / FileViewerPanel
 * pass tabScopeKey as a prop, never a React key). This unit test renders <FileViewer>
 * directly, so it guards the INSTANCE-LEVEL invariant (cache survives a scope change);
 * the "parent must not use key=" invariant is guarded by those call sites' code + the
 * mutation check documented at the bottom of the no-remount test.
 *
 * Heavy children (tab bar, editor core, api, renderers) are mocked to leaf stubs.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import FileViewer from '../FileViewer';

vi.mock('../FileViewerTabBar', () => ({
  default: ({ tabs }: { tabs: Array<{ id: string; fileName: string }> }) => (
    <div data-testid="tab-bar">
      {tabs.map((t) => (
        <span key={t.id} data-testid="fv-tab">{t.fileName}</span>
      ))}
    </div>
  ),
}));
vi.mock('../FileViewerStatusBar', () => ({ default: () => <div data-testid="status-bar" /> }));
vi.mock('../../common/FileEditorCore', () => ({
  default: () => <div data-testid="editor-core" />,
}));

// api.get spy — the content-load effect (FileViewer.tsx:213) calls /workspace/file
// on a cache MISS and short-circuits (no call) on a cache HIT. Counting the GETs
// per path is how we observe contentCache survival across a scope change.
const apiGet = vi.fn(async (url: string, _cfg?: unknown) => {
  if (url === '/workspace/file') return { data: { content: 'x', encoding: 'utf-8', size: 1 } };
  if (url === '/workspace/file/committed') return { data: { content: '' } };
  return { data: {} };
});
vi.mock('../../../services/api', () => ({ default: { get: (...args: unknown[]) => apiGet(...args) } }));

const md = (name: string) => ({ filePath: `/ws/${name}`, fileName: name });

describe('FileViewer tabScopeKey', () => {
  beforeEach(() => { apiGet.mockClear(); });

  it('opens a file into an internal tab, then CLEARS on tabScopeKey change (no bleed)', async () => {
    const { rerender } = render(
      <FileViewer variant="modal" onClose={() => {}} tabScopeKey="tab-A" initialFile={md('a.md')} />,
    );
    expect(await screen.findByText('a.md')).toBeTruthy();
    expect(screen.getAllByTestId('fv-tab')).toHaveLength(1);

    // Switch chat tab → tabScopeKey changes, new tab has its own file b.md.
    rerender(
      <FileViewer variant="modal" onClose={() => {}} tabScopeKey="tab-B" initialFile={md('b.md')} />,
    );
    // a.md must be GONE (cleared), only b.md present — no bleed.
    expect(await screen.findByText('b.md')).toBeTruthy();
    expect(screen.queryByText('a.md')).toBeNull();
    expect(screen.getAllByTestId('fv-tab')).toHaveLength(1);
  });

  it('does NOT clear on the first render (undefined→first scope must not wipe a fresh open)', async () => {
    render(
      <FileViewer variant="modal" onClose={() => {}} tabScopeKey="tab-A" initialFile={md('a.md')} />,
    );
    // The first scope set must not wipe the just-opened file.
    expect(await screen.findByText('a.md')).toBeTruthy();
  });

  it('preserves the content cache across a scope change (proves NO remount)', async () => {
    // Open a.md — one /workspace/file GET for it.
    const { rerender } = render(
      <FileViewer variant="modal" onClose={() => {}} tabScopeKey="tab-A" initialFile={md('a.md')} />,
    );
    expect(await screen.findByText('a.md')).toBeTruthy();

    const getsFor = (path: string) =>
      apiGet.mock.calls.filter(([u, cfg]: any[]) => u === '/workspace/file' && cfg?.params?.path === path).length;
    expect(getsFor('/ws/a.md')).toBe(1);

    // Switch to tab-B (b.md) — clears the tab LIST, but the SAME FileViewer
    // instance keeps its contentCache useRef (a.md's content still cached).
    rerender(
      <FileViewer variant="modal" onClose={() => {}} tabScopeKey="tab-B" initialFile={md('b.md')} />,
    );
    expect(await screen.findByText('b.md')).toBeTruthy();

    // Switch BACK to tab-A, re-opening a.md. The scope-change effect resets
    // prevInitialFileRef (FileViewer.tsx:181-186) so the open effect re-fires —
    // and it hits the SURVIVING cache → NO second GET for a.md.
    rerender(
      <FileViewer variant="modal" onClose={() => {}} tabScopeKey="tab-A" initialFile={md('a.md')} />,
    );
    expect(await screen.findByText('a.md')).toBeTruthy();

    // THE GUARD: still exactly ONE fetch for a.md. A key={tabScopeKey} remount
    // (the reverted design) wipes contentCache → a.md re-fetches → this is 2.
    // Mutation-verified: adding key={tabScopeKey} to the render above makes this
    // assertion RED (getsFor('/ws/a.md') === 2), confirming it is non-vacuous.
    expect(getsFor('/ws/a.md')).toBe(1);
  });
});
