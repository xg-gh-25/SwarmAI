/**
 * Tests for the terminal attachment chip (AttachmentPreviewCard type 'terminal' +
 * FileAttachmentPreview's full-text panel): the chip shows the folder and the line
 * count (and "cut" when trimmed); clicking it opens the full text, clicking again
 * closes; × removes it without opening it.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FileAttachmentPreview } from '../FileAttachmentPreview';
import type { UnifiedAttachment } from '../../../types';

const chip: UnifiedAttachment = {
  id: 'term1', name: 'Terminal · swarmai', type: 'terminal', deliveryStrategy: 'terminal_context',
  size: 20, mediaType: 'text/plain', textContent: 'npm test\nFAIL TerminalStore', preview: 'npm test\nFAIL TerminalStore',
  terminal: { cwd: '/r/swarmai', lines: 2, truncated: true }, isLoading: false,
};

describe('terminal attachment chip', () => {
  it('shows folder + lines, opens / closes the full text, × removes', () => {
    const onRemove = vi.fn();
    render(<FileAttachmentPreview attachments={[chip]} onRemove={onRemove} />);
    const card = screen.getByTestId('attachment-terminal-term1');
    expect(card).toHaveTextContent('swarmai');
    expect(card).toHaveTextContent('2 lines · cut');
    expect(screen.queryByTestId('attachment-terminal-full')).toBeNull();
    fireEvent.click(card);
    const full = screen.getByTestId('attachment-terminal-full');
    expect(full).toHaveTextContent('FAIL TerminalStore');
    expect(full).toHaveTextContent('/r/swarmai');
    expect(full).toHaveTextContent('older output cut');
    fireEvent.click(card);
    expect(screen.queryByTestId('attachment-terminal-full')).toBeNull();
    fireEvent.click(screen.getByLabelText('Remove terminal output'));
    expect(onRemove).toHaveBeenCalledWith('term1');
    expect(screen.queryByTestId('attachment-terminal-full')).toBeNull();
  });
});
