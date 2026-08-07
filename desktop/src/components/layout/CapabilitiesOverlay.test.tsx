/**
 * CapabilitiesOverlay — fail-safe grouping logic (run_b5d98151 §5).
 *
 * These exercise the pure groupSkills/orderedCategories helpers that back the
 * Skills view. The load-bearing invariants (Gate-1 adopted):
 *   • an EMPTY category group (esp. Internal) is NEVER emitted (no void, no crash);
 *   • a skill MISSING category/visibility falls to Utilities/public, never vanishes;
 *   • signature skills are pulled into heroes (not double-rendered as rows);
 *   • Internal is ordered LAST.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { groupSkills, orderedCategories, CapabilitiesContent } from './CapabilitiesOverlay';
import type { Skill, SkillHealthMap } from '../../types';

// jsdom lacks ResizeObserver.
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

const listSkills = vi.fn();
const getHealth = vi.fn();
vi.mock('../../services/skills', () => ({
  skillsService: {
    list: () => listSkills(),
    getHealth: () => getHealth(),
  },
}));
const listAllMcp = vi.fn(() => Promise.resolve([]));
vi.mock('../../services/mcpConfig', () => ({
  mcpConfigService: {
    listAll: () => listAllMcp(),
    updateCatalogEntry: vi.fn(),
    updateDevEntry: vi.fn(),
  },
}));
vi.mock('../../services/api', () => ({
  classifyLoadError: (_e: unknown, ctx: string) => `${ctx} failed`,
}));

function skill(partial: Partial<Skill> & { folderName: string }): Skill {
  return {
    name: partial.folderName.replace(/^s_/, ''),
    description: 'desc',
    version: '1.0.0',
    sourceTier: 'built-in',
    readOnly: true,
    category: 'Utilities',
    visibility: 'public',
    tier: 'lazy',
    ...partial,
  };
}

describe('orderedCategories', () => {
  it('puts Internal last and known categories in canonical order', () => {
    const out = orderedCategories(['Internal', 'Writing', 'Research', 'ZzzUnknown']);
    expect(out[out.length - 1]).toBe('Internal');
    expect(out.indexOf('Research')).toBeLessThan(out.indexOf('Writing'));
    // unknown category sorts after known ones but before Internal
    expect(out.indexOf('ZzzUnknown')).toBeLessThan(out.indexOf('Internal'));
  });
});

describe('groupSkills — fail-safe (§5)', () => {
  it('never emits an empty group when NO internal skills are present', () => {
    const skills = [
      skill({ folderName: 's_deep-research', category: 'Research' }),
      skill({ folderName: 's_narrative-writing', category: 'Writing' }),
    ];
    const { groups } = groupSkills(skills);
    const cats = groups.map(([c]) => c);
    expect(cats).not.toContain('Internal'); // no internal → no Internal group, no void
    expect(groups.every(([, list]) => list.length > 0)).toBe(true);
  });

  it('renders an Internal group only when >=1 internal skill exists', () => {
    const skills = [
      skill({ folderName: 's_deep-research', category: 'Research' }),
      skill({ folderName: 's_cmhk-weekly-report', category: 'Internal', visibility: 'internal' }),
    ];
    const { groups } = groupSkills(skills);
    expect(groups.map(([c]) => c)).toContain('Internal');
  });

  it('a skill missing category falls to Utilities, never vanishes', () => {
    // simulate a defensive undefined category (service defaults to Utilities, but guard anyway)
    const s = skill({ folderName: 's_mystery' });
    // @ts-expect-error — force the missing-field path
    s.category = undefined;
    const { groups } = groupSkills([s]);
    const all = groups.flatMap(([, list]) => list);
    expect(all.map((x) => x.folderName)).toContain('s_mystery');
    expect(groups.map(([c]) => c)).toContain('Utilities');
  });

  it('pulls signature skills into heroes, not rows', () => {
    const skills = [
      skill({ folderName: 's_autonomous-pipeline', category: 'Automation' }),
      skill({ folderName: 's_pollinate', category: 'Content' }),
      skill({ folderName: 's_deep-research', category: 'Research' }),
    ];
    const { heroes, groups } = groupSkills(skills);
    expect(heroes.map((h) => h.folderName).sort()).toEqual(['s_autonomous-pipeline', 's_pollinate']);
    // heroes must NOT also appear as rows
    const rowFolders = groups.flatMap(([, list]) => list.map((s) => s.folderName));
    expect(rowFolders).not.toContain('s_autonomous-pipeline');
    expect(rowFolders).toContain('s_deep-research');
  });

  it('empty input produces no groups and no heroes (no throw)', () => {
    const { heroes, groups } = groupSkills([]);
    expect(heroes).toEqual([]);
    expect(groups).toEqual([]);
  });
});

describe('CapabilitiesContent — health dot (lazy + fail-safe) + tier marker (run_a85e6641)', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const twoSkills: Skill[] = [
    skill({ folderName: 's_deep-research', category: 'Research', tier: 'always' }),
    skill({ folderName: 's_narrative-writing', category: 'Writing', tier: 'lazy' }),
  ];

  it('renders a health dot per skill row once /skills/health resolves (lazy)', async () => {
    listSkills.mockResolvedValue(twoSkills);
    // Health map is keyed by the exact folderName the backend returns (canonicalized
    // server-side); the frontend just looks up by folderName. No underscore/hyphen dual key.
    const health: SkillHealthMap = {
      's_deep-research': { status: 'healthy', success_rate: 0.9, last_used: '2026-08-06' },
      's_narrative-writing': { status: 'never_used', success_rate: null, last_used: null },
    };
    getHealth.mockResolvedValue(health);

    render(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);

    // Card renders first (from list()), independent of health.
    await waitFor(() => expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy());
    // Health line's status resolves after the lazy fetch (the line's dot carries data-status).
    await waitFor(() => {
      expect(screen.getByTestId('cap-healthline-s_deep-research').getAttribute('data-status')).toBe('healthy');
      expect(screen.getByTestId('cap-healthline-s_narrative-writing').getAttribute('data-status')).toBe('never_used');
    });
  });

  it('FAIL-SAFE: health fetch REJECTS → cards still render, NO perpetual "loading" line, no crash', async () => {
    listSkills.mockResolvedValue(twoSkills);
    getHealth.mockRejectedValue(new Error('network down'));

    render(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);

    // The skill cards are fully usable even though health failed.
    await waitFor(() => expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy());
    await waitFor(() => expect(getHealth).toHaveBeenCalled());
    // Once SETTLED with no data, the health line renders NOTHING — never a perpetual
    // "loading health…" (adversarial HIGH: reject/empty must not leave cards stuck loading).
    await waitFor(() => expect(screen.queryByText(/loading health/i)).toBeNull());
    expect(screen.queryByTestId('cap-healthline-s_deep-research')).toBeNull();
    // Card still present + clickable.
    expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy();
  });

  it('first-run: empty health map ({}) → cards render, NO grey wall of "loading" lines', async () => {
    listSkills.mockResolvedValue(twoSkills);
    getHealth.mockResolvedValue({} as SkillHealthMap); // backend empty-table guard returns {}
    render(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy());
    await waitFor(() => expect(getHealth).toHaveBeenCalled());
    // Settled-empty → no health lines at all (not a wall of perpetual loaders).
    await waitFor(() => expect(screen.queryByText(/loading health/i)).toBeNull());
    expect(screen.queryByTestId('cap-healthline-s_deep-research')).toBeNull();
  });

  it('renders a faint tier marker per row (always/lazy)', async () => {
    listSkills.mockResolvedValue(twoSkills);
    getHealth.mockResolvedValue({});
    render(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('cap-tier-s_deep-research')).toBeTruthy());
    expect(screen.getByTestId('cap-tier-s_deep-research').getAttribute('data-tier')).toBe('always');
    expect(screen.getByTestId('cap-tier-s_narrative-writing').getAttribute('data-tier')).toBe('lazy');
  });

  it('shows a full health LINE on each card by default: status · X% success · last used DATE', async () => {
    listSkills.mockResolvedValue(twoSkills);
    getHealth.mockResolvedValue({
      's_deep-research': { status: 'healthy', success_rate: 0.92, last_used: '2026-08-06' },
      's_narrative-writing': { status: 'never_used', success_rate: null, last_used: null },
    } as SkillHealthMap);
    render(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);
    // The health line is ON the card (not a drawer) and carries success% + last-used.
    await waitFor(() => {
      const line = screen.getByTestId('cap-healthline-s_deep-research');
      expect(line.textContent).toContain('92% success');
      expect(line.textContent).toContain('2026-08-06');
    });
    // never_used card shows the status but no fabricated %/date.
    const nu = screen.getByTestId('cap-healthline-s_narrative-writing');
    expect(nu.textContent?.toLowerCase()).toContain('never used');
    expect(nu.textContent).not.toContain('% success');
  });

  it('health line is LAZY but the CARD renders immediately (line appears after health resolves)', async () => {
    listSkills.mockResolvedValue(twoSkills);
    let resolveHealth: (v: SkillHealthMap) => void = () => {};
    getHealth.mockReturnValue(new Promise<SkillHealthMap>((r) => { resolveHealth = r; }));
    render(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);
    // Card is present before health resolves.
    await waitFor(() => expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy());
    // Health line not yet populated (lazy) — placeholder present, no % yet.
    expect(screen.queryByText(/92% success/)).toBeNull();
    // Resolve health → line populates ON the card.
    resolveHealth({ 's_deep-research': { status: 'healthy', success_rate: 0.92, last_used: '2026-08-06' } } as SkillHealthMap);
    await waitFor(() => expect(screen.getByTestId('cap-healthline-s_deep-research').textContent).toContain('92% success'));
  });
});
