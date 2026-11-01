/**
 * HiveFleetOverlay — the left-nav "Hive" workbench (SYSTEM zone). A structural mirror
 * of JobsRunsOverlay, for SwarmAI's Hive fleet: remote AI clones deployed to your own
 * AWS (100% data sovereignty, one 24/7 stand-in per teammate). This elevates Hive from
 * a buried Settings tab to a first-class fullscreen surface (run_b450108e).
 *
 * Opens via the nav-hive card → openOverlay('hive') (OverlayHost). Nav-card-only:
 * deliberately ABSENT from ALL_SHOW_EVENTS — the agent cannot open it (it controls AWS
 * credentials + live cloud infra; same security boundary as library/settings/eval).
 *
 * Two views inside the host panel (Fleet | Accounts):
 *   • FLEET — overview strip (running/provisioning/stopped/error counts) + My/Shared
 *     Hive sections (reusing HiveSection/InstanceCard/DeployProgress/AuthDisplay), with
 *     a Deploy New Hive launcher. Polls listInstances every 5s ONLY while an instance
 *     is transitional (mirrors the former HiveTab poll) — no idle polling.
 *   • ACCOUNTS — the AWS-account roster (AccountCard) + Add Account, the same surface
 *     the slim Settings Hive tab shows (shared components, no drift).
 *
 * NO chat dispatch (unlike JobsRunsOverlay): every Hive mutation (deploy/stop/start/
 * update/retry/delete/credentials) calls hiveService directly — synchronous API, not a
 * yaml-owned skill — so this content takes only `close`, no `onDispatch` (Gate-1 D5:
 * copying the dispatchPrompt bridge from the Jobs template would be dead plumbing).
 *
 * Local state ONLY — never MessageStore / active-tab mutation (OT01 safety).
 *
 * @exports HiveFleetContent
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { HiveAccount, HiveInstance, TRANSITIONAL_STATUSES, MAX_HIVES, countedHives, hiveAccountView } from '../../services/hive';
import { overlayQuery, invalidateOverlay } from './overlayQueries';
import { OverlayError } from './OverlayState';
import { classifyLoadError } from '../../services/api';
import { HiveSection, AccountCard, AddAccountDialog, DeployHiveDialog } from '../settings/hiveComponents';
import { WorkbenchToolbar } from './overlayShell';

export interface HiveFleetContentProps {
  /** Host-owned close (kept for symmetry with sibling overlays; the Fleet surface
   *  mutates via hiveService directly and has no dispatch-then-close path). */
  close: () => void;
}

type ViewMode = 'fleet' | 'accounts';

