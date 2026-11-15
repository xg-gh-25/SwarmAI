/**
 * The chat's "conversation too large" card (backend core.transcript_media): sending the
 * same oversized history again fails the same way, so the card offers no Continue,
 * while an ordinary error keeps it.
 */
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChatErrorMessage } from '../ChatErrorMessage';

vi.mock('../../../contexts/HealthContext', () => ({ useHealth: () => ({ triggerHealthCheck: vi.fn() }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

describe('ChatErrorMessage — conversation too large', () => {
  it('CONTEXT_TOO_LARGE shows its text and no Continue', () => {
    render(<ChatErrorMessage onContinue={vi.fn()} error={{
      code: 'CONTEXT_TOO_LARGE',
      message: "This conversation is too large for the model's request limit.",
      suggestedAction: 'Compact the conversation, or start a new chat tab and continue there.',
    }} />);
    expect(screen.queryByTestId('continue-button')).toBeNull();
    expect(screen.getByText(/too large for the model/)).toBeInTheDocument();
  });

  it('an ordinary error keeps Continue', () => {
    render(<ChatErrorMessage onContinue={vi.fn()} error={{ code: 'SDK_ERROR', message: 'boom' }} />);
    expect(screen.getByTestId('continue-button')).toBeInTheDocument();
  });
});
