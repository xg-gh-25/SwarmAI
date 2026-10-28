/**
 * The Hive dialogs and the review comment box are layers (hooks/useLayer).
 *
 * A Hive dialog sits inside the Hive overlay: Escape closes the dialog, not the
 * overlay, and does nothing while it is saving. The comment box sits over the editor:
 * Escape cancels the comment wherever focus is inside it, and the editor stays open.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import type { ReactNode } from 'react';
import { AddAccountDialog } from '../hiveComponents';
import CommentPopover from '../../common/CommentPopover';
import { useEscape } from '../../../hooks/useLayer';

vi.mock('../../../services/hive', async (orig) => ({
  ...(await orig<typeof import('../../../services/hive')>()),
  hiveService: { createAccount: vi.fn(() => new Promise(() => {})) },
}));

afterEach(cleanup);

function Below({ onEscape, children }: { onEscape: () => void; children?: ReactNode }) {
  useEscape(true, onEscape);
  return <>{children}</>;
}
/** The layer below is open first, the dialog opens from it later, as in the app. */
function openOver(below: () => void, ui: ReactNode) {
  const r = render(<Below onEscape={below}>{null}</Below>);
  r.rerender(<Below onEscape={below}>{ui}</Below>);
}

describe('AddAccountDialog', () => {
  it('focus moves to the first field; Escape closes the dialog and not the Hive overlay', () => {
    const onClose = vi.fn();
    const overlay = vi.fn();
    openOver(overlay, <AddAccountDialog onClose={onClose} onSaved={vi.fn()} />);
    const first = screen.getByPlaceholderText('Enter 12-digit account ID');
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(overlay).not.toHaveBeenCalled();
  });

  it('while saving, Escape does nothing (and still does not reach the overlay)', () => {
    const onClose = vi.fn();
    const overlay = vi.fn();
    openOver(overlay, <AddAccountDialog onClose={onClose} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('Enter 12-digit account ID'), { target: { value: '123456789012' } });
    fireEvent.click(screen.getByText('Add Account'));
    expect(screen.getByText('Saving...')).toBeTruthy();
    fireEvent.keyDown(screen.getByPlaceholderText('Enter 12-digit account ID'), { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(overlay).not.toHaveBeenCalled();
  });
});

describe('CommentPopover', () => {
  it('Escape on one of its buttons cancels the comment and leaves the editor open', () => {
    const onCancel = vi.fn();
    const editor = vi.fn();
    openOver(editor, <CommentPopover lineNumber={3} topOffset={0} onSubmit={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByTestId('comment-submit'), { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(editor).not.toHaveBeenCalled();
  });
});
