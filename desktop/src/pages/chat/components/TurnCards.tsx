/**
 * TurnCards — the backend-written cards of a turn (chat turn spec v6).
 *
 * Each has a fixed place in the turn, set by AssistantMessageView:
 * - AttentionCard — what the run needs the user to judge (`pipeline_attention`): a
 *                   first-level card after the turn's zones, before the output card.
 *                   What the assistant settled without asking (questions it answered,
 *                   assumptions) is one group under ONE "{name} decided for you" line.
 * - OutputCard    — the turn's ONE output card at the end: a code-changes section
 *                   (ChangesCard, from `turn_changes`) and a deliverables section (what
 *                   the turn produced — compact `turn_deliverables` rows — and a pipeline
 *                   run's documents, with the run's verdict). Either section alone;
 *                   a file is in one section only.
 * All text in these blocks is model-written: rendered plain, never as markdown.
 *
 * @exports AttentionCard, OutputCard, middleEllipsis
 */
import { useContext, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type {
  PipelineAttentionContent, PipelineAttentionItem, PipelineSummaryContent, PipelineSummaryDoc, TurnDeliverable, TurnChangesContent,
} from '../../../types';
import { OPEN_FILE_EVENT, resolveImageSrc } from '../../../components/common/MarkdownRenderer';
import { fileIcon, fileIconColor } from '../../../utils/fileUtils';
import { copyToClipboard } from '../../../utils/clipboard';
import { tf } from '../tf';
import { docMeta } from '../runDocMeta';
import { ASSISTANT_NAME } from '../assistantName';
import { ReadOnlyContext } from '../readOnlyContext';
import { ChangesCard } from './ChangesCard';


/** A verdict that blocks is red; anything else that needs a look stays amber. */
const isBlocking = (v: string) => /BLOCK|FAIL|REJECT/i.test(v);
/** Items Swarm settled without asking: a question it answered itself, an assumption. */
const isDecided = (i: PipelineAttentionItem) => i.kind === 'self_answered' || i.kind === 'assumption';

const DIM = 'text-[12.5px] text-[var(--color-text-muted)]';
const TAG = 'mr-1.5 font-mono text-[11.5px] text-[var(--color-text-muted)]';

function AttentionRow({ item }: { item: PipelineAttentionItem }) {
  const { t } = useTranslation();
  let line: ReactNode;
  let note: ReactNode = null;
  if (item.kind === 'self_answered') {
    line = (
      <>
        {item.id && <span className={TAG}>{item.id}</span>}
        {item.question}
        {item.answer && <> → {item.answer}</>}
      </>
    );
    note = item.reason && tf(t, 'chat.turn.attention.reason', 'Why: {{text}}', { text: item.reason });
  } else if (item.kind === 'assumption') {
    line = (
      <>
        <span className={TAG}>{tf(t, 'chat.turn.attention.assumption', 'Assumed {{id}}', { id: item.id ?? '' }).trim()}</span>
        {item.statement}
      </>
    );
  } else if (item.kind === 'gate') {
    line = <>{item.name} · <span className={isBlocking(item.verdict) ? 'text-red-500' : ''}>{item.verdict}</span></>;
    note = item.detail;
  } else {
    line = (
      <>
        {/* Amber, not red: push_ready=false is a normal outcome (the change is
            committed locally; only the push waits for a look). */}
        <span className="text-amber-600 dark:text-amber-400">{tf(t, 'chat.turn.attention.notPushReady', 'Not ready to push')}</span>
        {item.reasons.length > 0 && (
          <ul data-testid="not-ready-reasons" className={`${DIM} list-disc pl-5`}>
            {/* one sentence per cause, in the UI language when the backend named the cause */}
            {item.reasons.map((r, i) => {
              const code = item.reasonCodes?.[i];
              return <li key={i}>{code ? tf(t, `chat.turn.attention.reasonCode.${code}`, r) : r}</li>;
            })}
          </ul>
        )}
      </>
    );
    note = item.detail;
  }
  return (
    <li data-testid="attention-item" data-kind={item.kind} className="py-0.5 text-[13.5px] text-[var(--color-text)] break-words">
      <div>{line}</div>
      {note && <div className={DIM}>{note}</div>}
    </li>
  );
}

export function AttentionCard({ block }: { block: PipelineAttentionContent }) {
  const { t } = useTranslation();
  const count = block.items.length + (block.more || 0);
  const decided = block.items.filter(isDecided);
  const rest = block.items.filter((i) => !isDecided(i));
  return (
    <section
      data-testid="turn-attention"
      aria-label={tf(t, 'chat.turn.attention.title', 'For you to check')}
      className="border-l-2 border-amber-500/70 bg-amber-500/[0.06] rounded-r-md pl-3 pr-2 py-2"
    >
      <div className="text-[12px] font-medium text-amber-600 dark:text-amber-400">
        {tf(t, 'chat.turn.attention.heading', 'For you to check · {{count}}', { count })}
      </div>
      {/* The assistant's name once, over what it settled for the user; its items
          inherit the subject (spacing groups them, not a box). */}
      {decided.length > 0 && (
        <div data-testid="attention-decided" className="mt-1">
          <div className="text-[12px] text-[var(--color-text-secondary)]">
            {tf(t, 'chat.turn.attention.decidedFor', '{{name}} decided for you · {{count}}', { name: ASSISTANT_NAME, count: decided.length })}
          </div>
          <ul className="pl-3">
            {decided.map((item, i) => <AttentionRow key={`d-${i}`} item={item} />)}
          </ul>
        </div>
      )}
      {rest.length > 0 && (
        <ul className="mt-1">
          {rest.map((item, i) => <AttentionRow key={`r-${i}`} item={item} />)}
        </ul>
      )}
      {block.more > 0 && (
        <div className={`mt-0.5 ${DIM}`}>
          {tf(t, 'chat.turn.attention.more', '+{{count}} more in the run report', { count: block.more })}
        </div>
      )}
    </section>
  );
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg)$/i;
const baseName = (p: string) => p.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || p;

/** Shorten a file name in the MIDDLE, keeping its extension ("very-lo…ng.pptx"). */
export function middleEllipsis(name: string, max = 28): string {
  if (name.length <= max) return name;
  const dot = name.lastIndexOf('.');
  const ext = dot > 0 && name.length - dot <= 8 ? name.slice(dot) : '';
  const stem = ext ? name.slice(0, dot) : name;
  const room = Math.max(2, max - ext.length - 1);
  const head = Math.ceil(room * 0.6);
  return `${stem.slice(0, head)}…${stem.slice(stem.length - (room - head))}${ext}`;
}

function DeliverableTile({ file }: { file: TurnDeliverable }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [thumbFailed, setThumbFailed] = useState(false);
  // A read-only preview (History) shows what was produced but does not act on it.
  const readOnly = useContext(ReadOnlyContext);
  const name = baseName(file.path || file.absolutePath);
  const thumb = IMAGE_EXT.test(name) && !thumbFailed ? resolveImageSrc(file.absolutePath) : undefined;
  const open = () => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.absolutePath } }));
  const copy = async () => {
    if (await copyToClipboard(file.absolutePath)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  const Head = readOnly ? 'div' : 'button';
  const headProps = readOnly ? {} : { type: 'button' as const, onClick: open };
  const action = 'text-[11.5px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:underline';
  return (
    <li data-testid="deliverable" data-path={file.absolutePath} className="flex items-center gap-2 min-w-0 py-1.5">
      <Head {...headProps} title={file.absolutePath} className="min-w-0 flex-1 flex items-center gap-2 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50">
        {thumb ? (
          <img src={thumb} alt="" onError={() => setThumbFailed(true)} className="w-7 h-7 rounded object-cover shrink-0" />
        ) : (
          <span
            aria-hidden="true"
            className="material-symbols-outlined text-[18px] leading-none shrink-0"
            style={{ color: fileIconColor(name), filter: 'saturate(0.55)' }}
          >
            {fileIcon(name)}
          </span>
        )}
        <span className="min-w-0 font-mono text-[12.5px] text-[var(--color-text-secondary)] truncate">{middleEllipsis(name)}</span>
      </Head>
      {!readOnly && (
        <div className="ml-auto shrink-0 flex items-center gap-3">
          <button type="button" onClick={open} className={action}>{tf(t, 'chat.turn.deliverables.open', 'Open in Canvas')}</button>
          <button type="button" onClick={copy} className={action}>
            {copied ? tf(t, 'chat.turn.deliverables.copied', 'Copied') : tf(t, 'chat.turn.deliverables.copyPath', 'Copy path')}
          </button>
        </div>
      )}
    </li>
  );
}

function SummaryDocRow({ doc }: { doc: PipelineSummaryDoc }) {
  const { t } = useTranslation();
  const readOnly = useContext(ReadOnlyContext);
  const meta = docMeta(t, doc.name, doc.purpose);
  const purpose = meta.purpose;
  const there = !!doc.absolutePath;
  const stat = doc.stat
    ? tf(t, 'chat.turn.summary.reviewStat', '{{experts}} experts · {{findings}} findings · {{settled}} settled', doc.stat)
    : null;
  const open = () => there && document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: doc.absolutePath } }));
  const Row = there && !readOnly ? 'button' : 'div';
  return (
    <li data-testid="summary-doc" data-name={doc.name} data-present={there ? 'true' : 'false'}>
      <Row
        {...(Row === 'button' ? { type: 'button' as const, onClick: open, title: doc.absolutePath ?? undefined } : {})}
        className={`w-full flex items-baseline gap-2 py-1 text-left min-w-0 ${Row === 'button' ? 'rounded hover:bg-[var(--color-hover)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
      >
        <span aria-hidden="true" className={`material-symbols-outlined text-[16px] leading-none self-center shrink-0 ${there ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-dim)]'}`}>
          {meta.icon}
        </span>
        <span className={`shrink-0 font-mono text-[12.5px] ${there ? 'text-[var(--color-text)]' : 'text-[var(--color-text-dim)]'}`}>{doc.name}</span>
        <span className={`min-w-0 truncate text-[12.5px] ${there ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-dim)]'}`}>
          {there ? purpose : (doc.reason ?? tf(t, 'chat.turn.summary.missing', 'not produced'))}
        </span>
        {there && stat && <span data-testid="summary-review-stat" className="ml-auto pl-2 shrink-0 text-[12px] text-[var(--color-text-muted)] tabular-nums">{stat}</span>}
      </Row>
    </li>
  );
}

