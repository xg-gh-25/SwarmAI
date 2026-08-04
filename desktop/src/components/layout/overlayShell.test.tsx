/**
 * overlayShell tests — lock the SHARED workbench-frame primitives (M4, run_fdeaead8).
 * These guard the contract the 4 migrated surfaces rely on: fmtTs formatting, the
 * toolbar's slot/loading layout, and the drawer's positioning (absolute right, z-order,
 * width/cap, click-stop). Mutation-verified: each assertion goes RED if the primitive
 * regresses.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { fmtTs, WorkbenchToolbar, OverlayDrawer } from './overlayShell';

describe('overlayShell — fmtTs', () => {
  it('formats a valid ISO as absolute YYYY-MM-DD HH:MM (zero-padded)', () => {
    // Build from local components so the assertion is timezone-independent.
    const d = new Date(2026, 0, 5, 9, 7); // 2026-01-05 09:07 local
    expect(fmtTs(d.toISOString())).toBe('2026-01-05 09:07');
  });
  it('returns — for null / undefined / invalid', () => {
    expect(fmtTs(null)).toBe('—');
    expect(fmtTs(undefined)).toBe('—');
    expect(fmtTs('not-a-date')).toBe('—');
  });
});

describe('overlayShell — WorkbenchToolbar', () => {
  it('renders left + right slots and shows Loading… only when loading', () => {
    const { rerender } = render(
      <WorkbenchToolbar testid="tb" left={<span>L</span>} right={<span>R</span>} />,
    );
    expect(screen.getByText('L')).toBeInTheDocument();
    expect(screen.getByText('R')).toBeInTheDocument();
    expect(screen.queryByText('Loading…')).toBeNull();

    rerender(<WorkbenchToolbar testid="tb" left={<span>L</span>} loading />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('is a bottom-bordered flex bar (the shared sub-header contract)', () => {
    render(<WorkbenchToolbar testid="tb" left={<span>x</span>} />);
    const bar = screen.getByTestId('tb');
    expect(bar.className).toContain('flex');
    expect(bar.className).toContain('border-b');
  });
});

describe('overlayShell — OverlayDrawer', () => {
  it('is an absolute right-anchored drawer with the given width, cap and z', () => {
    render(<OverlayDrawer testid="dr" widthPx={420} maxWidthPct={75} z={20}><div>body</div></OverlayDrawer>);
    const dr = screen.getByTestId('dr');
    expect(dr.className).toContain('absolute');
    expect(dr.className).toContain('inset-y-0');
    expect(dr.className).toContain('right-0');
    expect(dr.className).toContain('border-l');
    expect(dr.style.width).toBe('420px');
    expect(dr.style.maxWidth).toBe('75%');
    expect(dr.style.zIndex).toBe('20');
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('defaults to z-10 and a 90% cap', () => {
    render(<OverlayDrawer testid="dr" widthPx={360}><div /></OverlayDrawer>);
    const dr = screen.getByTestId('dr');
    expect(dr.style.zIndex).toBe('10');
    expect(dr.style.maxWidth).toBe('90%');
  });

  it('stops click propagation by default (drawer over a click-to-dismiss board)', () => {
    const outer = vi.fn();
    render(
      <div onClick={outer}>
        <OverlayDrawer testid="dr" widthPx={360}><button>hit</button></OverlayDrawer>
      </div>,
    );
    fireEvent.click(screen.getByText('hit'));
    expect(outer).not.toHaveBeenCalled();
  });
});
