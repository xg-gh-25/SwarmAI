import type React from 'react';
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
import { screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { groupSkills, orderedCategories, mostUsed, byFrequencyThenName, CapabilitiesContent } from './CapabilitiesOverlay';
import type { Skill, SkillHealthMap, ExpertsByDomain } from '../../types';
import type { AppsCatalog } from '../../services/apps';
import { QueryClient as __QC, QueryClientProvider as __QCP } from '@tanstack/react-query';
import { render as __render } from '@testing-library/react';
// The overlay reads its data through TanStack Query (overlayQueries): a fresh client
// per render, so no cache carries over between tests.
const renderQ = (ui: React.ReactElement) =>
  __render(<__QCP client={new __QC({ defaultOptions: { queries: { retry: false } } })}>{ui}</__QCP>);

// Health fixture builder — invocation_count is now required (run_ff4adc88).
function h(status: SkillHealthMap[string]['status'], invocation_count: number | null, success_rate: number | null = null, last_used: string | null = null): SkillHealthMap[string] {
  return { status, success_rate, last_used, invocation_count };
}

// jsdom lacks ResizeObserver.
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

const listSkills = vi.fn();
const getHealth = vi.fn();
// Experts (R2): default to empty so the many skills-focused tests don't need to stub it;
// the Experts-view tests below set a real grouped payload.
const listExperts = vi.fn(() => Promise.resolve({}));
vi.mock('../../services/skills', () => ({
  skillsService: {
    list: () => listSkills(),
    getHealth: () => getHealth(),
  },
}));
vi.mock('../../services/experts', () => ({
  expertsService: { list: () => listExperts() },
}));
const listApps = vi.fn<() => Promise<AppsCatalog>>(() => Promise.resolve({
  schemaVersion: 1,
  hostContractVersion: '1',
  currentProfile: 'desktop-daemon',
  apps: [],
}));
vi.mock('../../services/apps', () => ({
  appsService: { list: () => listApps() },
}));
vi.mock('../../generated/appBindings', () => ({
  BUILTIN_APP_BINDING_DIGEST: 'b'.repeat(64),
  BUILTIN_APP_SURFACE_IMPORTS: {},
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

function renderSkills(): void {
  renderQ(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);
  fireEvent.click(screen.getByTestId('cap-view-skills'));
}

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

  it('sorts WITHIN a group ALPHABETICALLY, NOT by frequency (findability > usage)', () => {
    // run_54491b88: groups answer "where do I find X" → predictable alphabetical position
    // that does NOT shift with usage. Frequency ranking lives ONLY in the Most-Used strip
    // ("what do I use"). groupSkills is health-agnostic by design — it takes no health arg,
    // so a heavily-used skill can NEVER jump its alphabetical slot in the group.
    const skills = [
      skill({ folderName: 's_zebra', category: 'Research' }),
      skill({ folderName: 's_alpha', category: 'Research' }),
    ];
    const { groups } = groupSkills(skills);
    const research = groups.find(([c]) => c === 'Research')![1];
    expect(research.map((s) => s.name)).toEqual(['alpha', 'zebra']);
  });
});

describe('CapabilitiesContent — Apps catalog-only default (Phase A)', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    listApps.mockReset();
    listApps.mockResolvedValue({
      schemaVersion: 1, hostContractVersion: '1', currentProfile: 'desktop-daemon', apps: [],
    });
  });

  it('opens on Apps and renders Pollinate without lifecycle controls', async () => {
    listApps.mockResolvedValue({
      schemaVersion: 1,
      hostContractVersion: '1',
      currentProfile: 'desktop-daemon',
      apps: [{
        appId: 'pollinate', displayName: 'Pollinate', description: 'Media package',
        version: '1.0.0', instanceMode: 'singleton', sourceTier: 'built-in',
        evidenceState: 'catalog-only', lifecycleControls: [],
        packageReleaseDigest: 'a'.repeat(64), executionReleaseDigest: null,
        bindingMapDigest: 'b'.repeat(64), proofBlockers: ['proof-not-run'],
        profileAccounting: [{ profile: 'desktop-daemon', contractVersion: '1',
          capabilityDigest: 'c'.repeat(64), supported: true, blockedResources: [] }],
        resourceCount: 4, upstreamSource: null,
      }],
    });
    renderQ(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);
    const card = await screen.findByTestId('cap-app-pollinate');
    expect(screen.getByTestId('cap-view-apps').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('cap-app-state-pollinate').textContent).toContain('catalog-only');
    expect(screen.getByTestId('cap-app-profile-pollinate').textContent).toContain('compatible');
    expect(card.querySelector('button')).toBeNull();
    expect(listSkills).not.toHaveBeenCalled();
  });

  it('blocks compatibility when API and compiled binding digests differ', async () => {
    listApps.mockResolvedValue({
      schemaVersion: 1, hostContractVersion: '1', currentProfile: 'desktop-daemon',
      apps: [{
        appId: 'pollinate', displayName: 'Pollinate', description: 'Media', version: '1.0.0',
        instanceMode: 'singleton', sourceTier: 'built-in', evidenceState: 'catalog-only',
        lifecycleControls: [], packageReleaseDigest: 'a'.repeat(64), executionReleaseDigest: null,
        bindingMapDigest: 'x'.repeat(64), proofBlockers: ['proof-not-run'],
        resourceCount: 4, upstreamSource: null,
        profileAccounting: [{ profile: 'desktop-daemon', contractVersion: '1',
          capabilityDigest: 'c'.repeat(64), supported: true, blockedResources: [] }],
      }],
    });
    renderQ(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);
    expect(await screen.findByTestId('cap-app-profile-pollinate')).toHaveTextContent('compiled bindings mismatch');
  });
  it('renders verified upstream attribution for an external App', async () => {
    listApps.mockResolvedValue({
      schemaVersion: 1, hostContractVersion: '1', currentProfile: 'desktop-daemon',
      apps: [{
        appId: 'research', displayName: 'Research', description: 'Research', version: '1.0.0',
        instanceMode: 'singleton', sourceTier: 'external', evidenceState: 'catalog-only',
        lifecycleControls: [], packageReleaseDigest: 'a'.repeat(64), executionReleaseDigest: null,
        bindingMapDigest: 'b'.repeat(64), proofBlockers: ['proof-not-run'], resourceCount: 1,
        upstreamSource: { sources: [{ id: 'mattpocock-skills',
          repository: 'https://github.com/mattpocock/skills', commit: 'c'.repeat(40), license: 'MIT' }] },
        profileAccounting: [{ profile: 'desktop-daemon', contractVersion: '1',
          capabilityDigest: 'c'.repeat(64), supported: true, blockedResources: [] }],
      }],
    });
    renderQ(<CapabilitiesContent onDispatch={() => true} close={() => {}} />);
    const source = await screen.findByTestId('cap-app-upstream-research');
    expect(source).toHaveTextContent('github.com/mattpocock/skills');
    expect(source).toHaveTextContent('(MIT)');
    expect(screen.getByTestId('cap-app-research').querySelector('button')).toBeNull();
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

  it('a failed skills load shows the error with a Retry that fetches again (OverlayState)', async () => {
    listSkills.mockRejectedValueOnce(new Error('down')).mockResolvedValue(twoSkills);
    getHealth.mockResolvedValue({});
    renderSkills();
    expect(await screen.findByTestId('cap-error')).toHaveTextContent('Capabilities failed');
    fireEvent.click(screen.getByTestId('cap-retry'));
    await waitFor(() => expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy());
    expect(listSkills).toHaveBeenCalledTimes(2);
  });

  it('renders a health dot per skill row once /skills/health resolves (lazy)', async () => {
    listSkills.mockResolvedValue(twoSkills);
    // Health map is keyed by the exact folderName the backend returns (canonicalized
    // server-side); the frontend just looks up by folderName. No underscore/hyphen dual key.
    const health: SkillHealthMap = {
      's_deep-research': h('healthy', 12, 0.9, '2026-08-06'),
      's_narrative-writing': h('never_used', null),
    };
    getHealth.mockResolvedValue(health);

    renderSkills();

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

    renderSkills();

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
    renderSkills();
    await waitFor(() => expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy());
    await waitFor(() => expect(getHealth).toHaveBeenCalled());
    // Settled-empty → no health lines at all (not a wall of perpetual loaders).
    await waitFor(() => expect(screen.queryByText(/loading health/i)).toBeNull());
    expect(screen.queryByTestId('cap-healthline-s_deep-research')).toBeNull();
  });

  it('renders a faint tier marker per row (always/lazy)', async () => {
    listSkills.mockResolvedValue(twoSkills);
    getHealth.mockResolvedValue({});
    renderSkills();
    await waitFor(() => expect(screen.getByTestId('cap-tier-s_deep-research')).toBeTruthy());
    expect(screen.getByTestId('cap-tier-s_deep-research').getAttribute('data-tier')).toBe('always');
    expect(screen.getByTestId('cap-tier-s_narrative-writing').getAttribute('data-tier')).toBe('lazy');
  });

  it('shows a full health LINE on each card by default: status · X% success · last used DATE', async () => {
    listSkills.mockResolvedValue(twoSkills);
    getHealth.mockResolvedValue({
      's_deep-research': h('healthy', 12, 0.92, '2026-08-06'),
      's_narrative-writing': h('never_used', null),
    } as SkillHealthMap);
    renderSkills();
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
    renderSkills();
    // Card is present before health resolves.
    await waitFor(() => expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy());
    // Health line not yet populated (lazy) — placeholder present, no % yet.
    expect(screen.queryByText(/92% success/)).toBeNull();
    // Resolve health → line populates ON the card.
    resolveHealth({ 's_deep-research': h('healthy', 12, 0.92, '2026-08-06') } as SkillHealthMap);
    await waitFor(() => expect(screen.getByTestId('cap-healthline-s_deep-research').textContent).toContain('92% success'));
  });
});

describe('CapabilitiesContent — Most-Used strip + noise reduction (AC2/AC4/AC5 render)', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  const threeSkills: Skill[] = [
    skill({ folderName: 's_deep-research', category: 'Research' }),
    skill({ folderName: 's_narrative-writing', category: 'Writing' }),
    skill({ folderName: 's_summarize', category: 'Research' }),
  ];

  it('renders the Most-Used strip once health has data (AC2)', async () => {
    listSkills.mockResolvedValue(threeSkills);
    getHealth.mockResolvedValue({
      's_deep-research': h('healthy', 50, 0.9, '2026-08-06'),
      's_summarize': h('healthy', 20, 0.8, '2026-08-05'),
      's_narrative-writing': h('never_used', null),
    } as SkillHealthMap);
    renderSkills();
    await waitFor(() => expect(screen.getByTestId('cap-most-used')).toBeTruthy());
    // Most-used strip contains the high-frequency skills (never_used excluded).
    const strip = screen.getByTestId('cap-most-used');
    expect(strip.textContent).toContain('deep-research');
  });

  it('AC5 fail-safe: strip ABSENT before health settles + when health empty', async () => {
    listSkills.mockResolvedValue(threeSkills);
    getHealth.mockResolvedValue({} as SkillHealthMap);
    renderSkills();
    await waitFor(() => expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy());
    await waitFor(() => expect(getHealth).toHaveBeenCalled());
    // Empty health → no strip (no flash of mis-ranked/dead skills), list still renders.
    expect(screen.queryByTestId('cap-most-used')).toBeNull();
    expect(screen.getByTestId('cap-skill-s_deep-research')).toBeTruthy();
  });

  it('AC4: per-group count header removed (no bare number beside the category name)', async () => {
    listSkills.mockResolvedValue(threeSkills);
    getHealth.mockResolvedValue({} as SkillHealthMap);
    renderSkills();
    await waitFor(() => expect(screen.getByTestId('cap-group-Research')).toBeTruthy());
    // The group header is just the category name — no count span. The old markup put a
    // bare "{list.length}" beside it; assert no lone digit text node in the header.
    const header = screen.getByTestId('cap-group-Research').querySelector('h2');
    expect(header?.textContent?.trim()).toBe('Research');
  });
});

