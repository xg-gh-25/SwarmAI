/**
 * TurnCards — the backend-written cards of a turn (chat turn spec v6).
 *
 * Each has a fixed place in the turn, set by AssistantMessageView:
 * - RunLine — how a pipeline run ended: one grey line, its details expanded under it
 *             (design 2026-10-26-turn-cards-unified.md §5.1). No run-result or
 *             attention card (Gawan 2026-10-26: the agent says it in its answer).
 * - AssumptionsDisclosure — self-answered decisions and still-open assumptions,
 *                           folded after Output and directly before Next steps.
 * - OutputCard — the turn's code changes and deliverables (files and remote actions).
 * - ProductList — a products zone's rows in the turn (session-products design §3.1).
 * All text in these blocks is model-written: rendered plain, never as markdown.
 *
 * @exports RunLine, AssumptionsDisclosure, isVerificationAttentionItem,
 *          OutputCard, ProductList, remoteProducts, middleEllipsis, shortFolder
 */
import { useContext, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type {
  PipelineAttentionContent, PipelineAttentionItem, PipelineSummaryContent, TurnDeliverable, TurnChangesContent,
  TurnProductContent,
} from '../../../types';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { openExternal } from '../../../utils/openExternal';
import { tf } from '../tf';
import { ReadOnlyContext } from '../readOnlyContext';
import { ChangesCard } from './ChangesCard';
import { FileItemRow, ItemBox, ItemRow, ITEM_ACTION, shortFolder } from './ItemRows';

export { shortFolder };


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
  } else {
    return null;      // gates / not landed: not shown in the chat (the run line has them)
  }
  return (
    <li data-testid="attention-item" data-kind={item.kind} className="py-0.5 text-[13.5px] text-[var(--color-text)] break-words">
      <div>{line}</div>
      {note && <div className={DIM}>{note}</div>}
    </li>
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

/** THE product list (2026-10-26, Gawan), one look in the turn and on the output card:
 *  the shared ItemBox / ItemRow (./ItemRows). The full name as a link (never cut, never
 *  wrapped), its folder after it, Copy right after that. The whole row opens the file
 *  in the Canvas, so there is no separate Open button. */

/** A remote action in the same row: what was done and on what; the row opens the
 *  link only when the backend found one in the tool's own output. */
function RemoteProductCardTile({ product }: { product: RemoteProduct }) {
  const { t } = useTranslation();
  const readOnly = useContext(ReadOnlyContext);
  const act = !!product.url && !readOnly;
  const go = () => void openExternal(product.url!);
  const label = tf(t, 'chat.turn.deliverables.openLink', 'Open link');
  return (
    <ItemRow testId="remote-product" data={{ tool: product.tool }}
      badge={<span aria-hidden="true" className="material-symbols-outlined icon-sm shrink-0 text-[var(--color-text-muted)]">send</span>}
      name={product.action} folder={product.target || undefined} folderTitle={product.target || undefined}
      onOpen={act ? go : undefined} rowOpens openTitle={product.url}
      actions={act && (
        <button type="button" onClick={(e) => { e.stopPropagation(); go(); }} aria-label={label} title={product.url} className={ITEM_ACTION}>
          <span aria-hidden="true" className="material-symbols-outlined icon-sm">open_in_new</span>
        </button>
      )} />
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
      rows.push(<FileItemRow key={`f-${b.absolutePath}`} testId="deliverable" file={file} />);
    } else {
      rows.push(<RemoteProductCardTile key={`r-${b.toolUseId}`} product={b} />);
    }
  }
  return <div data-testid="product-list"><ItemBox testId="product-box">{rows}</ItemBox></div>;
}

/** The turn's remote products (top-level and sub-agent), in order: the output card
 *  lists them beside the files (the files come from turn_deliverables). */
export function remoteProducts(products: TurnProductContent[]): RemoteProduct[] {
  return products.filter((p): p is RemoteProduct => p.product === 'remote');
}

