/**
 * MessageBody: an Inbox message as paragraphs (a mail's blank line between every line is
 * paragraph spacing, not an empty line), folded when long so the reply box stays in view.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MessageBody } from './ReplyLane';

afterEach(() => { cleanup(); });

function sized(scroll: number, client: number) {
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => scroll });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => client });
}

describe('MessageBody', () => {
  it('turns blank lines into paragraphs and keeps line breaks inside one', () => {
    sized(100, 100);
    render(<MessageBody text={'Hi Each WS owner,\n\n\nPlease provide your ETA,\nnote blockers.\n\n  \nThanks'} testid="mb" />);
    const ps = screen.getByTestId('mb').querySelectorAll('p');
    expect([...ps].map((p) => p.textContent)).toEqual(['Hi Each WS owner,', 'Please provide your ETA,\nnote blockers.', 'Thanks']);
    expect(screen.queryByTestId('inbox-original-toggle')).toBeNull();      // short: nothing to fold
    cleanup();
    render(<MessageBody text={'one\n \ntwo'} testid="mb2" />);           // a blank line holding a space
    expect(screen.getByTestId('mb2').querySelectorAll('p')).toHaveLength(2);
  });

  it('folds a long message and opens it on 展开全文', () => {
    sized(900, 300);
    render(<MessageBody text={'a\n\nb\n\nc'} testid="mb" />);
    const toggle = screen.getByTestId('inbox-original-toggle');
    expect(toggle).toHaveTextContent('Show all');
    fireEvent.click(toggle);
    expect(screen.getByTestId('inbox-original-toggle')).toHaveTextContent('Show less');
    expect(screen.getByTestId('mb').firstElementChild?.getAttribute('data-open')).toBe('true');
  });
});
