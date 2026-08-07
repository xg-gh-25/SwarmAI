/**
 * LibraryHealth — the Native-store health + cleanup section in the Library rail.
 *
 * Sits BELOW the NATIVE stats block. Shows the weekly `library-health` job's
 * findings (GET /api/library/health) — each a one-line, decision-oriented item
 * with an executable action:
 *   · archive_old_logs → one-click "Archive to Archives/" (reversible move)
 *   · delete_empty     → "Delete" behind an inline confirm (destructive)
 *   · oversized_category → informational flag, no button
 *
 * Reversible-first (STEERING #2): archive runs immediately; delete flips the row
 * into a confirm state and only POSTs with confirm=true after the user OKs — we
 * never auto-destroy. After any action the report refetches so the section
 * reflects the new state. Clean store → a quiet "healthy" line, not an empty void.
 *
 * @exports LibraryHealth
 */
import { useState } from 'react';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import api from '../../services/api';

interface HealthFinding {
  kind: 'archive_old_logs' | 'delete_empty' | 'oversized_category';
  title: string;
  detail: string;
  action_label: string;
  actionable: boolean;
  reversible: boolean;
  count: number;
  total_bytes: number;
  paths: string[];
}
interface HealthReport { generated_at: number; root: string; findings: HealthFinding[]; clean: boolean; }

const KIND_TINT: Record<HealthFinding['kind'], string> = {
  archive_old_logs: '#4a8fb0',   // blue — reversible move
  delete_empty: '#d08a4a',       // amber — destructive, caution
  oversized_category: '#8a8f99', // grey — informational
};

export function LibraryHealth() {
  const qc = useQueryClient();
  // Which delete-finding row is currently awaiting confirm (keyed by kind — only
  // one delete finding exists, but keying is explicit + future-proof).
  const [confirming, setConfirming] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery<HealthReport>({
    queryKey: ['library-health'],
    queryFn: async () => (await api.get<HealthReport>('/library/health')).data,
    staleTime: 60_000,
  });

  const action = useMutation({
    mutationFn: async (vars: { kind: string; paths: string[]; confirm: boolean }) =>
      (await api.post('/library/health/action', vars)).data,
    onSuccess: () => {
      setConfirming(null);
      qc.invalidateQueries({ queryKey: ['library-health'] });
      qc.invalidateQueries({ queryKey: ['library-native'] }); // rail totals shift after cleanup
    },
  });

  // The section is intentionally quiet when there's nothing to say — a health
  // widget that shouts on a clean store is noise.
  // Guard the serialization boundary (O023): a malformed/partial payload (no
  // `findings` array) is treated as "clean", never a render crash.
  const findings = Array.isArray(data?.findings) ? data!.findings : [];
  if (isLoading || isError || !data) {
    return isError ? (
      <div data-testid="library-health-error" className="text-[10px] text-[var(--color-text-faint)]">
        health check unavailable
      </div>
    ) : null;
  }

  if (data.clean || findings.length === 0) {
    return (
      <div data-testid="library-health-clean" className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-faint)]">
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#5fc99a' }} aria-hidden />
        Healthy — nothing to clean
      </div>
    );
  }

  return (
    <div data-testid="library-health" className="flex flex-col gap-2">
      <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
        🧹 Health
      </div>
      {findings.map((f) => {
        const isConfirming = confirming === f.kind;
        const busy = action.isPending && action.variables?.kind === f.kind;
        return (
          <div key={f.kind} data-testid={`library-health-${f.kind}`} className="flex flex-col gap-1">
            <div className="flex items-start gap-2">
              <span className="mt-1 w-1.5 h-1.5 shrink-0 rounded-full" style={{ background: KIND_TINT[f.kind] }} aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-medium text-[var(--color-text)] leading-snug">{f.title}</div>
                <div className="text-[10px] text-[var(--color-text-faint)] leading-snug">{f.detail}</div>
              </div>
            </div>
            {f.actionable && (
              <div className="pl-3.5">
                {!isConfirming ? (
                  <button
                    data-testid={`library-health-action-${f.kind}`}
                    disabled={busy}
                    onClick={() => {
                      // Reversible (archive) → run now. Destructive (delete) → confirm first.
                      if (f.reversible) action.mutate({ kind: f.kind, paths: f.paths, confirm: false });
                      else setConfirming(f.kind);
                    }}
                    className="rounded px-2 py-0.5 text-[10px] font-medium text-white disabled:opacity-60"
                    style={{ background: KIND_TINT[f.kind] }}
                  >
                    {busy ? '…' : f.action_label}
                  </button>
                ) : (
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] text-[var(--color-text-muted)]">Delete {f.count}?</span>
                    <button
                      data-testid={`library-health-confirm-${f.kind}`}
                      disabled={busy}
                      onClick={() => action.mutate({ kind: f.kind, paths: f.paths, confirm: true })}
                      className="rounded px-2 py-0.5 text-[10px] font-medium text-white disabled:opacity-60"
                      style={{ background: '#d0524a' }}
                    >
                      {busy ? '…' : 'Yes, delete'}
                    </button>
                    <button
                      data-testid={`library-health-cancel-${f.kind}`}
                      onClick={() => setConfirming(null)}
                      className="text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    >
                      cancel
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
      {action.isError && (
        <div className="text-[10px] text-[#d0524a]">Action failed — see logs.</div>
      )}
    </div>
  );
}

export default LibraryHealth;