describe('byFrequencyThenName — within-group sort (AC3, Gate-1 #4 tiebreak)', () => {
  const sk = (folderName: string) => skill({ folderName });
  it('sorts by invocation_count DESC, breaking ties by name ASC', () => {
    const health: SkillHealthMap = {
      s_a: h('healthy', 10), s_b: h('healthy', 50), s_c: h('healthy', 10),
    };
    const sorted = [sk('s_a'), sk('s_b'), sk('s_c')].sort(byFrequencyThenName(health));
    // s_b (50) first; s_a & s_c tie at 10 → name asc → a before c
    expect(sorted.map((s) => s.folderName)).toEqual(['s_b', 's_a', 's_c']);
  });
  it('sinks never_used / no-data (null count) BELOW any used skill', () => {
    const health: SkillHealthMap = {
      s_used: h('healthy', 1), s_never: h('never_used', null),
    };
    const sorted = [sk('s_never'), sk('s_used')].sort(byFrequencyThenName(health));
    expect(sorted.map((s) => s.folderName)).toEqual(['s_used', 's_never']);
  });
  it('EMPTY health ({}) falls back to pure name ASC (fail-safe before health settles)', () => {
    // Every freq is absent → all tie → name asc. This is the SAME order as the old
    // alphabetical sort, so before health loads the list is deterministic, no jitter.
    const sorted = [sk('s_c'), sk('s_a'), sk('s_b')].sort(byFrequencyThenName({}));
    expect(sorted.map((s) => s.folderName)).toEqual(['s_a', 's_b', 's_c']);
  });
});