export function HiveFleetContent({ close: _close }: HiveFleetContentProps) {
  const { t } = useTranslation();
  const [view, setView] = useState<ViewMode>('fleet');
  const [showAddAccount, setShowAddAccount] = useState(false);
  const [showDeploy, setShowDeploy] = useState(false);
  // Accounts + instances through the shared overlay queries (overlayQueries), settled
  // independently (a failure on one keeps the other on screen). Instances poll every
  // 5s ONLY while one is transitional (deploy/stop in flight) — no idle polling; the
  // always-mounted idle cost lives on the nav card's own gated poll.
  const qc = useQueryClient();
  const accountsQ = useQuery(overlayQuery.hiveAccounts());
  const instancesQ = useQuery({
    ...overlayQuery.hiveInstances(),
    refetchInterval: (q) =>
      (q.state.data ?? []).some((i) => TRANSITIONAL_STATUSES.includes(i.status)) ? 5000 : false,
  });
  const accounts: HiveAccount[] = accountsQ.data ?? [];
  const instances: HiveInstance[] = instancesQ.data ?? [];
  const loading = accountsQ.isPending || instancesQ.isPending;
  const refresh = useCallback(() => invalidateOverlay(qc, 'hive'), [qc]);

  const view_ = hiveAccountView(accounts);
  const deployable = view_.deployable;
  const limitReached = countedHives(instances) >= MAX_HIVES;
  const deployBlocked = deployable.length === 0 ? t('ui.HiveFleetOverlay.addAccountFirst')
    : limitReached ? t('ui.HiveFleetOverlay.limitReached', { max: MAX_HIVES }) : null;

  const myHives = instances.filter((i) => i.hiveType === 'my');
  const sharedHives = instances.filter((i) => i.hiveType !== 'my');

  return (
    <div className="flex-1 min-h-0 flex flex-col relative" data-testid="hive-overlay">
      <WorkbenchToolbar
        gap={1}
        loading={loading}
        left={(['fleet', 'accounts'] as ViewMode[]).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            role="tab"
            aria-selected={view === v}
            data-testid={`hive-view-${v}`}
            className={`px-3 py-1 text-small rounded-md transition-colors ${
              view === v ? 'bg-primary/15 text-primary' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
            }`}
          >
            {v === 'fleet' ? t('ui.HiveFleetOverlay.tabFleet') : t('ui.HiveFleetOverlay.tabAccounts')}
          </button>
        ))}
        right={view === 'fleet' ? (
          <button
            onClick={() => setShowDeploy(true)}
            disabled={deployBlocked !== null}
            title={deployBlocked ?? t('ui.HiveFleetOverlay.deployNew')}
            aria-label={deployBlocked ? t('ui.HiveFleetOverlay.deployDisabled', { reason: deployBlocked }) : t('ui.HiveFleetOverlay.deployHive')}
            data-testid="hive-deploy-btn"
            className="btn-sm btn-soft transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <span className="material-symbols-outlined icon-xs">add</span>{t('ui.HiveFleetOverlay.deployHive')}
          </button>
        ) : view_.canAdd ? (
          <button
            onClick={() => setShowAddAccount(true)}
            data-testid="hive-add-account-btn"
            className="btn-sm btn-soft transition-colors"
          >
            <span className="material-symbols-outlined icon-xs">add</span>{t('ui.HiveFleetOverlay.addAccount')}
          </button>
        ) : null}
      />

      <GuideBanner />

      {/* A failed first load of the view's own list is an error, never an empty
          roster (OverlayState). Fleet needs the instances; Accounts the accounts. */}
      {view === 'fleet' && instancesQ.isError && !instancesQ.data ? (
        <OverlayError
          testid="hive-load-error"
          retryTestid="hive-load-retry"
          message={classifyLoadError(instancesQ.error, 'Hive instances')}
          onRetry={() => { void instancesQ.refetch(); }}
        />
      ) : view === 'accounts' && accountsQ.isError && !accountsQ.data ? (
        <OverlayError
          testid="hive-load-error"
          retryTestid="hive-load-retry"
          message={classifyLoadError(accountsQ.error, 'AWS accounts')}
          onRetry={() => { void accountsQ.refetch(); }}
        />
      ) : view === 'fleet' ? (
        <FleetView
          myHives={myHives}
          sharedHives={sharedHives}
          instances={instances}
          accounts={deployable}
          limitReached={limitReached}
          onAction={refresh}
          onDeploy={() => setShowDeploy(true)}
        />
      ) : (
        <AccountsView accounts={view_.shown} onAction={refresh}
          onAdd={view_.canAdd ? () => setShowAddAccount(true) : undefined} />
      )}

      {showAddAccount && (
        <AddAccountDialog
          onClose={() => setShowAddAccount(false)}
          onSaved={() => { setShowAddAccount(false); void refresh(); }}
        />
      )}
      {showDeploy && (
        <DeployHiveDialog
          accounts={deployable}
          onClose={() => setShowDeploy(false)}
          onDeployed={() => { setShowDeploy(false); void refresh(); }}
        />
      )}
    </div>
  );
}

// ── Overview strip (Fleet) ──────────────────────────────────────────

