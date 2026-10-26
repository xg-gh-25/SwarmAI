/**
 * TurnCards — the backend-written cards of a turn (chat turn spec v6).
 *
 * Each has a fixed place in the turn, set by AssistantMessageView:
 * - AttentionCard — unresolved gates / not-push-ready causes that require action,
 *                   immediately after the machine run status.
 * - AssumptionsDisclosure — self-answered decisions and still-open assumptions,
 *                           folded after Output and directly before Next steps.
 * - OutputCard — the turn's code changes and deliverables (files and remote actions).
 * - ProductList — a products zone's rows in the turn (session-products design §3.1).
 * All text in these blocks is model-written: rendered plain, never as markdown.
 *
 * @exports AttentionCard, AssumptionsDisclosure, isVerificationAttentionItem,
 *          OutputCard, ProductList, remoteProducts, middleEllipsis
 */
import { useContext, useState, type MouseEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type {
  PipelineAttentionContent, PipelineAttentionItem, PipelineSummaryContent, PipelineSummaryDoc, TurnDeliverable, TurnChangesContent,
  TurnProductContent,
} from '../../../types';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { FileTypeIcon } from '../../../components/common/FileTypeIcon';
import { copyToClipboard } from '../../../utils/clipboard';
import { openExternal } from '../../../utils/openExternal';
import { tf } from '../tf';
import { docMeta } from '../runDocMeta';
import { ReadOnlyContext } from '../readOnlyContext';
import { ChangesCard } from './ChangesCard';


/** A verdict that blocks is red; anything else that needs a look stays amber. */
const isBlocking = (v: string) => /BLOCK|FAIL|REJECT/i.test(v);
/** Secondary audit context, not an action blocker. */
export const isVerificationAttentionItem = (i: PipelineAttentionItem) =>
  i.kind === 'self_answered' || i.kind === 'assumption';

const DIM = 'text-[12.5px] text-[var(--color-text-muted)]';
const TAG = 'mr-1.5 text-[11px] font-medium uppercase tracking-[0.05em] text-[var(--color-text-muted)]';

function AttentionRow({ item }: { item: PipelineAttentionItem }) {
  const { t } = useTranslation();
  let line: ReactNode;
  let note: ReactNode = null;
  if (item.kind === 'self_answered') {
    line = (
      <>
        <span className={TAG}>{tf(t, 'chat.turn.assumptions.decision', 'Decision')}</span>
        {item.question}
        {item.answer && <> → {item.answer}</>}
      </>
    );
    note = item.reason && tf(t, 'chat.turn.attention.reason', 'Why: {{text}}', { text: item.reason });
  } else if (item.kind === 'assumption') {
    line = (
      <>
        <span className={TAG}>{tf(t, 'chat.turn.assumptions.assumption', 'Assumption')}</span>
        {item.statement}
      </>
    );
  } else if (item.kind === 'gate') {
    line = <>{item.name} · <span className={isBlocking(item.verdict) ? 'text-red-500' : ''}>{item.verdict}</span></>;
    note = item.detail;
  } else {
    line = (
      <>
        {/* not_landed: the change is not on main. A legacy not_push_ready item
            (before 2026-10-26) shows only its reasons: no push verdict. */}
        {item.kind === 'not_landed' && (
          <span className="text-amber-600 dark:text-amber-400">{tf(t, 'chat.turn.attention.notLanded', 'Not landed')}</span>
        )}
        {item.reasons.length > 0 && (
          <ul data-testid="not-ready-reasons" className={`${DIM} list-disc pl-5`}>
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

/** Immediate action only. Verification context has its own later disclosure. */
export function AttentionCard({ block }: { block: PipelineAttentionContent }) {
  const { t } = useTranslation();
  const action = block.items.filter((item) => !isVerificationAttentionItem(item));
  if (action.length === 0) return null;
  return (
    <section
      data-testid="turn-attention"
      aria-label={tf(t, 'chat.turn.attention.title', 'For you to check')}
      className="border-l-2 border-amber-500/70 bg-amber-500/[0.06] rounded-r-md pl-3 pr-2 py-2"
    >
      <div className="text-[12px] font-medium text-amber-600 dark:text-amber-400">
        {tf(t, 'chat.turn.attention.title', 'For you to check')}
      </div>
      <ul className="mt-1">
        {action.map((item, i) => <AttentionRow key={`a-${i}`} item={item} />)}
      </ul>
    </section>
  );
}

/** Optional context for judging the proposed next step, quiet until requested. */
export function AssumptionsDisclosure({ block }: { block: PipelineAttentionContent }) {
  const { t } = useTranslation();
  const verification = block.items.filter(isVerificationAttentionItem);
  const more = block.more ?? 0;
  if (verification.length === 0 && more === 0) return null;
  return (
    <details data-testid="turn-assumptions" className="group/assumptions text-[12.5px] text-[var(--color-text-muted)]">
      <summary className="flex w-fit cursor-pointer list-none items-center gap-1.5 rounded py-0.5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50">
        <span aria-hidden="true" className="material-symbols-outlined icon-sm transition-transform group-open/assumptions:rotate-90">chevron_right</span>
        <span>{tf(t, 'chat.turn.assumptions.title', 'Assumptions to verify')}</span>
      </summary>
      <ul className="mt-1 ml-[22px] space-y-1">
        {verification.map((item, i) => <AttentionRow key={`v-${i}`} item={item} />)}
      </ul>
      {more > 0 && (
        <div className={`mt-1 ml-[22px] ${DIM}`}>
          {tf(t, 'chat.turn.attention.more', '+{{count}} more in the run report', { count: more })}
        </div>
      )}
    </details>
  );
}

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


type RemoteProduct = Extract<TurnProductContent, { product: 'remote' }>;

/** A path's folder, home shown as ~. */
function folderOf(abs: string): string {
  const dir = abs.replace(/\/[^/]*$/, '') || '/';
  return dir.replace(/^\/(?:Users|home)\/[^/]+(?=\/|$)/, '~');
}

/** A folder cut in the MIDDLE, keeping its root and where it ends
 *  ("~/.swarm-ai/…/2-understanding"); the end says which folder it is. */
export function shortFolder(dirPath: string): string {
  const dir = folderOf(`${dirPath.replace(/\/+$/, '')}/x`);
  const segs = dir.split('/').filter(Boolean);
  const keep = segs[0] === '~' ? 2 : 1;          // "~/<first>" or "/<first>"
  if (segs.length - keep - 1 < 2) return dir;    // nothing worth cutting
  const head = `${dir.startsWith('/') ? '/' : ''}${segs.slice(0, keep).join('/')}`;
  return `${head}/…/${segs[segs.length - 1]}`;
}

/** THE product list (v3, 2026-10-26, Gawan), one look in the turn and on the output
 *  card: one bordered box that hugs its widest row, a file per line; the full name as a
 *  link (never shortened), its folder after it, Copy right after that. The whole row
 *  opens the file in the Canvas, so there is no separate Open button. */
const TILE_BOX = 'w-fit max-w-full rounded-[10px] border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden';
const TILE_LIST = 'flex flex-col divide-y divide-[var(--color-border)]';
const CARD_TILE = 'group flex items-center gap-2.5 min-w-0 px-3 py-1.5 transition-colors';
const CARD_TILE_ACT = 'cursor-pointer hover:bg-[var(--color-hover)]';
const ROW_NAME = 'break-all text-[13px] text-primary';
const ROW_DIR = 'min-w-[3ch] shrink-[100] truncate font-mono text-[11.5px] text-[var(--color-text-dim)]';
const ROW_BTN = 'icon-btn shrink-0 -ml-1 text-[var(--color-text-dim)] group-hover:text-[var(--color-text-secondary)]';
const DELIV_TITLE = 'text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text-dim)]';

/** One file the turn produced: its type badge, the name as a link, its folder;
 *  Copy path right after it. */
function DeliverableCardTile({ file }: { file: TurnDeliverable }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const readOnly = useContext(ReadOnlyContext);
  const name = baseName(file.path || file.absolutePath);
  const open = () => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.absolutePath } }));
  const copy = async (e: MouseEvent) => {
    e.stopPropagation();
    if (await copyToClipboard(file.absolutePath)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  const Head = readOnly ? 'div' : 'button';
  const headProps = readOnly ? {} : { type: 'button' as const, onClick: (e: MouseEvent) => { e.stopPropagation(); open(); } };
  const copyLabel = copied ? tf(t, 'chat.turn.deliverables.copied', 'Copied') : tf(t, 'chat.turn.deliverables.copyPath', 'Copy path');
  return (
    <li data-testid="deliverable" data-path={file.absolutePath} onClick={readOnly ? undefined : open}
      className={`${CARD_TILE} ${readOnly ? '' : CARD_TILE_ACT}`}>
      <Head {...headProps} title={file.absolutePath} className="min-w-0 flex items-center gap-2.5 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50">
        <FileTypeIcon name={name} size="sm" />
        {/* the name, then its folder in grey on the same line; hover: the full path */}
        <span className="min-w-0 flex items-baseline gap-2">
          <span data-testid="deliverable-name" className={`${ROW_NAME} ${readOnly ? '' : 'group-hover:underline'}`}>{name}</span>
          <span data-testid="deliverable-dir" title={file.absolutePath} className={ROW_DIR}>{shortFolder(file.absolutePath.replace(/\/[^/]*$/, '') || '/')}</span>
        </span>
      </Head>
      {!readOnly && (
        <button type="button" onClick={copy} aria-label={copyLabel} title={copyLabel} className={ROW_BTN}>
          <span aria-hidden="true" className="material-symbols-outlined icon-sm">{copied ? 'check' : 'content_copy'}</span>
        </button>
      )}
    </li>
  );
}

/** A remote action in the same row: what was done and on what; the row opens the
 *  link only when the backend found one in the tool's own output. */
function RemoteProductCardTile({ product }: { product: RemoteProduct }) {
  const { t } = useTranslation();
  const readOnly = useContext(ReadOnlyContext);
  const act = !!product.url && !readOnly;
  const go = () => void openExternal(product.url!);
  const label = tf(t, 'chat.turn.deliverables.openLink', 'Open link');
  return (
    <li data-testid="remote-product" data-tool={product.tool} onClick={act ? go : undefined}
      className={`${CARD_TILE} ${act ? CARD_TILE_ACT : ''}`}>
      <span aria-hidden="true" className="material-symbols-outlined icon-sm shrink-0 text-[var(--color-text-muted)]">send</span>
      <span className="min-w-0 flex items-baseline gap-2">
        <span className="break-words text-[13px] text-[var(--color-text)]">{product.action}</span>
        {product.target && <span className={ROW_DIR} title={product.target}>{product.target}</span>}
      </span>
      {act && (
        <button type="button" onClick={(e) => { e.stopPropagation(); go(); }} aria-label={label} title={product.url} className={ROW_BTN}>
          <span aria-hidden="true" className="material-symbols-outlined icon-sm">open_in_new</span>
        </button>
      )}
    </li>
  );
}


/** A products zone (design 2026-10-26-session-products.md §3.1): one row per file
 *  written (the deliverable look) and per remote action, in the order they happened. */
export function ProductList({ blocks }: { blocks: TurnProductContent[] }) {
  const seen = new Set<string>();
  const rows: ReactNode[] = [];
  for (const b of blocks) {
    if (b.product === 'file') {
      if (seen.has(b.absolutePath)) continue;
      seen.add(b.absolutePath);
      const file: TurnDeliverable = { path: b.path, absolutePath: b.absolutePath, kind: b.fileKind, operation: 'written' };
      rows.push(<DeliverableCardTile key={`f-${b.absolutePath}`} file={file} />);
    } else {
      rows.push(<RemoteProductCardTile key={`r-${b.toolUseId}`} product={b} />);
    }
  }
  return <div data-testid="product-box" className={TILE_BOX}><ul data-testid="product-list" className={TILE_LIST}>{rows}</ul></div>;
}

/** The turn's remote products (top-level and sub-agent), in order: the output card
 *  lists them beside the files (the files come from turn_deliverables). */
export function remoteProducts(products: TurnProductContent[]): RemoteProduct[] {
  return products.filter((p): p is RemoteProduct => p.product === 'remote');
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
        <span aria-hidden="true" className={`material-symbols-outlined icon-sm leading-none self-center shrink-0 ${there ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-dim)]'}`}>
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
  if (summary.landing === 'not_landed') return { text: tf(t, 'chat.turn.summary.notLanded', 'Done · not landed'), tone: 'warn' };
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
export function RunResult({ summary, showReasons = false, collapseDetails = false }: {
  summary: PipelineSummaryContent; showReasons?: boolean; collapseDetails?: boolean;
}) {
  const { t } = useTranslation();
  const v = runVerdict(t, summary);
  const tone = v.tone === 'warn' ? 'text-amber-600 dark:text-amber-400' : v.tone === 'ok' ? 'text-[var(--color-text)]' : 'text-[var(--color-text-secondary)]';
  const label = 'shrink-0 w-12 text-[12px] text-[var(--color-text-muted)]';
  const hasDetails = !!summary.intent || !!summary.solved;
  const detailRows = hasDetails ? (
    <div className="space-y-1">
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
    </div>
  ) : null;
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
      {collapseDetails && detailRows ? (
        <details data-testid="run-details" className="group/run-details text-[12.5px] text-[var(--color-text-muted)]">
          <summary className="flex w-fit cursor-pointer list-none items-center gap-1 rounded py-0.5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50">
            <span aria-hidden="true" className="material-symbols-outlined icon-sm transition-transform group-open/run-details:rotate-90">chevron_right</span>
            <span>{tf(t, 'chat.turn.runResult.details', 'Run details')}</span>
          </summary>
          <div className="mt-1 ml-[19px]">{detailRows}</div>
        </details>
      ) : detailRows}
      {summary.auditCard && (
        <pre data-testid="run-audit-card" aria-label={tf(t, 'chat.turn.runResult.audit', 'Run audit')}
          className="overflow-x-auto whitespace-pre rounded bg-[var(--color-bg-chrome)] p-2 font-mono text-[12px] leading-snug text-[var(--color-text-secondary)]">
          {summary.auditCard}
        </pre>
      )}
      {showReasons && summary.landing === 'not_landed' && summary.notLandedReason && (
        <ul data-testid="run-reasons" className={`${DIM} list-disc pl-5`}>
          <li>{summary.notLandedReason}</li>
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
export function OutputCard({ changes, deliverables, summary, remote = [] }: {
  changes?: TurnChangesContent; deliverables: TurnDeliverable[]; summary?: PipelineSummaryContent;
  /** The turn's remote products (turn_product blocks), in order. */
  remote?: RemoteProduct[];
}) {
  const { t } = useTranslation();
  const docs = summary?.docs ?? [];
  const nDeliv = deliverables.length + remote.length + docs.filter((d) => !!d.absolutePath).length;
  if (!changes && !deliverables.length && !remote.length && !docs.length) return null;
  const title = nDeliv === 1 ? tf(t, 'chat.turn.output.deliverableOne', '1 deliverable') : tf(t, 'chat.turn.output.deliverables', '{{count}} deliverables', { count: nDeliv });
  return (
    <section data-testid="output-card" className="space-y-3">
      {changes && <ChangesCard block={changes} />}
      {(deliverables.length > 0 || remote.length > 0 || docs.length > 0) && (
        <div data-testid="output-deliverables">
          {(deliverables.length > 0 || remote.length > 0) ? (
            <div data-testid="product-box" className={TILE_BOX}>
              <div className={`px-3 py-1.5 border-b border-[var(--color-border)] ${DELIV_TITLE}`}>{title}</div>
              <ul className={TILE_LIST}>
                {deliverables.map((f) => <DeliverableCardTile key={f.absolutePath} file={f} />)}
                {remote.map((r) => <RemoteProductCardTile key={`r-${r.toolUseId}`} product={r} />)}
              </ul>
            </div>
          ) : (
            <div className={`mb-2 ${DELIV_TITLE}`}>{title}</div>
          )}
          {docs.length > 0 && <ul className={deliverables.length || remote.length ? 'mt-2' : ''}>{docs.map((d) => <SummaryDocRow key={d.name} doc={d} />)}</ul>}
        </div>
      )}
    </section>
  );
}
