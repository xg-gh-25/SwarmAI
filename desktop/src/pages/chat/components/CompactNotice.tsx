/**
 * CompactNotice — the context-compaction divider in the chat stream.
 *
 * One row per compaction, updated in place: "compacting…" while the CLI
 * summarizes, then "Conversation above has been summarized · 750K → 12K · 55s
 * · auto" with a one-line explanation and a collapsed "Conversation Summary"
 * card that expands in place to the summary markdown (max two levels: the
 * divider, then the card). No spinner: the turn already shows the one live
 * spinner. Color only for the failure (it needs attention); normal completion
 * stays neutral grey. A legacy text-only notice (no record) renders its own
 * text: first line as the title, the rest as the explanation.
 *
 * @exports CompactNotice
 */
import { memo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Message } from '../../../types';
import MarkdownRenderer from '../../../components/common/MarkdownRenderer';
import { formatTokensK, toCompactionInfo } from '../compaction';
import { tf } from '../tf';

interface CompactNoticeProps {
  message: Message;
  /** The tab is streaming AND this divider is in the current turn: a
   *  "compacting" divider is only true then. */
  isStreaming: boolean;
}

function CompactNoticeImpl({ message, isStreaming }: CompactNoticeProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const info = toCompactionInfo(message);
  const block = message.content.find((b) => b.type === 'text');
  const text = block && 'text' in block ? String(block.text ?? '') : '';

  let icon = 'info';
  let title: string;
  let detail = '';
  let meta = '';
  let failed = false;
  if (!info) {
    const [first, ...rest] = (text || tf(t, 'chat.compact.done', 'Conversation above has been summarized')).split('\n');
    title = first;
    detail = rest.join('\n');
  } else if (info.phase === 'failed') {
    failed = true;
    icon = 'error';
    title = tf(t, 'chat.compact.failed', 'Context compaction failed');
    detail = tf(t, 'chat.compact.failedDetail', 'The conversation was not summarized; the turn continues with the full context.');
  } else if (info.phase === 'running') {
    icon = 'compress';
    title = isStreaming
      ? tf(t, 'chat.compact.running', 'Compacting conversation context…')
      : tf(t, 'chat.compact.interrupted', 'Context compaction did not finish');
    detail = isStreaming
      ? tf(t, 'chat.compact.runningDetail', 'Context reached the compaction threshold. Earlier messages are being summarized; this turn continues automatically when it is done.')
      : '';
  } else {
    title = tf(t, 'chat.compact.done', 'Conversation above has been summarized');
    detail = tf(t, 'chat.compact.doneDetail', 'The conversation reached the context threshold. Earlier messages have been summarized, and the summary is sent to the agent as context instead of the full text.');
    const parts: string[] = [];
    if (info.preTokens !== undefined && info.postTokens !== undefined) {
      parts.push(`${formatTokensK(info.preTokens)} → ${formatTokensK(info.postTokens)}`);
    }
    if (info.durationMs !== undefined) {
      parts.push(tf(t, 'chat.compact.duration', '{{seconds}}s', { seconds: Math.round(info.durationMs / 1000) }));
    }
    parts.push(tf(t, `chat.compact.trigger.${info.trigger}`, info.trigger));
    meta = parts.join(' · ');
  }
  const summary = info?.phase === 'done' ? info.summary : '';

  const tone = failed ? 'text-[var(--color-warning)]' : 'text-[var(--color-text-muted)]';
  return (
    <div data-testid="compact-notice" data-phase={info?.phase ?? 'legacy'} className="py-2">
      <div className={`flex items-center gap-2 text-xs ${tone}`}>
        <span className="material-symbols-outlined icon-sm shrink-0">{icon}</span>
        <span className="font-medium shrink-0">{title}</span>
        {meta && <span className="opacity-80 truncate">· {meta}</span>}
        <div className="flex-1 min-w-[24px] border-t border-dashed border-[var(--color-border)]" />
      </div>
      {detail && (
        <p className="mt-1 ml-[23px] text-xs leading-relaxed text-[var(--color-text-muted)] whitespace-pre-wrap">
          {detail}
        </p>
      )}
      {summary && (
        <div className="mt-2 ml-[23px] rounded-md border border-[var(--color-border)] bg-[var(--color-hover)]/40">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-hover)] rounded-md transition-colors text-left"
          >
            <span className="material-symbols-outlined icon-sm">
              {open ? 'expand_more' : 'chevron_right'}
            </span>
            <span className="material-symbols-outlined icon-sm">summarize</span>
            {tf(t, 'chat.compact.summary', 'Conversation Summary')}
          </button>
          {open && (
            <div data-testid="compact-summary" className="border-t border-[var(--color-border)] px-3 py-2 text-sm">
              <MarkdownRenderer content={summary} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export const CompactNotice = memo(CompactNoticeImpl);