describe('mostUsed — Most-Used strip membership (AC2)', () => {
  const sk = (folderName: string) => skill({ folderName });
  it('returns Top-N by invocation_count, excluding heroes and never_used/no-data', () => {
    const skills = [
      sk('s_autonomous-pipeline'), // hero — excluded even if high
      sk('s_a'), sk('s_b'), sk('s_c'), sk('s_never'),
    ];
    const health: SkillHealthMap = {
      's_autonomous-pipeline': h('healthy', 999),
      s_a: h('healthy', 30), s_b: h('healthy', 50), s_c: h('healthy', 10),
      s_never: h('never_used', null),
    };
    const top = mostUsed(skills, health, 8);
    // heroes + never_used excluded; sorted desc by count
    expect(top.map((s) => s.folderName)).toEqual(['s_b', 's_a', 's_c']);
  });
  it('caps at N', () => {
    const skills = Array.from({ length: 12 }, (_, i) => sk(`s_${i}`));
    const health: SkillHealthMap = Object.fromEntries(skills.map((s, i) => [s.folderName, h('healthy', i + 1)]));
    expect(mostUsed(skills, health, 8)).toHaveLength(8);
  });
  it('EMPTY health → empty strip (no flash of dead/mis-ranked skills before health settles)', () => {
    const skills = [sk('s_a'), sk('s_b')];
    expect(mostUsed(skills, {}, 8)).toEqual([]);
  });
});

