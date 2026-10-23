/**
 * OfficeRenderer + routing (canvas-file-types.md P1).
 *
 * - docx / xlsx / xlsm / pptx route to 'office'; doc / xls / ppt / odt stay on the card;
 * - sheets: one tab per sheet, the rows drawn by CsvRenderer, the "first N of M" note;
 * - slides: the outline (title, body, table, notes);
 * - docx: an iframe with sandbox="" (no scripts) on the preview's format=html URL;
 * - a failed preview shows the reason and the info card (Open / Attach / Copy path).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ApiError } from '../../../../services/api';
import { classifyFileForViewer } from '../../utils/fileViewTypes';

const get = vi.fn();
vi.mock('../../../../services/api', async (orig) => {
  const real = await orig<typeof import('../../../../services/api')>();
  return { ...real, default: { get: (...a: unknown[]) => get(...a) } };
});
vi.mock('../../../../services/tauri', () => ({ getApiBaseUrl: () => 'http://localhost:18321', isDesktop: () => true }));
import OfficeRenderer from '../OfficeRenderer';

const props = (fileName: string) => ({ filePath: `/ws/${fileName}`, fileName, fileSize: 10 });
beforeEach(() => { get.mockReset(); }); // braces: a returned fn would run as a cleanup hook
// CsvRenderer measures its scroll box; jsdom has no ResizeObserver
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

describe('routing', () => {
  it.each(['a.docx', 'a.xlsx', 'a.xlsm', 'a.pptx'])('%s → office', (n) => expect(classifyFileForViewer(n)).toBe('office'));
  it.each(['a.doc', 'a.xls', 'a.ppt', 'a.odt'])('%s → card', (n) => expect(classifyFileForViewer(n)).toBe('unsupported'));
});

describe('OfficeRenderer', () => {
  it('sheets: a tab per sheet, rows in the table, the truncation note', async () => {
    get.mockResolvedValue({ data: { kind: 'sheets', totalSheets: 2, sheets: [
      { name: 'Data', rows: [['name', 'qty'], ['apple', '3']], totalRows: 900, totalCols: 2, truncated: true },
      { name: 'Other', rows: [['x'], ['other-cell']], totalRows: 2, totalCols: 1, truncated: false },
    ] } });
    render(<OfficeRenderer {...props('a.xlsx')} />);
    expect(await screen.findByText('apple')).toBeTruthy();
    expect(screen.getByTestId('office-truncated').textContent).toContain('first 2 of 900 rows');
    fireEvent.click(screen.getAllByTestId('office-sheet-tab')[1]);
    expect(await screen.findByText('other-cell')).toBeTruthy();
  });

  it('slides: the outline', async () => {
    get.mockResolvedValue({ data: { kind: 'slides', slides: [
      { index: 1, title: 'Roadmap', body: ['Ship it'], tables: [[['Q', 'Goal']]], notes: 'say hi', images: 2 },
    ] } });
    render(<OfficeRenderer {...props('d.pptx')} />);
    const slide = await screen.findByTestId('office-slide');
    expect(slide.textContent).toMatch(/Slide 1 · 2 pictures.*Roadmap.*Ship it.*Q.*Goal.*Notes: say hi/);
  });

  it('docx: a script-less sandboxed iframe on the html URL', async () => {
    get.mockResolvedValue({ data: { kind: 'html' } });
    render(<OfficeRenderer {...props('p.docx')} sessionId="s1" />);
    const frame = await screen.findByTestId('office-docx');
    expect(frame.getAttribute('sandbox')).toBe('');
    expect(frame.getAttribute('src')).toBe('http://localhost:18321/api/workspace/file/preview?path=%2Fws%2Fp.docx&format=html&session_id=s1');
  });

  it('a failed preview shows the reason and the info card', async () => {
    const err = new ApiError({ code: 'X', message: 'x', detail: 'not a valid Office file', suggestedAction: '' }, 422);
    get.mockImplementation(async () => { throw err; });
    render(<OfficeRenderer {...props('bad.xlsx')} />);
    expect((await screen.findByTestId('office-error')).textContent).toContain('not a valid Office file');
    expect(await screen.findByText('Open in Default App')).toBeTruthy();
  });
});
