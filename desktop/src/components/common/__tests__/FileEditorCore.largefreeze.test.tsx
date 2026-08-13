/**
 * Regression tests for the large-file CANVAS FREEZE (run_f71e5920, fix C).
 *
 * ROOT CAUSE (Gate-1 verified): in the EDIT view the visible text is painted by a
 * highlight <pre> overlay (the textarea itself is text-transparent). For a large
 * file the highlight effect dumped the ENTIRE content string into that <pre> as one
 * laid-out text node — WKWebView reflowing a 17MB text node froze the whole app.
 * The old HIGHLIGHT_MAX_CHARS guard only skipped hljs *markup* (computation) — it
 * STILL wrote the full raw content into the <pre> (the actual freeze).
 *
 * THE FIX (keeps the editable textarea — Gate-1 C1: textarea is the sole scroll
 * driver + browsers virtualize its value internally):
 *  - large file → highlight <pre> is NOT populated with full content (empty);
 *  - large file → textarea text is made VISIBLE (not transparent) so the user still
 *    sees content now that the <pre> no longer paints it;
 *  - the diff-overlay (maps content.split('\n')) is gated on !syncDisabled so a large
 *    file never builds a full-content overlay node.
 *
 * jsdom cannot measure reflow, so these assert the STRUCTURAL decision (which node
 * holds the content / which is visible). Reverting the guard turns them RED.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import FileEditorCore, { HIGHLIGHT_MAX_CHARS } from '../FileEditorCore';

function renderCore(fileName: string, initialContent: string) {
  return render(
    <FileEditorCore
      filePath={`src/${fileName}`}
      fileName={fileName}
      workspaceId="ws"
      initialContent={initialContent}
      variant="panel"
      onSave={vi.fn().mockResolvedValue(undefined)}
      onClose={vi.fn()}
    />,
  );
}

const HUGE = 'const a = 1;\n'.repeat(Math.ceil((HIGHLIGHT_MAX_CHARS + 5000) / 12));
const SMALL = 'const a = 1;\nconst b = 2;\n';

describe('FileEditorCore large-file freeze fix (C)', () => {
  it('AC6: a huge code file still renders an EDITABLE textarea (not readonly, not dropped)', () => {
    renderCore('archive.ts', HUGE);
    const ta = screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement;
    expect(ta).toBeInTheDocument();
    expect(ta.readOnly).toBe(false); // not force-readonly (Gate-1 C5: editing preserved)
  });

  it('AC7a: the highlight <pre> does NOT hold full content for a huge file (the freeze node stays empty)', () => {
    renderCore('archive.ts', HUGE);
    const layer = screen.getByTestId('editor-highlight-layer');
    // The freeze was a full-content text node here. After the fix it must be empty
    // (the textarea paints its own text on large files).
    expect(layer.textContent ?? '').not.toContain('const a = 1;');
    expect((layer.textContent ?? '').length).toBeLessThan(100);
  });

  it('AC7b: the textarea text is VISIBLE for a huge file (not text-transparent — user still sees content)', () => {
    renderCore('archive.ts', HUGE);
    const ta = screen.getByTestId('file-editor-textarea');
    // On large files the <pre> is empty, so the textarea must show its own text.
    expect(ta.className).not.toContain('text-transparent');
  });

  it('AC9: a SMALL file is unchanged — textarea stays transparent (highlight <pre> paints the text)', () => {
    renderCore('mod.ts', SMALL);
    const ta = screen.getByTestId('file-editor-textarea');
    expect(ta.className).toContain('text-transparent');
  });
});
