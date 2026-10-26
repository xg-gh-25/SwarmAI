import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SWARM_OUT_CHAT, clampWidth, dragCeiling, chatColumnStyle, useSwarmOutChatWidth } from './SwarmOutChatResize';

function Harness() {
  const { width, seam } = useSwarmOutChatWidth();
  return (
    <div>
      <div data-testid="dash" />
      <div data-testid="col" style={chatColumnStyle(width)} data-width={width}>{seam}</div>
    </div>
  );
}

describe('Swarm Out chat width', () => {
  beforeEach(() => localStorage.clear());

  it('defaults to 560px, wider than the old fixed 460px', () => {
    render(<Harness />);
    expect(screen.getByTestId('col').dataset.width).toBe('560');
    expect(SWARM_OUT_CHAT.DEFAULT_WIDTH).toBeGreaterThan(460);
  });

  it('ArrowLeft widens the column and the width persists', () => {
    render(<Harness />);
    fireEvent.keyDown(screen.getByTestId('swarm-out-chat-resize'), { key: 'ArrowLeft' });
    expect(screen.getByTestId('col').dataset.width).toBe(String(560 + SWARM_OUT_CHAT.KEY_STEP));
    expect(localStorage.getItem(SWARM_OUT_CHAT.STORAGE_KEY)).toBe(String(560 + SWARM_OUT_CHAT.KEY_STEP));
  });

  it('reads a stored width, never below the minimum', () => {
    localStorage.setItem(SWARM_OUT_CHAT.STORAGE_KEY, '100');
    render(<Harness />);
    expect(screen.getByTestId('col').dataset.width).toBe(String(SWARM_OUT_CHAT.MIN_WIDTH));
  });

  it('double-click resets to the default', () => {
    localStorage.setItem(SWARM_OUT_CHAT.STORAGE_KEY, '800');
    render(<Harness />);
    fireEvent.doubleClick(screen.getByTestId('swarm-out-chat-resize'));
    expect(screen.getByTestId('col').dataset.width).toBe('560');
  });

  it('the drag ceiling leaves the dashboard its minimum', () => {
    const dash = document.createElement('div');
    const col = document.createElement('div');
    const seam = document.createElement('div');
    col.appendChild(seam);
    const row = document.createElement('div');
    row.append(dash, col);
    dash.getBoundingClientRect = () => ({ width: 900 } as DOMRect);
    col.getBoundingClientRect = () => ({ width: 560 } as DOMRect);
    expect(dragCeiling(seam, 1)).toBe(900 + 560 - SWARM_OUT_CHAT.DASHBOARD_MIN);
    expect(clampWidth(5000, dragCeiling(seam, 1))).toBe(900 + 560 - SWARM_OUT_CHAT.DASHBOARD_MIN);
    expect(clampWidth(10, dragCeiling(seam, 1))).toBe(SWARM_OUT_CHAT.MIN_WIDTH);
  });

  it('the column CSS keeps the dashboard visible when the window shrinks', () => {
    expect(chatColumnStyle(2000).maxWidth).toBe(`calc(100% - ${SWARM_OUT_CHAT.DASHBOARD_MIN}px)`);
  });
});
