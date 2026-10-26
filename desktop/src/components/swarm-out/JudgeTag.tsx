/**
 * Autonomy Judge — one tag per run; a click opens the five-dimension matrix.
 *
 * The tag shows the worst verdict, not an average: any BLOCK makes the run BLOCK
 * (Run Isolation and Quality are hard constraints), otherwise the number of WARNs,
 * otherwise ✓. N/A and PENDING dimensions do not count. The popover lists every
 * dimension with its role, verdict and real numbers, under one summary sentence.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { DIMENSIONS, DIMENSION_ROLE, type RunJudge, type Verdict } from './mockData';
import { judgeState, judgeSummary } from './judge';

const VERDICT_LABEL: Record<Verdict, string> = { PASS: 'PASS', WARN: 'WARN', BLOCK: 'BLOCK', NA: 'N/A', PENDING: '还没到' };
const VERDICT_COLOR: Record<Verdict, string> = {
  PASS: 'var(--color-status-success)',
  WARN: 'var(--color-status-warning)',
  BLOCK: 'var(--color-status-error)',
  NA: 'var(--color-text-dim)',
  PENDING: 'var(--color-text-dim)',
};

export function JudgeTag({ judge, title }: { judge: RunJudge; title: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const tagRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const popId = useId();
  const { state, warns } = judgeState(judge.verdicts);

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    setPos(null);
    if (refocus) tagRef.current?.focus();
  }, []);

  // Place the popover once it has rendered, from its measured height: below the tag
  // when it fits, otherwise above it, never over it; then move focus into it.
  useLayoutEffect(() => {
    if (!open || !tagRef.current || !popRef.current) return;
    const r = tagRef.current.getBoundingClientRect();
    const h = popRef.current.offsetHeight;
    const w = popRef.current.offsetWidth;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 12));
    const fitsBelow = r.bottom + 6 + h <= window.innerHeight - 8;
    const top = fitsBelow ? r.bottom + 6 : Math.max(8, r.top - h - 6);
    setPos({ left, top });
    popRef.current.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (popRef.current?.contains(e.target as Node) || tagRef.current?.contains(e.target as Node)) return;
      close(false);
    };
    // Capture phase + stopPropagation: Escape closes this popover only, it must not
    // also reach the chat input's global Escape handler (which grabs focus).
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      close(true);
    };
    // A fixed-position popover would stay put while its row scrolls away: close it.
    const onMove = (e: Event) => {
      if (popRef.current && e.target instanceof Node && popRef.current.contains(e.target)) return;
      close(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [open, close]);

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (open) close(false);
    else setOpen(true);
  };

  const symbol = state === 'BLOCK' ? '✕ BLOCK' : state === 'WARN' ? `! ${warns} WARN` : '✓';
  const symbolColor = state === 'BLOCK' ? VERDICT_COLOR.BLOCK : state === 'WARN' ? VERDICT_COLOR.WARN : VERDICT_COLOR.PASS;

  return (
    <>
      <button
        ref={tagRef}
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? popId : undefined}
        aria-label={`Autonomy Judge: ${symbol}`}
        data-testid="judge-tag"
        className="inline-flex items-center gap-1.5 h-[20px] px-2.5 rounded-full border text-[11.5px] whitespace-nowrap bg-[var(--color-card)] text-[var(--color-text-secondary)] border-[var(--color-border)] hover:border-[var(--color-primary)] hover:text-[var(--color-text)]"
      >
        Autonomy Judge <b style={{ color: symbolColor }}>{symbol}</b>
        {judge.live && <span className="text-[10.5px] text-[var(--color-text-dim)]">到目前</span>}
      </button>
      {open && createPortal(
        <div
          ref={popRef}
          id={popId}
          role="dialog"
          tabIndex={-1}
          aria-label={`Autonomy Judge · ${title}`}
          data-testid="judge-popover"
          style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: 'hidden' }}
          className="fixed z-[60] w-[440px] max-w-[calc(100vw-24px)] outline-none rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-xl px-4 pt-3.5 pb-3 text-[var(--color-text)]"
        >
          <div className="flex items-baseline gap-2">
            <b className="text-[13.5px]">Autonomy Judge</b>
            <span className="text-[12px] text-[var(--color-text-muted)] truncate">{title}</span>
          </div>
          <div className="font-mono text-[11.5px] text-[var(--color-text-muted)] mt-0.5">{judge.outcome}</div>
          <p className="my-2.5 text-[13px] leading-relaxed">{judgeSummary(judge)}</p>
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="text-[11px] text-[var(--color-text-dim)] text-left">
                <th className="font-medium px-1.5 pb-1">维度</th>
                <th className="font-medium px-1.5 pb-1">地位</th>
                <th className="font-medium px-1.5 pb-1">结论</th>
                <th className="font-medium px-1.5 pb-1">数字</th>
              </tr>
            </thead>
            <tbody>
              {judge.verdicts.map((v, i) => (
                <tr key={DIMENSIONS[i]} className="border-t border-[var(--color-border)] align-top">
                  <td className="px-1.5 py-1.5 font-medium whitespace-nowrap">{DIMENSIONS[i]}</td>
                  <td className="px-1.5 py-1.5 text-[11.5px] text-[var(--color-text-muted)] whitespace-nowrap">{DIMENSION_ROLE[i]}</td>
                  <td className="px-1.5 py-1.5 text-[11.5px] font-bold whitespace-nowrap" style={{ color: VERDICT_COLOR[v] }}>{VERDICT_LABEL[v]}</td>
                  <td className="px-1.5 py-1.5 text-[var(--color-text-secondary)]">{judge.numbers[i]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 text-[11.5px] text-[var(--color-text-dim)]">
            由代码从 run 事件生成（run_audit.json），不做综合分；任一硬约束 BLOCK 整体 BLOCK。
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
