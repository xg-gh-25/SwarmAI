/**
 * BrainLearnedView — what a Brain learned (design docs/design/2026-10-30-brain-change-feed.md).
 *
 * The entries CDF wrote into the Brain's files, newest first, grouped by the three
 * layers of the seven types (meta → cognitive → operational), then the open items
 * (gaps). It reads CDF's own records (GET /api/ddd/brains/{name}/knowledge), never a
 * git diff, so it works for every Brain — those outside git and the General Brain too.
 *
 * The page answers one decision: is anything here wrong? Knowledge is already in effect
 * when it shows up, so there is no "seen" state and no batch action; the one action is
 * Withdraw, which deletes the entry (POST /api/ingestion/redact) after an inline confirm.
 * A line at the bottom says how many entries the person withdrew, with the list behind it.
 *
 * All entry text is rendered as text (React escapes it): entries often hold `<task_id>`.
 *
 * @exports BrainLearnedView
 */
import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  useBrainKnowledge, type KnowledgeItem, type KnowledgeLayer, type KnowledgeRange,
} from '../../services/ddd';
import { ingestionService } from '../../services/ingestion';
import { isApiError } from '../../services/api';

const LAYERS: KnowledgeLayer[] = ['meta', 'cognitive', 'operational'];
const RANGES: KnowledgeRange[] = ['week', 'month', 'all'];
const TYPES = ['principle', 'correction', 'decision', 'model', 'guideline', 'pitfall', 'process'] as const;
const SOURCES = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'] as const;

/** "today 14:05" / "yesterday 19:00" / "Oct 29" in the UI language; null → null. */
export function whenText(iso: string | null, lang: string, now = new Date(),
  t: (k: string, o?: Record<string, unknown>) => string = (k) => k): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((day(now) - day(at)) / 86_400_000);
  const time = at.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit', hour12: false });
  if (diff === 0) return t('brainLearned.when.today', { time });
  if (diff === 1) return t('brainLearned.when.yesterday', { time });
  return at.toLocaleDateString(lang, at.getFullYear() === now.getFullYear()
    ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
}

/** The api client rejects with an ApiError (services/api interceptor): the server's
 *  own `detail` when it sent one, else its message. */
function errorText(e: unknown): string {
  if (isApiError(e)) return e.response.detail || e.message;
  return (e as { message?: string })?.message ?? String(e);
}