// Connections view — registry-honest labels + tier marker (run_3989a574). This panel
// is a CONFIG REGISTRY (enabled = available for a session), NOT a session-liveness probe.
// These lock: (a) the label says ENABLED/available, never CONNECTED/live; (b) each row
// shows its load-timing tier marker; (c) no session-status is claimed.
describe('CapabilitiesContent — Connections registry honesty (run_3989a574)', () => {
  const mcp = (partial: Partial<import('../../services/mcpConfig').ConfigEntry> & { id: string }) => ({
    name: partial.id,
    description: 'a tool',
    connectionType: 'stdio' as const,
    config: {},
    enabled: true,
    tier: 'always' as const,
    layer: 'dev' as const,
    ...partial,
  });

  afterEach(() => { listAllMcp.mockReset(); listAllMcp.mockReturnValue(Promise.resolve([])); });

  async function renderConnections(mcps: unknown[]) {
    listSkills.mockResolvedValue([]);
    getHealth.mockResolvedValue({} as SkillHealthMap);
    listAllMcp.mockReturnValue(Promise.resolve(mcps));
    renderSkills();
    await waitFor(() => expect(screen.getByTestId('cap-view-connections')).toBeTruthy());
    fireEvent.click(screen.getByTestId('cap-view-connections'));
    await waitFor(() => expect(screen.getByTestId('cap-connections-view')).toBeTruthy());
  }

  it('labels an enabled MCP ENABLED (never CONNECTED/live)', async () => {
    await renderConnections([mcp({ id: 'slack-mcp', enabled: true, tier: 'always' })]);
    // The honest status word is ENABLED — the old CONNECTED/LIVE claim must be gone.
    expect(screen.getByText('ENABLED')).toBeTruthy();
    expect(screen.queryByText('CONNECTED')).toBeNull();
    expect(screen.queryByText(/\blive\b/i)).toBeNull();
    // The dot reflects enabled/off, not a connection state.
    const dot = screen.getByTestId('cap-conn-dot-slack-mcp');
    expect(dot.getAttribute('data-status')).toBe('enabled');
  });

  it('shows the load-timing tier marker per row (always ⚡ / ondemand 💤 / channel 📡)', async () => {
    await renderConnections([
      mcp({ id: 'always-mcp', tier: 'always' }),
      mcp({ id: 'ondemand-mcp', tier: 'ondemand' }),
      mcp({ id: 'channel-mcp', tier: 'channel' }),
    ]);
    expect(screen.getByTestId('cap-conn-tier-always-mcp').getAttribute('data-tier')).toBe('always');
    expect(screen.getByTestId('cap-conn-tier-ondemand-mcp').getAttribute('data-tier')).toBe('ondemand');
    expect(screen.getByTestId('cap-conn-tier-channel-mcp').getAttribute('data-tier')).toBe('channel');
    // marker icons render (faint, but present)
    expect(screen.getByTestId('cap-conn-tier-always-mcp').textContent).toBe('bolt');
    expect(screen.getByTestId('cap-conn-tier-ondemand-mcp').textContent).toBe('bedtime');
  });
});

