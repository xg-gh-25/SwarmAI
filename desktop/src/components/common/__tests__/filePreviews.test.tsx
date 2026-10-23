/**
 * filePreviews — the one preview table (canvas-file-types.md P3).
 *
 * - previewKindOf: md / markdown / mdx → markdown, svg, ipynb → notebook,
 *   mmd / mermaid → mermaid, everything else → none (case-insensitive, path-safe).
 * - FileEditorCore: a notebook / mermaid file opens on its Preview with the same
 *   Preview / Edit / Changes switch as markdown; Edit shows the raw source.
 * - NotebookView: markdown cells rendered, code cells with "In [n]:" and outputs
 *   (stream, text/plain, png image, error traceback without ANSI codes); text/html
 *   is NOT rendered (a note + its text/plain fallback); invalid JSON → a notice.
 * - mermaid: the source is drawn through MarkdownRenderer's mermaid block.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { previewKindOf, NotebookView } from '../filePreviews';

vi.mock('../../../services/api', () => ({ default: { get: vi.fn().mockResolvedValue({ data: {} }), put: vi.fn(), post: vi.fn() } }));
vi.mock('mermaid', () => ({ default: { initialize: vi.fn(), render: vi.fn(async () => ({ svg: '<svg data-testid="mermaid-svg"></svg>' })) } }));
import FileEditorCore from '../FileEditorCore';
import { ThemeProvider } from '../../../contexts/ThemeContext';

const NB = JSON.stringify({
  metadata: { language_info: { name: 'python' } },
  cells: [
    { cell_type: 'markdown', source: ['# Analysis\n', 'some *text*'] },
    { cell_type: 'code', execution_count: 3, source: 'print(1)', outputs: [
      { output_type: 'stream', name: 'stdout', text: ['1\n'] },
      { output_type: 'display_data', data: { 'image/png': 'iVBORw0KGgo=' } },
      { output_type: 'execute_result', data: { 'text/html': '<script>alert(1)</script><b>t</b>', 'text/plain': '<DataFrame 2x2>' } },
      { output_type: 'error', ename: 'ValueError', evalue: 'bad', traceback: ['\u001b[31mValueError\u001b[0m: bad'] },
    ] },
  ],
});

describe('previewKindOf', () => {
  it.each([
    ['a.md', 'markdown'], ['A.MARKDOWN', 'markdown'], ['x.mdx', 'markdown'], ['i.svg', 'svg'],
    ['n.ipynb', 'notebook'], ['d.mmd', 'mermaid'], ['d.mermaid', 'mermaid'],
    ['a.ts', null], ['README', null], ['dir.md/file', null],
  ])('%s → %s', (n, k) => expect(previewKindOf(n)).toBe(k));
});

describe('NotebookView', () => {
  it('renders cells and safe outputs; does not render HTML', () => {
    const { container } = render(<NotebookView content={NB} />);
    expect(screen.getAllByTestId('nb-md')[0].textContent).toContain('Analysis');
    expect(screen.getByTestId('nb-code').textContent).toContain('In [3]:');
    expect(container.textContent).toContain('1\n');
    expect(screen.getByTestId('nb-image').getAttribute('src')).toBe('data:image/png;base64,iVBORw0KGgo=');
    expect(screen.getByTestId('nb-html-skipped')).toBeTruthy();
    expect(container.textContent).toContain('<DataFrame 2x2>');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(screen.getByTestId('nb-error').textContent).toBe('ValueError: bad');
  });

  it('invalid JSON shows a notice', () => {
    render(<NotebookView content="{not json" />);
    expect(screen.getByTestId('nb-invalid').textContent).toContain('not valid JSON');
  });
});

describe('FileEditorCore uses the table', () => {
  const base = { workspaceId: '', onSave: vi.fn().mockResolvedValue(undefined), onClose: vi.fn(), variant: 'panel' as const };

  it('a notebook opens on its preview; Edit shows the raw JSON', () => {
    render(<FileEditorCore {...base} filePath="n.ipynb" fileName="n.ipynb" initialContent={NB} />);
    expect(screen.getByTestId('notebook-view')).toBeTruthy();
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Preview', 'Edit', 'Changes']);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    expect((screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement).value).toBe(NB);
  });

  it('a mermaid file opens on its diagram', async () => {
    render(<ThemeProvider><FileEditorCore {...base} filePath="d.mmd" fileName="d.mmd" initialContent={'graph TD\n  A-->B\n'} /></ThemeProvider>);
    expect(screen.getByTestId('mermaid-preview-box')).toBeTruthy();
    expect(await screen.findByTestId('mermaid-svg', {}, { timeout: 3000 })).toBeTruthy();
  });
});
