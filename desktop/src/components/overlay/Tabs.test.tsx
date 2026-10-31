/**
 * The shared overlay tab bar: the selected tab is marked for assistive tech, arrow
 * keys / Home / End move the choice, and only the selected tab is in the Tab order.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { Tabs } from './index';

afterEach(cleanup);

function Bar() {
  const [v, setV] = useState<'a' | 'b' | 'c'>('a');
  return <Tabs value={v} onChange={setV} label="Views" controls="panel"
    items={[{ key: 'a', label: 'A', testid: 't-a', icon: 'home', hint: 'first' },
            { key: 'b', label: 'B', testid: 't-b', count: 3, tone: 'alert' },
            { key: 'c', label: 'C', testid: 't-c', id: 'tab-c' }]} />;
}

describe('Tabs', () => {
  it('marks the selected tab and keeps only it in the Tab order', () => {
    render(<Bar />);
    expect(screen.getByRole('tablist').getAttribute('aria-label')).toBe('Views');
    expect(screen.getByTestId('t-a').getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('t-a').tabIndex).toBe(0);
    expect(screen.getByTestId('t-b').tabIndex).toBe(-1);
    expect(screen.getByTestId('t-b').getAttribute('aria-controls')).toBe('panel');
    expect(screen.getByTestId('t-c').id).toBe('tab-c');
    expect(screen.getByTestId('t-b-count').textContent).toBe('3');
    expect(screen.getByTestId('t-a').textContent).toContain('first');
  });

  it('arrow keys, Home and End move the choice and the focus', () => {
    render(<Bar />);
    fireEvent.keyDown(screen.getByTestId('t-a'), { key: 'ArrowRight' });
    expect(screen.getByTestId('t-b').getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(screen.getByTestId('t-b'));
    fireEvent.keyDown(screen.getByTestId('t-b'), { key: 'End' });
    expect(screen.getByTestId('t-c').getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(screen.getByTestId('t-c'), { key: 'ArrowRight' });
    expect(screen.getByTestId('t-a').getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(screen.getByTestId('t-a'), { key: 'ArrowLeft' });
    expect(screen.getByTestId('t-c').getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(screen.getByTestId('t-c'), { key: 'Home' });
    expect(screen.getByTestId('t-a').getAttribute('aria-selected')).toBe('true');
  });

  it('a click selects', () => {
    const onChange = vi.fn();
    render(<Tabs value="a" onChange={onChange} items={[{ key: 'a', label: 'A' }, { key: 'b', label: 'B', testid: 'x' }]} />);
    fireEvent.click(screen.getByTestId('x'));
    expect(onChange).toHaveBeenCalledWith('b');
  });
});
