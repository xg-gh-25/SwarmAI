/**
 * TurnCards — the backend-written cards of a turn (chat display v7.1).
 *
 * Each has a fixed place in the turn, set by AssistantMessageView:
 * - RunAlignment     — a pipeline run's requirement + plan (`pipeline_run`): the
 *                      turn's alignment, at the top, labelled "Understanding".
 * - AttentionCard    — what the run needs the user to judge (`pipeline_attention`):
 *                      right after the "Result" divider (or in place while running).
 *                      Amber, the one coloured card: colour is for exceptions. What
 *                      the assistant settled without asking (questions it answered,
 *                      assumptions) is one group under ONE "{name} decided for
 *                      you" line, so the name appears at most once per card.
 * - DeliverablesCard — the files the turn produced (`turn_deliverables`), after
 *                      the outcome: type icon or image thumbnail, name, open in
 *                      Canvas, copy path.
 * All text in these blocks is model-written: rendered plain, never as markdown.
 *
 * @exports RunAlignment, AttentionCard, DeliverablesCard, middleEllipsis
 */
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { PipelineAttentionContent, PipelineAttentionItem, PipelineRunContent, TurnDeliverable } from '../../../types';
import { OPEN_FILE_EVENT, resolveImageSrc } from '../../../components/common/MarkdownRenderer';
import { fileIcon, fileIconColor } from '../../../utils/fileUtils';
import { copyToClipboard } from '../../../utils/clipboard';
import { tf } from '../tf';
import { ASSISTANT_NAME } from '../assistantName';

const LABEL = 'text-[11px] tracking-wide text-[var(--color-text-muted)]';

export function RunAlignment({ run }: { run: PipelineRunContent }) {
  const { t } = useTranslation();
  const meta = [
    run.profile,
    run.stages.length ? tf(t, 'chat.turn.run.stages', '{{count}} stages', { count: run.stages.length }) : null,
  ].filter(Boolean).join(' · ');
  return (
    <div data-testid="turn-run">
      <div className={`mb-0.5 ${LABEL}`}>{tf(t, 'chat.turn.alignment', 'Understanding')}</div>
      <p className="text-[14.5px] leading-relaxed text-[var(--color-text)] whitespace-pre-wrap break-words">{run.requirement}</p>
      <div className="mt-1 text-[11.5px] text-[var(--color-text-muted)]">
        Pipeline <code className="font-mono text-[11px]">{run.runId}</code>{meta ? ` · ${meta}` : ''}
      </div>
    </div>
  );
}

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
        <span className="text-red-500">{tf(t, 'chat.turn.attention.notPushReady', 'Not ready to push')}</span>
        {item.reasons.length > 0 && <span className={DIM}> · {item.reasons.join(' · ')}</span>}
      </>
    );
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
  const name = baseName(file.path || file.absolutePath);
  const thumb = IMAGE_EXT.test(name) && !thumbFailed ? resolveImageSrc(file.absolutePath) : undefined;
  const open = () => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.absolutePath } }));
  const copy = async () => {
    if (await copyToClipboard(file.absolutePath)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  const action = 'text-[11.5px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:underline';
  return (
    <li data-testid="deliverable" data-path={file.absolutePath} className="min-w-0 rounded-md border border-[var(--color-border)] p-2">
      <button type="button" onClick={open} title={file.absolutePath} className="w-full flex items-center gap-2 min-w-0 text-left">
        {thumb ? (
          <img src={thumb} alt="" onError={() => setThumbFailed(true)} className="w-10 h-10 rounded object-cover shrink-0" />
        ) : (
          <span
            aria-hidden="true"
            className="material-symbols-outlined text-[28px] leading-none shrink-0"
            style={{ color: fileIconColor(name), filter: 'saturate(0.55)' }}
          >
            {fileIcon(name)}
          </span>
        )}
        <span className="min-w-0 font-mono text-[12px] text-[var(--color-text-secondary)] truncate">{middleEllipsis(name)}</span>
      </button>
      <div className="mt-1.5 flex items-center gap-3">
        <button type="button" onClick={open} className={action}>{tf(t, 'chat.turn.deliverables.open', 'Open in Canvas')}</button>
        <button type="button" onClick={copy} className={action}>
          {copied ? tf(t, 'chat.turn.deliverables.copied', 'Copied') : tf(t, 'chat.turn.deliverables.copyPath', 'Copy path')}
        </button>
      </div>
    </li>
  );
}

export function DeliverablesCard({ files }: { files: TurnDeliverable[] }) {
  const { t } = useTranslation();
  return (
    <section data-testid="turn-deliverables">
      <div className={`mb-1.5 ${LABEL}`}>{tf(t, 'chat.turn.deliverables.title', 'Produced this turn · {{count}}', { count: files.length })}</div>
      <ul className="grid gap-2 grid-cols-[repeat(auto-fill,minmax(190px,1fr))]">
        {files.map((f) => <DeliverableTile key={f.absolutePath} file={f} />)}
      </ul>
    </section>
  );
}
