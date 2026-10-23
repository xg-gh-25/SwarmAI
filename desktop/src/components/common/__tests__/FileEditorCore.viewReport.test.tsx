/**
 * FileEditorCore reports the SHOWN view (canvas-proprioception.md §3.2).
 *
 * - a markdown file opens in Preview and reports `preview`;
 * - switching to Edit / Changes reports each, once;
 * - Review turned on from Preview reports `edit` + review (the editor switches);
 * - a preview too large to draw reports `edit` (the user sees raw source);
 * - typing reports nothing (the callback fires on a view change only).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import FileEditorCore from '../FileEditorCore';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: {} }), put: vi.fn(), post: vi.fn() },
}));

const base = { workspaceId: '', onSave: vi.fn().mockResolvedValue(undefined), onClose: vi.fn(), onAttachToChat: vi.fn() };
const md = { ...base, filePath: 'docs/a.md', fileName: 'a.md', initialContent: '# A\n\nbody\n' };
beforeEach(() => cleanup());

const pick = (label: string) => fireEvent.click(screen.getAllByRole('radio').find((r) => r.textContent === label)!);

describe('FileEditorCore onViewChange', () => {
  it('reports preview, then each switch once, and nothing while typing', () => {
    const onViewChange = vi.fn();
    render(<FileEditorCore {...md} variant="panel" committedContent="# old\n" onViewChange={onViewChange} />);
    expect(onViewChange).toHaveBeenLastCalledWith({ path: 'docs/a.md', view: 'preview', review: false });
    pick('Edit');
    expect(onViewChange).toHaveBeenLastCalledWith({ path: 'docs/a.md', view: 'edit', review: false });
    const calls = onViewChange.mock.calls.length;
    fireEvent.change(screen.getByTestId('file-editor-textarea'), { target: { value: '# B\n' } });
    fireEvent.change(screen.getByTestId('file-editor-textarea'), { target: { value: '# BC\n' } });
    expect(onViewChange.mock.calls.length).toBe(calls);
    pick('Changes');
    expect(onViewChange).toHaveBeenLastCalledWith({ path: 'docs/a.md', view: 'changes', review: false });
  });

  it('Review from Preview reports edit + review', () => {
    const onViewChange = vi.fn();
    render(<FileEditorCore {...md} variant="panel" onViewChange={onViewChange} />);
    fireEvent.click(screen.getByTestId('review-mode-toggle'));
    expect(onViewChange).toHaveBeenLastCalledWith({ path: 'docs/a.md', view: 'edit', review: true });
  });

  it('a preview too large to draw reports edit', () => {
    const onViewChange = vi.fn();
    const big = '# big\n' + 'line of markdown text\n'.repeat(60_000);
    render(<FileEditorCore {...md} initialContent={big} variant="panel" onViewChange={onViewChange} />);
    pick('Preview');
    expect(screen.getByTestId('large-preview-fallback')).toBeTruthy();
    expect(onViewChange).toHaveBeenLastCalledWith({ path: 'docs/a.md', view: 'edit', review: false });
  });
});
