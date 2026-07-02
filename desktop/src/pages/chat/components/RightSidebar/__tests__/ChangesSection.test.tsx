/**
 * Tests for ChangesSection (✍ Changes) — Run-B (committed-based badge).
 * Focus: written-only (AC1), NEW/UPD badge from useChangeStatus (AC3),
 * NEW-before-UPD sort (AC5), no repeat-count (AC3-legacy), click→autoDiff (AC4).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChangesSection } from '../ChangesSection';
import type { ChangeStatus } from '../../../../../hooks/useChangeStatus';
import type { ReferencedFile } from '../../../../../hooks/useReferencedFiles';

// Mock the badge source so the component renders deterministically without I/O.
let mockStatus: Map<string, ChangeStatus> = new Map();
vi.mock('../../../../../hooks/useChangeStatus', () => ({
  useChangeStatus: () => mockStatus,
}));

function file(over: Partial<ReferencedFile>): ReferencedFile {
  return { path: 'a.ts', absolutePath: '/x/a.ts', fileName: 'a.ts', operation: 'written', firstSeen: 0, count: 1, ...over };
}

const grouped = (written: ReferencedFile[]) => ({
  written,
  read: [file({ path: 'r.ts', fileName: 'r.ts', operation: 'read' })],
  searched: [file({ path: 's.ts', fileName: 's.ts', operation: 'searched' })],
});

describe('ChangesSection', () => {
  beforeEach(() => { mockStatus = new Map(); });

  it('AC1: renders only written files (read/searched never shown)', () => {
    mockStatus = new Map([['src/new.ts', 'new']]);
    render(<ChangesSection grouped={grouped([file({ path: 'src/new.ts', fileName: 'new.ts' })])} totalCount={3} />);
    expect(screen.getByText('new.ts')).toBeInTheDocument();
    expect(screen.queryByText('r.ts')).not.toBeInTheDocument();
    expect(screen.queryByText('s.ts')).not.toBeInTheDocument();
  });

  it('AC3: NEW badge for untracked, UPD for modified, none for unclassified', () => {
    mockStatus = new Map<string, ChangeStatus>([['src/new.ts', 'new'], ['src/mod.ts', 'upd']]);
    render(<ChangesSection grouped={grouped([
      file({ path: 'src/new.ts', fileName: 'new.ts' }),
      file({ path: 'src/mod.ts', fileName: 'mod.ts' }),
      file({ path: 'src/unknown.ts', fileName: 'unknown.ts' }),
    ])} totalCount={3} />);
    expect(screen.getByText('NEW')).toBeInTheDocument();
    expect(screen.getByText('UPD')).toBeInTheDocument();
    // unknown.ts is still listed (not hidden) but has no badge
    expect(screen.getByText('unknown.ts')).toBeInTheDocument();
  });

  it('AC5: NEW files sort before UPD', () => {
    mockStatus = new Map<string, ChangeStatus>([['src/mod.ts', 'upd'], ['src/new.ts', 'new']]);
    // pass UPD first in input; expect NEW rendered first
    render(<ChangesSection grouped={grouped([
      file({ path: 'src/mod.ts', fileName: 'mod.ts' }),
      file({ path: 'src/new.ts', fileName: 'new.ts' }),
    ])} totalCount={2} />);
    const rows = screen.getAllByText(/\.ts$/).map((el) => el.textContent);
    expect(rows.indexOf('new.ts')).toBeLessThan(rows.indexOf('mod.ts'));
  });

  it('AC3-legacy: no repeat-count shown even when count>1', () => {
    mockStatus = new Map([['src/mod.ts', 'upd']]);
    render(<ChangesSection grouped={grouped([file({ path: 'src/mod.ts', fileName: 'mod.ts', count: 5 })])} totalCount={1} />);
    expect(screen.queryByText('(5)')).not.toBeInTheDocument();
  });

  it('AC4: click dispatches swarm:open-file with autoDiff:true', () => {
    mockStatus = new Map([['src/mod.ts', 'upd']]);
    const spy = vi.spyOn(document, 'dispatchEvent');
    render(<ChangesSection grouped={grouped([file({ path: 'src/mod.ts', fileName: 'mod.ts' })])} totalCount={1} />);
    fireEvent.click(screen.getByText('mod.ts'));
    const evt = spy.mock.calls.map((c) => c[0]).find((e) => (e as CustomEvent).type === 'swarm:open-file') as CustomEvent;
    expect(evt.detail).toEqual({ path: 'src/mod.ts', autoDiff: true });
    spy.mockRestore();
  });

  it('empty written → renders null', () => {
    const { container } = render(<ChangesSection grouped={grouped([])} totalCount={0} />);
    expect(container.firstChild).toBeNull();
  });
});
