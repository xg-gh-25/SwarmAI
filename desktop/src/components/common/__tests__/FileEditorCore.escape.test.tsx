/**
 * FileEditorCore — Escape (hooks/useLayer).
 *
 * Escape closes the search bar first, then the editor, and the editor's close goes
 * through the unsaved-changes question. In the modal the editor is a layer on the
 * shared stack: an overlay under it stays open, and the unsaved question above it
 * takes Escape as "continue editing". In the panel (Canvas) it is not a layer: it
 * answers only an Escape pressed inside it.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import type { ReactNode } from 'react';
import FileEditorCore from '../FileEditorCore';
import { useEscape } from '../../../hooks/useLayer';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: {} }), put: vi.fn(), post: vi.fn() },
}));

afterEach(cleanup);

const props = (variant: 'modal' | 'panel', onClose = vi.fn()) => ({
  workspaceId: '', onSave: vi.fn().mockResolvedValue(undefined), onClose, variant,
  filePath: 'notes/a.txt', fileName: 'a.txt', initialContent: 'alpha\nbeta\n',
});
const textarea = () => screen.getByTestId('file-editor-textarea');
const esc = (el: Element) => fireEvent.keyDown(el, { key: 'Escape' });
const openSearch = () => fireEvent.keyDown(textarea(), { key: 'f', metaKey: true });

describe('FileEditorCore in the modal: a layer', () => {
  it('Escape closes the search bar first, then the editor', () => {
    const onClose = vi.fn();
    render(<FileEditorCore {...props('modal', onClose)} />);
    openSearch();
    expect(screen.getByTestId('search-bar')).toBeTruthy();
    esc(textarea());
    expect(screen.queryByTestId('search-bar')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    esc(textarea());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape in the search box closes only the search box', () => {
    const onClose = vi.fn();
    render(<FileEditorCore {...props('modal', onClose)} />);
    openSearch();
    esc(screen.getByTestId('search-input'));
    expect(screen.queryByTestId('search-bar')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('with unsaved edits Escape asks first, and Escape on the question means continue editing', () => {
    const onClose = vi.fn();
    render(<FileEditorCore {...props('modal', onClose)} />);
    fireEvent.change(textarea(), { target: { value: 'alpha\nbeta\nedited\n' } });
    esc(textarea());
    const question = screen.getByRole('alertdialog', { name: 'Unsaved Changes' });
    expect(question.contains(document.activeElement)).toBe(true);   // focus moved to it
    esc(document.activeElement!);
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect((textarea() as HTMLTextAreaElement).value).toContain('edited');
  });

  it('opened over an overlay, Escape closes the editor and not the overlay', () => {
    const onClose = vi.fn();
    const overlay = vi.fn();
    function Overlay({ children }: { children: ReactNode }) {
      useEscape(true, overlay);
      return <>{children}</>;
    }
    const { rerender } = render(<Overlay>{null}</Overlay>);
    rerender(<Overlay><FileEditorCore {...props('modal', onClose)} /></Overlay>);
    esc(textarea());
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(overlay).not.toHaveBeenCalled();
  });
});

describe('FileEditorCore in the panel: not a layer', () => {
  it('Escape inside the editor closes it; Escape elsewhere on the page does not', () => {
    const onClose = vi.fn();
    render(<FileEditorCore {...props('panel', onClose)} />);
    esc(document.body);
    expect(onClose).not.toHaveBeenCalled();
    esc(textarea());
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
