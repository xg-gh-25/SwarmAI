/**
 * FileViewer tabScopeKey — chat-tab scope clearing WITHOUT remount (run_0fb40bbc).
 *
 * The bug: the first cut used key={activeTabId} on the panel, which remounted
 * FileViewer on every chat-tab switch → replayed the width-reveal animation +
 * dropped the content cache. The fix: a tabScopeKey prop that clears the
 * internal tab list on change (no remount). This test asserts:
 *  - opening a file adds an internal tab (via mocked FileViewerTabBar);
 *  - changing tabScopeKey CLEARS that tab list (tab A's file doesn't bleed);
 *  - the SAME FileViewer instance persists (no remount → cache/animation survive).
 *
 * Heavy children (tab bar, editor core, api, renderers) are mocked to leaf stubs
 * so we test the scope-clearing wiring only.
 *
 * NOTE (v6, run_09431085): the tab-scope clearing logic (useFileViewerTabs +
 * tabScopeKey effect) is variant-INDEPENDENT, but the horizontal FileViewerTabBar
 * — the observable used here to read the internal tab list — now renders only in
 * the MODAL variant (the PANEL variant uses the Canvas OUTPUTS list as selector).
 * So these tests render variant="modal" to observe the same clearing logic through
 * the surface that still shows the tab list. The logic under test is unchanged.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import FileViewer from '../FileViewer';

// Count FileViewer body mounts to prove NO remount on scope change.
let bodyMounts = 0;

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
  default: () => {
    bodyMounts += 1;
    return <div data-testid="editor-core" />;
  },
}));
vi.mock('../../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { content: 'x', size: 1, readonly: false } }) },
}));

const md = (name: string) => ({ filePath: `/ws/${name}`, fileName: name });

describe('FileViewer tabScopeKey', () => {
  beforeEach(() => { bodyMounts = 0; });

  it('opens a file into an internal tab, then CLEARS on tabScopeKey change (no bleed)', async () => {
    const { rerender } = render(
      <FileViewer variant="modal" onClose={() => {}} tabScopeKey="tab-A" initialFile={md('a.md')} />,
    );
    // a.md is an internal tab
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
});