describe('CapabilitiesContent — Experts view (R2)', () => {
  afterEach(() => {
    cleanup();
    listExperts.mockReset();
    listExperts.mockResolvedValue({});
    listSkills.mockResolvedValue([]);
    getHealth.mockResolvedValue({});
  });

  const grouped: ExpertsByDomain = {
    code: [
      { expertId: 'correctness', displayNameZh: '正确性评审官', displayNameEn: 'Correctness Auditor', domain: 'code', scope: '逻辑、边界条件、错误处理', visibility: 'public', isValid: true },
      { expertId: 'security', displayNameZh: '安全评审官', displayNameEn: 'Security Auditor', domain: 'code', scope: '鉴权、注入、密钥', visibility: 'public', isValid: true },
    ],
  };

  it('switching to Experts tab renders experts grouped by domain with name + scope', async () => {
    listSkills.mockResolvedValue([]);
    getHealth.mockResolvedValue({});
    listExperts.mockResolvedValue(grouped);
    renderSkills();
    fireEvent.click(screen.getByTestId('cap-view-experts'));
    await waitFor(() => expect(screen.getByTestId('cap-expert-group-code')).toBeTruthy());
    const card = screen.getByTestId('cap-expert-correctness');
    expect(card.textContent).toContain('Correctness Auditor');
    expect(card.textContent).toContain('逻辑、边界条件、错误处理'); // scope = the decision line
  });

  it('health signal: a valid expert shows ready, a malformed one shows malformed (the ONE standout)', async () => {
    listExperts.mockResolvedValue({
      code: [
        { expertId: 'good', displayNameZh: '好', displayNameEn: 'Good', domain: 'code', scope: 's', visibility: 'public', isValid: true },
        { expertId: 'bad', displayNameZh: '坏', displayNameEn: 'Bad', domain: 'code', scope: 's', visibility: 'public', isValid: false },
      ],
    });
    renderSkills();
    fireEvent.click(screen.getByTestId('cap-view-experts'));
    await waitFor(() => expect(screen.getByTestId('cap-expert-health-good')).toBeTruthy());
    expect(screen.getByTestId('cap-expert-health-good').getAttribute('data-valid')).toBe('true');
    expect(screen.getByTestId('cap-expert-health-good').textContent).toContain('ready');
    expect(screen.getByTestId('cap-expert-health-bad').getAttribute('data-valid')).toBe('false');
    expect(screen.getByTestId('cap-expert-health-bad').textContent).toContain('malformed');
  });

  it('FAIL-SAFE: experts fetch REJECTS → empty state, no crash, skills view still works', async () => {
    listSkills.mockResolvedValue([]);
    getHealth.mockResolvedValue({});
    listExperts.mockRejectedValue(new Error('network down'));
    renderSkills();
    fireEvent.click(screen.getByTestId('cap-view-experts'));
    await waitFor(() => expect(screen.getByTestId('cap-experts-empty')).toBeTruthy());
  });
});
