/**
 * Shared overlay parts (components/overlay): each renders its content, reports the
 * selected state accessibly and calls back with the chosen key; StatusBadge maps a
 * tone to its theme colour. Methodology: render with Testing Library, assert roles,
 * aria state and the theme variable each uses.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Stat, SectionLabel, Tabs, Segmented, StatusBadge, Hint } from './index';

afterEach(cleanup);

describe('overlay parts', () => {
  it('Stat shows label, value and hint', () => {
    render(<Stat label="Open" value={3} hint="last 30 days" testid="s" />);
    expect(screen.getByTestId('s').textContent).toBe('Open3last 30 days');
  });

  it('SectionLabel shows its text and the right slot', () => {
    render(<SectionLabel right="2">Health</SectionLabel>);
    expect(screen.getByText('Health')).toBeTruthy();
    expect(screen.getByText('2')).toBeTruthy();
  });

  it('Tabs mark the selected tab and report a click', () => {
    const on = vi.fn();
    render(<Tabs items={[{ key: 'a', label: 'A', count: 4 }, { key: 'b', label: 'B' }]} value="a" onChange={on} />);
    const [a, b] = screen.getAllByRole('tab');
    expect(a.getAttribute('aria-selected')).toBe('true');
    expect(b.getAttribute('aria-selected')).toBe('false');
    expect(a.textContent).toBe('A4');
    fireEvent.click(b);
    expect(on).toHaveBeenCalledWith('b');
  });

  it('Segmented marks the selected option and tints it with the primary colour', () => {
    const on = vi.fn();
    render(<Segmented label="Range" options={[{ key: '7d', label: '7d' }, { key: '30d', label: '30d' }]} value="30d" onChange={on} />);
    const radios = screen.getAllByRole('radio');
    expect(radios[1].getAttribute('aria-checked')).toBe('true');
    expect(radios[1].getAttribute('style')).toContain('--color-primary');
    expect(radios[0].getAttribute('style')).toBeNull();
    fireEvent.click(radios[0]);
    expect(on).toHaveBeenCalledWith('7d');
  });

  it('StatusBadge colours by tone from the theme', () => {
    render(<StatusBadge tone="error" testid="b">stopped</StatusBadge>);
    const b = screen.getByTestId('b');
    expect(b.getAttribute('data-tone')).toBe('error');
    expect(b.getAttribute('style')).toContain('var(--color-status-error)');
    expect(b.textContent).toBe('stopped');
  });

  it('Hint is plain text, no box', () => {
    render(<Hint testid="h">Ask Swarm in chat.</Hint>);
    expect(screen.getByTestId('h').className).not.toMatch(/border|bg-/);
  });
});
