/**
 * Hive AWS-account config tab (Settings).
 *
 * R27 dual-entry convergence (run_b450108e): fleet/instance MANAGEMENT (deploy /
 * start / stop / update / retry / delete / credentials) moved to the first-class
 * SYSTEM-zone "Hive" nav card → HiveFleetOverlay. This Settings tab is now the SLIM
 * credentials-and-accounts surface: add / verify / delete the AWS accounts Hives
 * deploy into. Accounts are a credentials/config concern, so they belong in Settings;
 * the live fleet belongs in the workbench overlay.
 *
 * All UI building blocks are shared from ./hiveComponents (single source — the overlay
 * consumes the same AccountCard/AddAccountDialog, so the two entries never drift).
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { hiveService, HiveAccount } from '../../services/hive';
import { AccountCard, AddAccountDialog } from './hiveComponents';

export default function HiveTab() {
  const [accounts, setAccounts] = useState<HiveAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddAccount, setShowAddAccount] = useState(false);
  const refreshInFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      const accs = await hiveService.listAccounts();
      setAccounts(accs);
    } catch (e) {
      console.error('Failed to load Hive accounts:', e);
    } finally {
      refreshInFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (loading) {
    return <div className="text-[var(--color-text-muted)] text-sm p-6">Loading...</div>;
  }

  return (
    <div className="space-y-6" data-testid="hive-accounts-tab">
      {/* Intro — points to the Fleet nav card for instance management */}
      <p className="text-sm text-[var(--color-text-muted)]">
        Manage the AWS accounts your Hives deploy into. To deploy, start/stop, or share
        a Hive, open the <span className="text-[var(--color-text)] font-medium">Hive</span> card
        in the left nav (System) — the Fleet workbench.
      </p>

      {/* AWS Accounts */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">AWS Accounts</h3>
          <button
            onClick={() => setShowAddAccount(true)}
            className="px-3 py-1.5 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded-lg hover:bg-[var(--color-primary)] hover:text-white transition-colors flex items-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">add</span>
            Add Account
          </button>
        </div>

        {accounts.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)] text-center py-4">
            No AWS accounts configured. Add one to start deploying Hives.
          </p>
        ) : (
          <div className="space-y-2">
            {accounts.map((acc) => (
              <AccountCard key={acc.id} account={acc} onDelete={refresh} />
            ))}
          </div>
        )}
      </section>

      {showAddAccount && (
        <AddAccountDialog
          onClose={() => setShowAddAccount(false)}
          onSaved={() => { setShowAddAccount(false); refresh(); }}
        />
      )}
    </div>
  );
}