const VERDICT_TONE: Record<string, string> = {
  BLOCK: 'text-[var(--color-status-error)]', FAIL: 'text-[var(--color-status-error)]',
  WARN: 'text-[var(--color-status-warning)]',
};

/**
 * How a pipeline run ended, as ONE grey line (design 2026-10-26-turn-cards-unified.md
 * §5, §5.1): `<run id> · <status> · landed <sha> | not landed · N documents`, no
 * frame, no colour. Its details sit right under it, expanded by default (Gawan
 * 2026-10-26); clicking the line folds them (per message, not kept): what was asked,
 * why it did not land (only then), the five audit rows (only WARN / BLOCK coloured, on
 * the verdict word), the REQUIRED documents the run did not write, and REPORT.md.
 * An older block without audit rows shows the backend's audit card text. The run's
 * documents themselves are rows on the output card. Model-written text: plain.
 */
export function RunLine({ summary }: { summary: PipelineSummaryContent }) {
  const { t } = useTranslation();
  const readOnly = useContext(ReadOnlyContext);
  const [open, setOpen] = useState(true);
  const docs = summary.docs ?? [];
  const present = docs.filter((d) => !!d.absolutePath);
  const missing = docs.filter((d) => d.required && !d.absolutePath);
  const report = present.find((d) => d.name === 'REPORT.md');
  const status = summary.status ? tf(t, `chat.turn.run.status.${summary.status}`, summary.status) : null;
  const landing = summary.landing === 'landed' || summary.landing === 'human_override'
    ? (summary.landedSha
      ? tf(t, 'chat.turn.run.landedSha', 'landed {{sha}}', { sha: summary.landedSha.slice(0, 8) })
      : tf(t, 'chat.turn.run.landed', 'landed'))
    : summary.landing === 'not_landed' ? tf(t, 'chat.turn.run.notLanded', 'not landed') : null;
  const ndocs = present.length === 1 ? tf(t, 'chat.turn.run.docOne', '1 document')
    : present.length ? tf(t, 'chat.turn.run.docs', '{{count}} documents', { count: present.length }) : null;
  const line = [summary.runId, status, landing, ndocs].filter(Boolean).join(' · ');
  const K = 'shrink-0 w-20 text-[12px] text-[var(--color-text-dim)]';
  const V = 'min-w-0 text-[12.5px] text-[var(--color-text-secondary)] break-words';
  const openReport = () => report && document.dispatchEvent(
    new CustomEvent(OPEN_FILE_EVENT, { detail: { path: report.absolutePath } }));
  return (
    <section data-testid="run-summary" aria-label={tf(t, 'chat.turn.run.title', 'Pipeline run')}>
      <button type="button" data-testid="run-line" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-left text-[12.5px] text-[var(--color-text-dim)] hover:text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50">
        <span aria-hidden="true" className={`material-symbols-outlined icon-sm transition-transform ${open ? 'rotate-90' : ''}`}>chevron_right</span>
        <span className="font-mono">{line}</span>
      </button>
      {open && (
        <div data-testid="run-details" className="mt-1 ml-[19px] space-y-1">
          {summary.intent && (
            <div data-testid="run-intent" className="flex gap-3">
              <span className={K}>{tf(t, 'chat.turn.run.asked', 'Asked')}</span><span className={V}>{summary.intent}</span>
            </div>
          )}
          {summary.landing === 'not_landed' && summary.notLandedReason && (
            <div data-testid="run-not-landed" className="flex gap-3">
              <span className={K}>{tf(t, 'chat.turn.run.notLandedLabel', 'Not landed')}</span><span className={V}>{summary.notLandedReason}</span>
            </div>
          )}
          {(summary.audit?.length || summary.auditCard) && (
            <div className="flex gap-3">
              <span className={K}>{tf(t, 'chat.turn.run.audit', 'Audit')}</span>
              {summary.audit?.length ? (
                <div className="min-w-0 grid grid-cols-[auto_auto_minmax(0,1fr)] gap-x-2.5 gap-y-0.5 text-[12px]">
                  {summary.audit.map((r) => (
                    <div key={r.dim} data-testid="run-audit-row" data-dim={r.dim} className="contents">
                      <span className="whitespace-nowrap text-[var(--color-text-secondary)]">{r.dim}</span>
                      <span data-testid="run-audit-verdict"
                        className={`whitespace-nowrap font-mono text-[10.5px] font-semibold tracking-[0.04em] ${VERDICT_TONE[r.verdict] ?? 'text-[var(--color-text-dim)]'}`}>{r.verdict}</span>
                      <span title={r.detail} className="min-w-0 truncate text-[var(--color-text-dim)]">{r.detail}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <pre data-testid="run-audit-card"
                  className="min-w-0 overflow-x-auto whitespace-pre font-mono text-[12px] leading-snug text-[var(--color-text-secondary)]">{summary.auditCard}</pre>
              )}
            </div>
          )}
          {missing.length > 0 && (
            <div data-testid="run-missing" className="flex gap-3">
              <span className={K}>{tf(t, 'chat.turn.run.missing', 'Missing')}</span>
              <span className={V}>{missing.map((d) => d.name).join(', ')} · {tf(t, 'chat.turn.run.notWritten', 'not written by this run')}</span>
            </div>
          )}
          {report && (
            <div className="flex gap-3">
              <span className={K}>{tf(t, 'chat.turn.run.report', 'Full report')}</span>
              {readOnly ? <span className={V}>REPORT.md</span> : (
                <button type="button" data-testid="run-report-link" onClick={openReport} title={report.absolutePath ?? undefined}
                  className="text-[12.5px] text-primary hover:underline">REPORT.md</button>
              )}
            </div>
          )}
        </div>
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
export function OutputCard({ changes, deliverables, summary, remote = [], omitted = 0 }: {
  changes?: TurnChangesContent; deliverables: TurnDeliverable[]; summary?: PipelineSummaryContent;
  /** The turn's remote products (turn_product blocks), in order. */
  remote?: RemoteProduct[];
  /** Deliverable files the backend did not list (it keeps the first 12). */
  omitted?: number;
}) {
  const { t } = useTranslation();
  // the run's documents it wrote; one it did not write is not a deliverable (the run
  // line names a missing required one)
  const runDocs = (summary?.docs ?? []).filter((d) => !!d.absolutePath);
  const nDeliv = deliverables.length + remote.length + runDocs.length + omitted;
  if (!changes && !nDeliv) return null;
  const title = nDeliv === 1 ? tf(t, 'chat.turn.output.deliverableOne', '1 deliverable') : tf(t, 'chat.turn.output.deliverables', '{{count}} deliverables', { count: nDeliv });
  return (
    <section data-testid="output-card" className="space-y-3">
      {changes && <ChangesCard block={changes} />}
      {nDeliv > 0 && (
        <div data-testid="output-deliverables">
          <ItemBox testId="product-box" title={title}>
            {runDocs.map((d) => <FileItemRow key={d.absolutePath} testId="deliverable" data={{ name: d.name }}
              file={{ path: d.name, absolutePath: d.absolutePath! }} />)}
            {deliverables.map((f) => <FileItemRow key={f.absolutePath} testId="deliverable" file={f} />)}
            {remote.map((r) => <RemoteProductCardTile key={`r-${r.toolUseId}`} product={r} />)}
            {omitted > 0 && (
              <div data-testid="deliverables-omitted" className="text-small text-[var(--color-text-muted)] px-3 py-1">
                {tf(t, 'chat.turn.changes.omitted', '{{n}} more files are not listed.', { n: omitted })}
              </div>
            )}
          </ItemBox>
        </div>
      )}
    </section>
  );
}
