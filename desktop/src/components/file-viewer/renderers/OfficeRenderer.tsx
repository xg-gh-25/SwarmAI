/**
 * OfficeRenderer — read-only preview of docx / xlsx / xlsm / pptx
 * (docs/design/canvas-file-types.md P1), from GET /workspace/file/preview.
 *
 * - sheets: a tab per sheet, the table drawn by the existing CsvRenderer (rows
 *   passed through Papa.unparse — one table implementation); "first N of M rows"
 *   when the backend capped it.
 * - slides: a slide-by-slide outline (title, text, tables, speaker notes, picture
 *   count) — no slide drawing, said on the card.
 * - html (docx): the document in an iframe with sandbox="" (no scripts, no forms,
 *   no same-origin) — mammoth's HTML is not sanitized, so it never enters our DOM.
 * - any failure (not previewable, broken, too slow): the info card, with the
 *   reason, so Open in default app / Attach / Copy path stay one click away.
 *
 * @exports default OfficeRenderer
 */
import { lazy, Suspense, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import Papa from 'papaparse';
import api from '../../../services/api';
import { getApiBaseUrl } from '../../../services/tauri';

const CsvRenderer = lazy(() => import('./CsvRenderer'));
const UnsupportedRenderer = lazy(() => import('./UnsupportedRenderer'));

interface Sheet { name: string; rows: string[][]; totalRows: number; totalCols: number; truncated: boolean }
interface Slide { index: number; title: string; body: string[]; tables: string[][][]; notes: string; images: number }
type Preview =
  | { kind: 'sheets'; sheets: Sheet[]; totalSheets: number }
  | { kind: 'slides'; slides: Slide[] }
  | { kind: 'html' };

interface Props {
  filePath: string;
  fileName: string;
  fileSize: number;
  absolutePath?: string;
  sessionId?: string;
  onAttachToChat?: (path: string) => void;
}

export function officePreviewUrl(filePath: string, sessionId?: string): string {
  const q = new URLSearchParams({ path: filePath, format: 'html', ...(sessionId ? { session_id: sessionId } : {}) });
  return `${getApiBaseUrl()}/api/workspace/file/preview?${q}`;
}

function SheetsView({ p, fileName }: { p: Extract<Preview, { kind: 'sheets' }>; fileName: string }) {
  const { t } = useTranslation();
  const [i, setI] = useState(0);
  const sheet = p.sheets[i];
  return (
    <div className="flex flex-col h-full" data-testid="office-sheets">
      {p.sheets.length > 1 && (
        <div className="flex items-center gap-1 px-2 py-1 border-b border-[var(--color-border)] overflow-x-auto shrink-0" role="tablist">
          {p.sheets.map((s, k) => (
            <button key={s.name + k} type="button" role="tab" aria-selected={k === i} onClick={() => setI(k)}
              className={`px-2 py-0.5 rounded text-[12px] whitespace-nowrap ${k === i ? 'bg-[var(--color-hover)] text-[var(--color-text)] font-semibold' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
              data-testid="office-sheet-tab">{s.name}</button>
          ))}
        </div>
      )}
      {sheet.truncated && (
        <div className="px-3 py-1 text-[11px] text-[var(--color-text-muted)] shrink-0" data-testid="office-truncated">
          {sheet.totalCols > (sheet.rows[0]?.length ?? 0)
            ? t('ui.OfficeRenderer.truncatedRowsCols', { shown: sheet.rows.length, total: sheet.totalRows, shownCols: sheet.rows[0]?.length ?? 0, totalCols: sheet.totalCols })
            : t('ui.OfficeRenderer.truncatedRows', { shown: sheet.rows.length, total: sheet.totalRows })}
        </div>
      )}
      <div className="flex-1 min-h-0">
        {sheet.rows.length === 0
          ? <div className="p-4 text-[12px] text-[var(--color-text-muted)]">{t('ui.OfficeRenderer.emptySheet')}</div>
          : (
            <Suspense fallback={null}>
              <CsvRenderer key={i} filePath={fileName} fileName={`${sheet.name}.csv`} content={Papa.unparse(sheet.rows)}
                encoding="utf-8" mimeType="text/csv" fileSize={0} />
            </Suspense>
          )}
      </div>
    </div>
  );
}

function SlidesView({ p }: { p: Extract<Preview, { kind: 'slides' }> }) {
  const { t } = useTranslation();
  return (
    <div className="h-full overflow-auto p-4" data-testid="office-slides">
      <div className="max-w-3xl mx-auto flex flex-col gap-3">
        <div className="text-[11px] text-[var(--color-text-muted)]">{t('ui.OfficeRenderer.slidesOutline', { count: p.slides.length })}</div>
        {p.slides.map((s) => (
          <section key={s.index} className="rounded-lg border border-[var(--color-border)] p-3" data-testid="office-slide">
            <div className="text-[11px] text-[var(--color-text-muted)] mb-1">{t('ui.OfficeRenderer.slideNumber', { n: s.index })}{s.images ? ` · ${t('ui.OfficeRenderer.pictures', { count: s.images })}` : ''}</div>
            {s.title && <h3 className="text-[14px] font-semibold mb-1">{s.title}</h3>}
            {s.body.map((l, k) => <p key={k} className="text-[13px] whitespace-pre-wrap">{l}</p>)}
            {s.tables.map((tbl, k) => (
              <table key={k} className="mt-2 text-[12px] border-collapse">
                <tbody>{tbl.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci} className="border border-[var(--color-border)] px-2 py-0.5">{c}</td>)}</tr>)}</tbody>
              </table>
            ))}
            {s.notes && <div className="mt-2 text-[12px] text-[var(--color-text-muted)] italic">{t('ui.OfficeRenderer.notes', { notes: s.notes })}</div>}
          </section>
        ))}
      </div>
    </div>
  );
}

export default function OfficeRenderer(props: Props) {
  const { t } = useTranslation();
  const { filePath, fileName, sessionId } = props;
  const [state, setState] = useState<{ p: Preview } | { error: string } | null>(null);
  useEffect(() => {
    let live = true;
    setState(null);
    api.get<Preview>('/workspace/file/preview', { params: { path: filePath, ...(sessionId ? { session_id: sessionId } : {}) } })
      .then((r) => { if (live) setState({ p: r.data }); })
      .catch((e) => { if (live) setState({ error: (e as { detail?: string })?.detail || '' }); });
    return () => { live = false; };
  }, [filePath, sessionId]);

  if (!state) return <div className="p-4 text-[12px] text-[var(--color-text-muted)]">{t('ui.OfficeRenderer.loadingPreview')}</div>;
  if ('error' in state) {
    return (
      <div className="h-full flex flex-col">
        <div className="px-3 py-1.5 text-[11.5px] text-[var(--color-text-muted)]" data-testid="office-error">{t('ui.OfficeRenderer.previewUnavailable', { reason: state.error || t('ui.OfficeRenderer.couldNotPreview') })}</div>
        <div className="flex-1 min-h-0">
          <Suspense fallback={null}>
            <UnsupportedRenderer filePath={filePath} fileName={fileName} content={null} encoding="base64"
              mimeType="application/octet-stream" fileSize={props.fileSize} absolutePath={props.absolutePath}
              onAttachToChat={props.onAttachToChat} />
          </Suspense>
        </div>
      </div>
    );
  }
  const p = state.p;
  if (p.kind === 'sheets') return <SheetsView p={p} fileName={fileName} />;
  if (p.kind === 'slides') return <SlidesView p={p} />;
  return (
    <iframe
      title={fileName}
      src={officePreviewUrl(filePath, sessionId)}
      sandbox=""
      className="w-full h-full border-0 bg-white"
      data-testid="office-docx"
    />
  );
}
