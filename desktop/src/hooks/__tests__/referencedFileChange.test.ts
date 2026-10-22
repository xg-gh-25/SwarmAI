/**
 * toReferencedFile — a backend artifact row becomes a rail row; its NEW / UPD badge is
 * the backend's `inHead` (the one source; the rail asks git nothing itself).
 *
 * Invariants: in HEAD → 'upd'; not in HEAD → 'new'; unknown (null / absent) → no badge.
 */
import { describe, it, expect } from 'vitest';
import { toReferencedFile } from '../useReferencedFiles';
import type { SessionArtifact } from '../../services/canvasSession';

const row = (inHead?: boolean | null): SessionArtifact => ({
  path: 'a.md', absolutePath: '/ws/a.md', kind: 'knowledge', role: 'Knowledge', product: true, starred: false,
  displayLabel: 'a.md', firstAt: '2026-10-23T00:00:00Z', lastAt: '2026-10-23T00:00:00Z', writes: 1,
  deleted: false, exists: true, ...(inHead === undefined ? {} : { inHead }),
});

describe('the rail badge comes from inHead', () => {
  it('maps in HEAD / not in HEAD / unknown', () => {
    expect(toReferencedFile(row(true)).change).toBe('upd');
    expect(toReferencedFile(row(false)).change).toBe('new');
    expect(toReferencedFile(row(null)).change).toBeUndefined();
    expect(toReferencedFile(row()).change).toBeUndefined();
  });
});
