/**
 * Tabbed TSCC context panel content.
 *
 * Renders a multi-tab view of the assembled system prompt:
 *   1. Files    — context file list with per-file token counts + share bars
 *   2. Recall   — recalled-knowledge snapshot (Memory/Library/DDD provenance)
 *   3. Security — prompt security scan verdict (secrets/PII), reused detectors
 *   4. Prompt   — full assembled system-prompt text + token total
 *
 * DECOUPLING: the Recall and Security tabs fetch their data LAZILY — only the
 * first time that tab is opened — so merely rendering the panel (Files tab)
 * issues zero extra requests, and nothing here ever touches the chat send path.
 * The backend endpoints are read-only snapshots; Security scans server-side on
 * demand, Recall reads a snapshot captured fire-and-forget during message #1.
 *
 * Key exports:
 * - ``SystemPromptModule`` — the tabbed panel body (name kept for call-site compat)
 */

import { useState, useCallback, useEffect } from 'react';
import type {
  SystemPromptMetadata,
  RecallSnapshot,
  SecurityScanResult,
} from '../../../types';
import {
  getSystemPromptMetadata,
  getRecallSnapshot,
  getSecurityScan,
} from '../../../services/tscc';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SystemPromptModuleProps {
  sessionId: string | null;
  metadata: SystemPromptMetadata | null;
}

type TabKey = 'files' | 'recall' | 'security' | 'prompt';

// ---------------------------------------------------------------------------
// Ownership classification (matches backend context_directory_loader priority)
// ---------------------------------------------------------------------------

const OWNER: Record<string, { label: string; color: string }> = {
  'SWARMAI.md': { label: 'sys', color: '#6ea8fe' },
  'IDENTITY.md': { label: 'sys', color: '#6ea8fe' },
  'SOUL.md': { label: 'sys', color: '#6ea8fe' },
  'AGENT.md': { label: 'sys', color: '#6ea8fe' },
  'SELF.md': { label: 'agent', color: '#a78bfa' },
  'USER.md': { label: 'user', color: '#4ade80' },
  'STEERING.md': { label: 'user', color: '#4ade80' },
  'TOOLS.md': { label: 'user', color: '#4ade80' },
  'MEMORY.md': { label: 'agent', color: '#a78bfa' },
  'EVOLUTION.md': { label: 'agent', color: '#a78bfa' },
  'KNOWLEDGE.md': { label: 'gen', color: '#38d9c4' },
  'PROJECTS.md': { label: 'gen', color: '#38d9c4' },
};

function ownerOf(filename: string): { label: string; color: string } {
  return OWNER[filename] ?? { label: 'gen', color: '#8b93a7' };
}

// ---------------------------------------------------------------------------
// Full Prompt Modal
// ---------------------------------------------------------------------------

function FullPromptModal({
  fullText,
  onClose,
}: {
  fullText: string;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Full system prompt"
    >
      <div
        className="w-[92vw] max-w-5xl max-h-[90vh] flex flex-col bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">
            System Prompt
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)]"
            aria-label="Close"
          >
            <span className="material-symbols-outlined text-base">close</span>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          <pre className="text-[13px] text-[var(--color-text)] whitespace-pre-wrap font-mono leading-relaxed">
            {fullText || '(empty)'}
          </pre>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 1: Files
// ---------------------------------------------------------------------------