/** How a run ended, in one line: the one thing the summary makes stand out. */
export function runVerdict(t: Parameters<typeof tf>[0], summary: PipelineSummaryContent): { text: string; tone: 'ok' | 'warn' | 'muted' } {
  const ended = summary.status;
  if (ended === 'abandoned' || ended === 'cancelled') return { text: tf(t, 'chat.turn.summary.abandoned', 'Closed without landing'), tone: 'muted' };
  if (ended === 'failed') return { text: tf(t, 'chat.turn.summary.failed', 'Failed'), tone: 'warn' };
  if (summary.pushReady === true) return { text: tf(t, 'chat.turn.summary.ready', 'Done · ready to push'), tone: 'ok' };
  if (summary.pushReady === false) return { text: tf(t, 'chat.turn.summary.notReady', 'Done · not ready to push'), tone: 'warn' };
  return { text: tf(t, 'chat.turn.summary.done', 'Done'), tone: 'muted' };
}

/**
 * A finished run's result, verdict first (XG 2026-10-20: one summary, from code): how
 * it ended and where it landed, then what was asked and what changed (before → now).
 * What to check is the attention card right below; when the turn has none (a landing
 * that was refused never sends it), the reasons it would show are listed here
 * (`showReasons`). The documents are in the output card. Model-written text (intent,
 * outcome): rendered plain.
 */