function OverviewStrip({ instances }: { instances: HiveInstance[] }) {
  const { t } = useTranslation();
  const count = (s: string) => instances.filter((i) => i.status === s).length;
  const provisioning = instances.filter((i) => TRANSITIONAL_STATUSES.includes(i.status)).length;
  const cells: { id: string; label: string; value: string; danger?: boolean }[] = [
    { id: 'total', label: t('ui.HiveFleetOverlay.overviewTotal'), value: String(instances.length) },
    { id: 'running', label: t('ui.HiveFleetOverlay.overviewRunning'), value: String(count('running')) },
    { id: 'provisioning', label: t('ui.HiveFleetOverlay.overviewProvisioning'), value: String(provisioning) },
    { id: 'stopped', label: t('ui.HiveFleetOverlay.overviewStopped'), value: String(count('stopped')) },
    { id: 'error', label: t('ui.HiveFleetOverlay.overviewError'), value: String(count('error')), danger: count('error') > 0 },
  ];
  return (
    <div className="shrink-0 mx-4 mt-3 grid grid-cols-3 sm:grid-cols-5 gap-2" data-testid="hive-overview">
      {cells.map((c) => (
        <div key={c.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 py-1.5 flex flex-col">
          <span className={`text-heading font-semibold ${c.danger ? 'text-[var(--color-status-error)]' : 'text-[var(--color-text)]'}`}>{c.value}</span>
          <span className="text-meta uppercase tracking-wider text-[var(--color-text-faint)]">{c.label}</span>
        </div>
      ))}
    </div>
  );
}

// ── Fleet view: overview + My/Shared instance sections ─────────────

function FleetView({ myHives, sharedHives, instances, accounts, limitReached, onAction, onDeploy }: {
  myHives: HiveInstance[];
  sharedHives: HiveInstance[];
  instances: HiveInstance[];
  accounts: HiveAccount[];
  limitReached: boolean;
  onAction: () => void;
  onDeploy: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden" data-testid="hive-fleet-view">
      <OverviewStrip instances={instances} />
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {myHives.length > 0 && (
          <HiveSection title={t('ui.HiveFleetOverlay.myHives')} instances={myHives} onAction={onAction} />
        )}
        <HiveSection
          title={myHives.length > 0 ? t('ui.HiveFleetOverlay.sharedHives') : t('ui.HiveFleetOverlay.hiveInstances')}
          // Always sharedHives — myHives ∪ sharedHives partitions instances, so when
          // there are no My Hives this section (titled "Hive Instances") shows every
          // instance anyway. Passing sharedHives (not `instances`) is partition-robust:
          // it can never double-render a My Hive even if the split logic later changes
          // (adversarial MED, run_b450108e).
          instances={sharedHives}
          onAction={onAction}
          accounts={accounts}
          onDeploy={onDeploy}
          limitReached={limitReached}
          showEmpty
        />
      </div>
    </div>
  );
}

// ── Accounts view: AWS account roster ───────────────────────────────

function AccountsView({ accounts, onAction, onAdd }: {
  accounts: HiveAccount[]; onAction: () => void; onAdd?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3" data-testid="hive-accounts-view">
      {accounts.length === 0 ? (
        <div className="text-meta text-[var(--color-text-faint)] text-center py-8" data-testid="hive-accounts-empty">
          {t('ui.HiveFleetOverlay.noAccounts')}
        </div>
      ) : (
        <div className="space-y-2">
          {accounts.map((acc) => (
            <AccountCard key={acc.id} account={acc} onDelete={onAction} />
          ))}
        </div>
      )}
      {onAdd && (
        <button
          onClick={onAdd}
          className="mt-3 text-meta text-primary hover:underline"
          data-testid="hive-accounts-add-inline"
        >
          {t('ui.HiveFleetOverlay.addAnother')}
        </button>
      )}
    </div>
  );
}

// ── Guide banner ────────────────────────────────────────────────────

function GuideBanner() {
  const { t } = useTranslation();
  return (
    <div
      className="shrink-0 mx-4 mt-3 rounded-lg border border-primary/25 bg-primary/[0.06] px-3.5 py-2.5 flex items-start gap-2.5"
      data-testid="hive-guide-banner"
    >
      <span className="material-symbols-outlined icon-sm text-primary mt-0.5 shrink-0">cloud</span>
      <div className="flex-1 min-w-0 text-meta leading-relaxed text-[var(--color-text-muted)]">
        <span className="text-[var(--color-text)] ">{t('ui.HiveFleetOverlay.guideLead')}</span>{' '}{t('ui.HiveFleetOverlay.guideBeforeDeploy')}{' '}
        <span className="text-[var(--color-text)]">{t('ui.HiveFleetOverlay.deployHive')}</span>{t('ui.HiveFleetOverlay.guideAfterDeploy')}
      </div>
    </div>
  );
}