export function BrainLearnedView({ name, brainLabel }: {
  /** the Brain's name for the API ('General' = the built-in General Brain) */
  name: string;
  /** how the page names it (defaults to `name`) */
  brainLabel?: string;
}) {
  const { t, i18n } = useTranslation();
  const [range, setRange] = useState<KnowledgeRange>('week');
  const { data, error, isLoading, refetch } = useBrainKnowledge(name, range);
  const [note, setNote] = useState<string | null>(null);
  const [showWithdrawn, setShowWithdrawn] = useState(false);
  const label = brainLabel ?? name;

  const shown = useMemo(() => (data ? data.layers.reduce((s, l) => s + l.items.length, 0) + data.gaps.length : 0), [data]);

  if (error) {
    return (
      <div className="p-4 flex items-center gap-3 text-body text-[var(--color-status-error)]" data-testid="learned-error">
        {t('brainLearned.loadFailed', { error: errorText(error) })}
        <button className="btn-sm btn-secondary" onClick={() => void refetch()} data-testid="learned-retry">{t('brainLearned.retry')}</button>
      </div>
    );
  }
  if (isLoading || !data) {
    return <div className="p-4 text-body text-[var(--color-text-muted)]" data-testid="learned-loading">{t('brainLearned.loading')}</div>;
  }

  const verdict = shown
    ? t(`brainLearned.verdict.${range}`, { name: label, count: shown })
    : t(`brainLearned.none.${range}`, { name: label });
  const written = data.counts.all + data.withdrawn.count;

  return (
    <div className="px-6 py-5 max-w-[920px]" data-testid="brainhub-learned">
      <div className="flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <h2 className="m-0 text-heading font-semibold" data-testid="learned-verdict">{verdict}</h2>
          {shown > 0 && <p className="mt-1 text-small text-[var(--color-text-muted)]">{t('brainLearned.sub')}</p>}
        </div>
        <div className="flex shrink-0 rounded-md border border-[var(--color-border)] overflow-hidden" role="group" data-testid="learned-range">
          {RANGES.map((r) => (
            <button key={r} onClick={() => { setRange(r); setNote(null); }} aria-pressed={range === r}
              data-testid={`learned-range-${r}`}
              className={`px-3 py-1 text-small ${range === r
                ? 'bg-[var(--color-hover)] text-[var(--color-text)] font-semibold'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
              {t(`brainLearned.range.${r}`)}
            </button>
          ))}
        </div>
      </div>

      {note && <div className="mt-3 text-small text-[var(--color-text-secondary)]" data-testid="learned-note">{note}</div>}

      {shown === 0 ? (
        <p className="mt-6 text-body text-[var(--color-text-muted)]" data-testid="learned-empty">
          {range === 'all' ? t('brainLearned.emptyAll') : t('brainLearned.empty')}
        </p>
      ) : (
        <div className="mt-6 flex flex-col gap-8">
          {LAYERS.map((layer) => {
            const items = data.layers.find((l) => l.layer === layer)?.items ?? [];
            return (
              <Group key={layer} testid={`learned-layer-${layer}`} title={t(`brainLearned.layer.${layer}.name`)}
                desc={t(`brainLearned.layer.${layer}.desc`)} count={items.length}
                emptyText={t('brainLearned.layerEmpty')}>
                {items.map((it) => (
                  <Entry key={it.anchor} item={it} brain={name} brainLabel={label} lang={i18n.language} onWithdrawn={setNote} />
                ))}
              </Group>
            );
          })}
          {data.gaps.length > 0 && (
            <Group testid="learned-gaps" title={t('brainLearned.gaps.name')} desc={t('brainLearned.gaps.desc')}
              count={data.gaps.length} emptyText="">
              {data.gaps.map((it) => (
                <Entry key={it.anchor} item={it} brain={name} brainLabel={label} lang={i18n.language} onWithdrawn={setNote} />
              ))}
            </Group>
          )}
        </div>
      )}

      <div className="mt-10 text-small text-[var(--color-text-muted)]" data-testid="learned-withdrawn">
        <span data-testid="learned-withdrawn-line">
          {data.withdrawn.count
            ? t('brainLearned.withdrawn.line', { count: data.withdrawn.count, written })
            : t('brainLearned.withdrawn.none', { written })}
        </span>
        {data.withdrawn.count > 0 && (
          <button className="ml-2 underline hover:text-[var(--color-text)]" data-testid="learned-withdrawn-toggle"
            onClick={() => setShowWithdrawn((v) => !v)}>
            {showWithdrawn ? t('brainLearned.withdrawn.hide') : t('brainLearned.withdrawn.show')}
          </button>
        )}
        {showWithdrawn && (
          <div className="mt-3 flex flex-col gap-3" data-testid="learned-withdrawn-list">
            {data.withdrawn.items.map((w, i) => (
              <div key={`${w.at}-${i}`}>
                <div className="text-[var(--color-text-secondary)]">
                  {w.type && <TypeTag type={w.type} />}{w.title}
                </div>
                <div className="text-meta text-[var(--color-text-faint)]">
                  {t('brainLearned.withdrawn.meta', {
                    written: whenText(w.written_at, i18n.language, undefined, t) ?? t('brainLearned.unknownTime'),
                    at: whenText(w.at, i18n.language, undefined, t) ?? '—',
                    reason: w.reason || '—',
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Group({ testid, title, desc, count, emptyText, children }: {
  testid: string; title: string; desc: string; count: number; emptyText: string; children: React.ReactNode;
}) {
  return (
    <section data-testid={testid}>
      <div className="flex items-baseline gap-3 pb-1.5 border-b border-[var(--color-border)]">
        <span className="text-body font-semibold">{title}</span>
        <span className="text-small text-[var(--color-text-muted)]">{desc}</span>
        {count > 0 && <span className="ml-auto text-meta text-[var(--color-text-faint)] tabular-nums">{count}</span>}
      </div>
      {count === 0
        ? <div className="pt-2 text-small text-[var(--color-text-faint)]" data-testid={`${testid}-empty`}>{emptyText}</div>
        : <div className="flex flex-col">{children}</div>}
    </section>
  );
}

function TypeTag({ type }: { type: string }) {
  const { t } = useTranslation();
  const known = (TYPES as readonly string[]).includes(type) || type === 'gap';
  return (
    <span className="mr-2 inline-block px-1.5 rounded text-meta font-semibold align-[1px] text-[var(--color-text-muted)] bg-[var(--color-hover)]">
      {known ? t(`brainLearned.type.${type}`) : type}
    </span>
  );
}

function Entry({ item, brain, brainLabel, lang, onWithdrawn }: {
  item: KnowledgeItem; brain: string; brainLabel: string; lang: string; onWithdrawn: (note: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  // the title is already shown above the body: the body is the rest of the entry
  const body = item.text.replace(/^-\s*\[[^\]]*\]\s*\*\*[^*]+\*\*\s*/, '').trim();
  const long = body.length > 220 || body.split('\n').length > 2;
  const when = whenText(item.written_at, lang, undefined, t);
  const source = item.source && (SOURCES as readonly string[]).includes(item.source)
    ? t(`brainLearned.source.${item.source}`) : item.source ? item.source : t('brainLearned.source.expired');

  const withdraw = async () => {
    setBusy(true); setErr(null);
    try {
      await ingestionService.redact(item.anchor, reason.trim() || t('brainLearned.confirm.defaultReason'));
      setGone(true);
      onWithdrawn(t('brainLearned.done', { title: item.title, doc: item.doc }));
      void qc.invalidateQueries({ queryKey: ['brain-knowledge', brain] });
      void qc.invalidateQueries({ queryKey: ['brains-with-pinned'] });
      void qc.invalidateQueries({ queryKey: ['brain-detail', brain] });
    } catch (e) {
      const status = isApiError(e) ? e.statusCode : undefined;
      setErr(status === 409 ? t('brainLearned.confirm.stale') : t('brainLearned.confirm.failed', { error: errorText(e) }));
    } finally { setBusy(false); }
  };

  if (gone) return null;
  return (
    <article className="group py-3 border-b border-[color-mix(in_srgb,var(--color-border)_60%,transparent)] last:border-b-0"
      data-testid="learned-entry">
      <div className="text-body font-medium text-[var(--color-text)]">
        <TypeTag type={item.gap_kind ? 'gap' : item.type} />
        <span data-testid="learned-entry-title">{item.title}</span>
      </div>
      {body && (
        <div className={`mt-1 text-small text-[var(--color-text-secondary)] whitespace-pre-wrap ${open ? '' : 'line-clamp-2'}`}
          data-testid="learned-entry-body">{body}</div>
      )}
      {item.adds_to && (
        <div className="mt-1 text-small text-[var(--color-text-muted)]" data-testid="learned-entry-adds">
          {t('brainLearned.addsTo', { title: item.adds_to })}
        </div>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 text-meta text-[var(--color-text-faint)]">
        <span>{t('brainLearned.writtenInto', { doc: item.doc, section: item.section })}</span>
        <span>·</span>
        <span data-testid="learned-entry-when">
          {when ? (item.collected ? t('brainLearned.collected', { when }) : when) : t('brainLearned.unknownTime')}
        </span>
        <span>·</span>
        <span title={item.source_ref ?? undefined}>{source}</span>
        {long && (
          <button className="underline hover:text-[var(--color-text)]" onClick={() => setOpen((v) => !v)} data-testid="learned-entry-more">
            {open ? t('brainLearned.less') : t('brainLearned.more')}
          </button>
        )}
        {!confirming && (
          <button className="ml-auto opacity-60 group-hover:opacity-100 hover:text-[var(--color-status-error)]"
            onClick={() => setConfirming(true)} data-testid="learned-withdraw">{t('brainLearned.withdraw')}</button>
        )}
      </div>
      {confirming && (
        <div className="mt-3 rounded-md border border-[var(--color-border)] p-3" data-testid="learned-confirm">
          <p className="m-0 text-small text-[var(--color-text-secondary)]">
            {t('brainLearned.confirm.text', { name: brainLabel, doc: item.doc })}
          </p>
          <textarea rows={4} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500}
            placeholder={t('brainLearned.confirm.reason')} data-testid="learned-confirm-reason"
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !busy) void withdraw(); }}
            className="mt-2 w-full resize-y rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-small" />
          {err && <div className="mt-1 text-small text-[var(--color-status-error)]" data-testid="learned-confirm-error">{err}</div>}
          <div className="mt-2 flex items-center gap-2">
            <button className="btn-sm btn-danger disabled:opacity-40" disabled={busy} onClick={() => void withdraw()}
              data-testid="learned-confirm-yes">{t('brainLearned.withdraw')}</button>
            <button className="btn-sm btn-ghost" disabled={busy} onClick={() => { setConfirming(false); setErr(null); }}
              data-testid="learned-confirm-no">{t('brainLearned.confirm.cancel')}</button>
            <span className="ml-auto text-meta text-[var(--color-text-faint)]">{t('brainLearned.confirm.shortcut')}</span>
          </div>
        </div>
      )}
    </article>
  );
}