export function RunResult({ summary, showReasons = false }: { summary: PipelineSummaryContent; showReasons?: boolean }) {
  const { t } = useTranslation();
  const v = runVerdict(t, summary);
  const tone = v.tone === 'warn' ? 'text-amber-600 dark:text-amber-400' : v.tone === 'ok' ? 'text-[var(--color-text)]' : 'text-[var(--color-text-secondary)]';
  const label = 'shrink-0 w-12 text-[12px] text-[var(--color-text-muted)]';
  return (
    <section data-testid="run-result" aria-label={tf(t, 'chat.turn.runResult.title', 'Run result')} className="space-y-1">
      <div data-testid="summary-verdict" className={`text-[14px] font-medium ${tone}`}>
        {v.text}
        {summary.landedSha && (
          <span className="ml-2 font-mono text-[12.5px] font-normal text-[var(--color-text-muted)]">
            {tf(t, 'chat.turn.runResult.landed', 'on main · {{sha}}', { sha: summary.landedSha.slice(0, 8) })}
          </span>
        )}
      </div>
      {summary.intent && (
        <div data-testid="run-intent" className="flex gap-2 text-[13px] text-[var(--color-text-secondary)] min-w-0">
          <span className={label}>{tf(t, 'chat.turn.runResult.asked', 'Asked')}</span>
          <span className="min-w-0 break-words">{summary.intent}</span>
        </div>
      )}
      {summary.solved && (
        <div data-testid="run-solved" className="space-y-0.5 text-[13px] text-[var(--color-text-secondary)]">
          <div className="flex gap-2 min-w-0"><span className={label}>{tf(t, 'chat.turn.runResult.before', 'Before')}</span><span className="min-w-0 break-words">{summary.solved.before}</span></div>
          <div className="flex gap-2 min-w-0"><span className={label}>{tf(t, 'chat.turn.runResult.now', 'Now')}</span><span className="min-w-0 break-words text-[var(--color-text)]">{summary.solved.now}</span></div>
        </div>
      )}
      {showReasons && summary.pushReady === false && (summary.pushReasons?.length ?? 0) > 0 && (
        <ul data-testid="run-reasons" className={`${DIM} list-disc pl-5`}>
          {summary.pushReasons!.map((r, i) => (
            <li key={i}>{r.code ? tf(t, `chat.turn.attention.reasonCode.${r.code}`, r.text) : r.text}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The turn's ONE output card (chat turn spec v6 §8): a code-changes section (the
 * backend's turn_changes rows, ChangesCard) and a deliverables section (what the turn
 * produced, and a pipeline run's documents — listed only here). Either section alone;
 * neither → no card. A file is in one section only (the caller drops deliverables
 * from the changes rows).
 */
export function OutputCard({ changes, deliverables, summary }: {
  changes?: TurnChangesContent; deliverables: TurnDeliverable[]; summary?: PipelineSummaryContent;
}) {
  const { t } = useTranslation();
  const docs = summary?.docs ?? [];
  const nDeliv = deliverables.length + docs.filter((d) => !!d.absolutePath).length;
  if (!changes && !deliverables.length && !docs.length) return null;
  return (
    <section data-testid="output-card" className="space-y-3">
      {changes && <ChangesCard block={changes} />}
      {(deliverables.length > 0 || docs.length > 0) && (
        <div data-testid="output-deliverables">
          <div className="mb-1 flex items-baseline gap-2 text-[12px] font-medium text-[var(--color-text-muted)]">
            {nDeliv === 1 ? tf(t, 'chat.turn.output.deliverableOne', '1 deliverable') : tf(t, 'chat.turn.output.deliverables', '{{count}} deliverables', { count: nDeliv })}
          </div>
          {deliverables.length > 0 && (
            <ul className="divide-y divide-[var(--color-border)]">
              {deliverables.map((f) => <DeliverableTile key={f.absolutePath} file={f} />)}
            </ul>
          )}
          {docs.length > 0 && <ul className={deliverables.length ? 'mt-1' : ''}>{docs.map((d) => <SummaryDocRow key={d.name} doc={d} />)}</ul>}
        </div>
      )}
    </section>
  );
}