function FilesTab({ metadata }: { metadata: SystemPromptMetadata | null }) {
  const files = metadata?.files ?? [];
  const totalTokens = metadata?.totalTokens ?? 0;
  const maxTok = files.reduce((m, f) => Math.max(m, f.tokens), 1);

  if (files.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-muted)] italic py-4">
        No context files loaded
      </p>
    );
  }

  return (
    <div>
      <ul className="text-sm space-y-1.5">
        {files.map((f) => {
          const o = ownerOf(f.filename);
          const pct = Math.round((f.tokens / maxTok) * 100);
          return (
            <li key={f.filename}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--color-text)] truncate flex items-center gap-1.5 text-[13px]">
                  <span
                    className="w-2 h-2 rounded-sm flex-shrink-0"
                    style={{ background: o.color }}
                  />
                  <span className="font-mono">{f.filename}</span>
                  {f.truncated && (
                    <span
                      className="text-[9px] px-1 rounded bg-amber-500/20 text-amber-500"
                      title="Smart-selected / truncated to fit budget"
                    >
                      smart
                    </span>
                  )}
                </span>
                <span className="text-xs text-[var(--color-text-muted)] tabular-nums flex-shrink-0">
                  {f.tokens.toLocaleString()}
                </span>
              </div>
              <div className="h-1 rounded bg-[var(--color-border)] mt-1 overflow-hidden">
                <div
                  className="h-full rounded"
                  style={{ width: `${pct}%`, background: o.color }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)] pt-2 mt-2 border-t border-[var(--color-border)]">
        <span>Total ({files.length} files)</span>
        <span className="tabular-nums font-medium">
          {totalTokens.toLocaleString()} tokens
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 2: Recall
// ---------------------------------------------------------------------------

function RecallTab({ sessionId }: { sessionId: string }) {
  const [snap, setSnap] = useState<RecallSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(false);
    getRecallSnapshot(sessionId)
      .then((s) => {
        if (alive) setSnap(s);
      })
      .catch(() => {
        if (alive) setErr(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);

  if (loading) {
    return <p className="text-sm text-[var(--color-text-muted)] py-4">Loading…</p>;
  }
  if (err) {
    return (
      <p className="text-sm text-[var(--color-text-muted)] italic py-4">
        Failed to load recall snapshot
      </p>
    );
  }
  if (!snap || !snap.ran) {
    return (
      <p className="text-sm text-[var(--color-text-muted)] italic py-4">
        No recall ran this session — recall fires once, on the first substantive
        message (skipped for channels / short openers).
      </p>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)] mb-2 pb-2 border-b border-[var(--color-border)]">
        <span className="tabular-nums">~{snap.tokens.toLocaleString()} tok</span>
        <span className="tabular-nums">{Math.round(snap.latencyMs)} ms</span>
        <span className="text-[10px] px-1.5 rounded bg-[var(--color-hover)] font-mono">
          keyword / FTS5
        </span>
      </div>
      {snap.keywords.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {snap.keywords.slice(0, 12).map((k, i) => (
            <span
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--color-hover)] text-[var(--color-text-muted)] font-mono"
            >
              {k}
            </span>
          ))}
        </div>
      )}
      <pre className="text-[12px] text-[var(--color-text)] whitespace-pre-wrap font-mono leading-relaxed max-h-[280px] overflow-y-auto opacity-90">
        {snap.body}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 3: Security
// ---------------------------------------------------------------------------

const GRADE_COLOR: Record<string, string> = {
  A: '#4ade80',
  'A-': '#4ade80',
  B: '#fbbf24',
  C: '#f87171',
  'n/a': '#8b93a7',
};
const SEV_COLOR: Record<string, string> = {
  critical: '#f87171',
  high: '#fb923c',
  medium: '#fbbf24',
  info: '#6ea8fe',
};

function SecurityTab({ sessionId }: { sessionId: string }) {
  const [scan, setScan] = useState<SecurityScanResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(false);
    getSecurityScan(sessionId)
      .then((s) => {
        if (alive) setScan(s);
      })
      .catch(() => {
        if (alive) setErr(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);

  if (loading) {
    return <p className="text-sm text-[var(--color-text-muted)] py-4">Scanning…</p>;
  }
  if (err || !scan) {
    return (
      <p className="text-sm text-[var(--color-text-muted)] italic py-4">
        Failed to run security scan
      </p>
    );
  }
  if (scan.grade === 'n/a') {
    return (
      <p className="text-sm text-[var(--color-text-muted)] italic py-4">
        No assembled prompt to scan yet
      </p>
    );
  }

  const gc = GRADE_COLOR[scan.grade] ?? '#8b93a7';

  return (
    <div>
      <div className="flex items-center gap-3 mb-3">
        <div
          className="w-11 h-11 rounded-lg flex items-center justify-center text-xl font-bold font-mono flex-shrink-0"
          style={{ background: `${gc}22`, color: gc }}
        >
          {scan.grade}
        </div>
        <div className="flex gap-3 text-xs">
          <span className="text-[var(--color-text-muted)]">
            <b style={{ color: SEV_COLOR.critical }}>{scan.critical}</b> crit
          </span>
          <span className="text-[var(--color-text-muted)]">
            <b style={{ color: SEV_COLOR.high }}>{scan.high}</b> high
          </span>
          <span className="text-[var(--color-text-muted)]">
            <b style={{ color: SEV_COLOR.info }}>{scan.info}</b> info
          </span>
          <span className="text-[var(--color-text-muted)]">
            {scan.scannedFiles} files
          </span>
        </div>
      </div>
      <ul className="space-y-1.5">
        {scan.findings.map((f, i) => {
          const pass = f.status === 'pass';
          const sc = pass ? '#4ade80' : SEV_COLOR[f.severity] ?? '#8b93a7';
          return (
            <li
              key={i}
              className="flex items-start gap-2 p-2 rounded bg-[var(--color-hover)]/40"
            >
              <span
                className="material-symbols-outlined text-sm mt-0.5"
                style={{ color: sc }}
              >
                {pass ? 'check_circle' : 'warning'}
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium text-[var(--color-text)] flex items-center gap-2">
                  {f.detector}
                  <span
                    className="text-[9px] px-1.5 rounded uppercase font-mono"
                    style={{ background: `${sc}22`, color: sc }}
                  >
                    {pass ? 'pass' : f.severity}
                  </span>
                </div>
                <div className="text-[11px] text-[var(--color-text-muted)] mt-0.5 break-words">
                  {f.detail}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="text-[10px] text-[var(--color-text-muted)] italic mt-2 pt-2 border-t border-[var(--color-border)]">
        Static scan of the assembled prompt · secrets masked · reuses egress
        credential detectors. Design-time review, not a runtime output filter.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 4: Prompt (full text launcher)
// ---------------------------------------------------------------------------

function PromptTab({
  sessionId,
  metadata,
}: {
  sessionId: string;
  metadata: SystemPromptMetadata | null;
}) {
  const [showModal, setShowModal] = useState(false);
  const [fullText, setFullText] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleView = useCallback(async () => {
    if (metadata?.fullText) {
      setFullText(metadata.fullText);
      setShowModal(true);
      return;
    }
    setIsLoading(true);
    try {
      const result = await getSystemPromptMetadata(sessionId);
      setFullText(result ? result.fullText : '(Session not initialized yet)');
      setShowModal(true);
    } catch {
      setFullText('(Failed to load system prompt)');
      setShowModal(true);
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, metadata?.fullText]);

  const total = metadata?.totalTokens ?? 0;

  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-3">
        <span className="text-[var(--color-text-muted)]">Assembled tokens</span>
        <span className="tabular-nums font-mono font-semibold text-[var(--color-text)]">
          {total.toLocaleString()}
        </span>
      </div>
      <button
        onClick={handleView}
        disabled={isLoading}
        className="w-full text-xs py-1.5 px-3 rounded bg-[var(--color-hover)] text-[var(--color-text)] hover:bg-[var(--color-border)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isLoading ? 'Loading…' : 'View Full Prompt'}
      </button>
      {showModal && fullText !== null && (
        <FullPromptModal fullText={fullText} onClose={() => setShowModal(false)} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SystemPromptModule — tabbed shell
// ---------------------------------------------------------------------------

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'files', label: 'Files', icon: 'description' },
  { key: 'recall', label: 'Recall', icon: 'travel_explore' },
  { key: 'security', label: 'Security', icon: 'security' },
  { key: 'prompt', label: 'Prompt', icon: 'article' },
];

export function SystemPromptModule({
  sessionId,
  metadata,
}: SystemPromptModuleProps) {
  const [tab, setTab] = useState<TabKey>('files');

  if (!sessionId) {
    return (
      <p className="text-sm text-[var(--color-text-muted)] italic">
        No active session
      </p>
    );
  }

  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-2">
        System Prompt
      </h4>

      {/* Tab bar */}
      <div className="flex gap-0.5 border-b border-[var(--color-border)] mb-3 -mx-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex-1 flex items-center justify-center gap-1 py-1.5 text-[11px] font-medium border-b-2 -mb-px transition-colors ${
              tab === t.key
                ? 'text-[var(--color-primary)] border-[var(--color-primary)]'
                : 'text-[var(--color-text-muted)] border-transparent hover:text-[var(--color-text)]'
            }`}
          >
            <span className="material-symbols-outlined text-[14px]">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab body — Recall/Security mount lazily (fetch only when opened) */}
      {tab === 'files' && <FilesTab metadata={metadata} />}
      {tab === 'recall' && <RecallTab sessionId={sessionId} />}
      {tab === 'security' && <SecurityTab sessionId={sessionId} />}
      {tab === 'prompt' && (
        <PromptTab sessionId={sessionId} metadata={metadata} />
      )}
    </div>
  );
}
