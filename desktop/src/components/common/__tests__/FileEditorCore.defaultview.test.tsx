/**
 * Regression tests for Canvas default-view behavior (run_7bfc0c09).
 *
 * WHAT THIS LOCKS:
 *  - .md / .svg files OPEN IN PREVIEW by default (not the raw edit textarea) —
 *    a user browsing a doc sees formatted content, not source.
 *  - Code files (.ts/.py/.json) STILL open in EDIT (textarea) — preview default
 *    is markdown/svg only.
 *  - autoDiff (initialShowDiff=true) STILL wins over the new preview default —
 *    a Radar "Changes" click opens on the diff, and after "Back to Edit" it lands
 *    on EDIT, not preview (Gate-1 Risk 4: preview must NOT be sticky under autoDiff).
 *  - A LARGE .md (> HIGHLIGHT_MAX_CHARS) does NOT default to preview (would
 *    full-parse ReactMarkdown and jank) — it defaults to edit; and a manual
 *    preview toggle on a huge file renders a plain <pre> + notice, not
 *    MarkdownRenderer.
 *
 * These drive the REAL component (no mock of the state under test). Reverting any
 * default flip turns the matching assertion RED (mutation-proven).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import FileEditorCore from '../FileEditorCore';
import { HIGHLIGHT_MAX_CHARS } from '../FileEditorCore';

interface Opts {
  fileName: string;
  filePath?: string;
  initialContent?: string;
  committedContent?: string;
  initialShowDiff?: boolean;
}

function renderCore(o: Opts) {
  return render(
    <FileEditorCore
      filePath={o.filePath ?? `src/${o.fileName}`}
      fileName={o.fileName}
      workspaceId="ws"
      initialContent={o.initialContent ?? '# Title\n\nsome **markdown** body'}
      committedContent={o.committedContent}
      initialShowDiff={o.initialShowDiff}
      variant="panel"
      onSave={vi.fn().mockResolvedValue(undefined)}
      onClose={vi.fn()}
    />,
  );
}

// The rendered markdown preview lives in a container with class "markdown-content"
// (MarkdownRenderer.tsx). The edit surface is the textarea data-testid="file-editor-textarea".
function hasMarkdownPreview(container: HTMLElement): boolean {
  return !!container.querySelector('.markdown-content');
}

describe('FileEditorCore default view — md/svg preview default', () => {
  it('AC1: a .md file opens in PREVIEW by default (markdown rendered, textarea absent)', () => {
    const { container } = renderCore({ fileName: 'notes.md' });
    expect(hasMarkdownPreview(container)).toBe(true);
    expect(screen.queryByTestId('file-editor-textarea')).not.toBeInTheDocument();
  });

  it('AC2: a .svg file opens in PREVIEW by default (svg-preview visible, textarea absent)', () => {
    const { container } = renderCore({
      fileName: 'icon.svg',
      initialContent: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>',
    });
    expect(screen.getByTestId('svg-preview')).toBeInTheDocument();
    expect(screen.queryByTestId('file-editor-textarea')).not.toBeInTheDocument();
    expect(container).toBeTruthy();
  });

  it('AC4: a code file (.ts) opens in EDIT by default (textarea present, no preview)', () => {
    const { container } = renderCore({ fileName: 'mod.ts', initialContent: 'const x = 1;\n' });
    expect(screen.getByTestId('file-editor-textarea')).toBeInTheDocument();
    expect(hasMarkdownPreview(container)).toBe(false);
  });

  it('AC4b: a code file (.json) opens in EDIT by default', () => {
    renderCore({ fileName: 'data.json', initialContent: '{"a":1}\n' });
    expect(screen.getByTestId('file-editor-textarea')).toBeInTheDocument();
  });
});

describe('FileEditorCore default view — autoDiff priority preserved (Gate-1 Risk 4)', () => {
  it('AC3: a .md opened with autoDiff=true shows DIFF, not preview', () => {
    const { container } = renderCore({
      fileName: 'notes.md',
      initialContent: 'line1\nline2-changed\nline3',
      committedContent: 'line1\nline2\nline3',
      initialShowDiff: true,
    });
    expect(screen.getByTestId('diff-view')).toBeInTheDocument();
    expect(hasMarkdownPreview(container)).toBe(false);
  });

  it('AC3b: after "Back to Edit" from an autoDiff .md, it lands on EDIT (not preview — no sticky preview)', () => {
    const { container } = renderCore({
      fileName: 'notes.md',
      initialContent: 'line1\nline2-changed\nline3',
      committedContent: 'line1\nline2\nline3',
      initialShowDiff: true,
    });
    // Toggle diff off via the show-changes toggle (title flips to "Back to edit").
    fireEvent.click(screen.getByTestId('show-changes-toggle'));
    // Must land on the edit textarea, NOT the markdown preview.
    expect(screen.getByTestId('file-editor-textarea')).toBeInTheDocument();
    expect(hasMarkdownPreview(container)).toBe(false);
  });
});

describe('FileEditorCore default view — toggle works both ways', () => {
  it('AC5: preview(default) → Edit → Preview toggles correctly for a .md', () => {
    const { container } = renderCore({ fileName: 'notes.md' });
    // Starts in preview.
    expect(hasMarkdownPreview(container)).toBe(true);
    // Click the preview toggle → edit source.
    fireEvent.click(screen.getByTestId('markdown-preview-toggle'));
    expect(screen.getByTestId('file-editor-textarea')).toBeInTheDocument();
    expect(hasMarkdownPreview(container)).toBe(false);
    // Click again → back to preview.
    fireEvent.click(screen.getByTestId('markdown-preview-toggle'));
    expect(hasMarkdownPreview(container)).toBe(true);
  });
});

describe('FileEditorCore default view — large-file guard (perf)', () => {
  const huge = '# Big\n' + 'x'.repeat(HIGHLIGHT_MAX_CHARS + 100);

  it('AC6a: a >threshold .md does NOT default to preview (defaults to edit, avoids full ReactMarkdown parse)', () => {
    const { container } = renderCore({ fileName: 'archive.md', initialContent: huge });
    expect(screen.getByTestId('file-editor-textarea')).toBeInTheDocument();
    expect(hasMarkdownPreview(container)).toBe(false);
  });

  it('AC6b: manually toggling preview ON for a huge .md renders a large-file notice, NOT MarkdownRenderer', () => {
    const { container } = renderCore({ fileName: 'archive.md', initialContent: huge });
    fireEvent.click(screen.getByTestId('markdown-preview-toggle'));
    // Guard: large-file preview fallback is shown, MarkdownRenderer is NOT mounted.
    expect(screen.getByTestId('large-md-preview-fallback')).toBeInTheDocument();
    expect(hasMarkdownPreview(container)).toBe(false);
  });

  it('AC6c: a >threshold .svg does NOT default to preview (Gate-2: symmetric with md)', () => {
    const hugeSvg = '<svg xmlns="http://www.w3.org/2000/svg">' + '<rect/>'.repeat(HIGHLIGHT_MAX_CHARS / 7 + 100) + '</svg>';
    renderCore({ fileName: 'huge.svg', initialContent: hugeSvg });
    expect(screen.getByTestId('file-editor-textarea')).toBeInTheDocument();
    expect(screen.queryByTestId('svg-preview')).not.toBeInTheDocument();
  });

  it('AC6d: manually toggling preview ON for a huge .svg renders the large-svg fallback, NOT the img', () => {
    const hugeSvg = '<svg xmlns="http://www.w3.org/2000/svg">' + '<rect/>'.repeat(HIGHLIGHT_MAX_CHARS / 7 + 100) + '</svg>';
    renderCore({ fileName: 'huge.svg', initialContent: hugeSvg });
    fireEvent.click(screen.getByTestId('svg-preview-toggle'));
    expect(screen.getByTestId('large-svg-preview-fallback')).toBeInTheDocument();
    expect(screen.queryByTestId('svg-preview')).not.toBeInTheDocument();
  });
});
