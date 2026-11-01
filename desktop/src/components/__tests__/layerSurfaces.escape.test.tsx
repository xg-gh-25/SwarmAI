/**
 * More surfaces that close on Escape, each a layer on the shared stack
 * (hooks/useLayer): opened from a layer below (an overlay), Escape closes the surface
 * and not the layer under it; dialogs take focus and give it back.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useRef, type ReactNode } from 'react';
import { DeployHiveDialog } from '../settings/hiveComponents';
import { FolderPickerModal } from '../workspace/FolderPickerModal';
import FileContextMenu from '../workspace-explorer/FileContextMenu';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { useEscape } from '../../hooks/useLayer';

// react-i18next is not mocked: the app's i18n (test-setup) renders the real English text.
vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: {} }), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('../../services/workspace', () => ({
  workspaceService: { browseFilesystem: vi.fn().mockResolvedValue({ files: [], currentPath: '/' }) },
}));

afterEach(cleanup);

function Below({ onEscape, children }: { onEscape: () => void; children?: ReactNode }) {
  useEscape(true, onEscape);
  return <>{children}</>;
}
function openOver(below: () => void, ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrap = (c: ReactNode) => <QueryClientProvider client={qc}><Below onEscape={below}>{c}</Below></QueryClientProvider>;
  const r = render(wrap(null));
  r.rerender(wrap(ui));
  return (next: ReactNode) => r.rerender(wrap(next));
}

describe('DeployHiveDialog', () => {
  it('focus moves to the first field; Escape closes the dialog and not the Hive overlay', () => {
    const onClose = vi.fn();
    const overlay = vi.fn();
    openOver(overlay, <DeployHiveDialog accounts={[]} onClose={onClose} onDeployed={vi.fn()} />);
    const name = screen.getByPlaceholderText('e.g. my-hive, dev-01');
    expect(document.activeElement).toBe(name);
    fireEvent.keyDown(name, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(overlay).not.toHaveBeenCalled();
  });
});

describe('FolderPickerModal', () => {
  it('takes focus, Escape closes it (only it), and focus goes back to what opened it', () => {
    const onClose = vi.fn();
    const overlay = vi.fn();
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const rerender = openOver(overlay, <FolderPickerModal isOpen onClose={onClose} onSelect={vi.fn()} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(overlay).not.toHaveBeenCalled();
    rerender(<FolderPickerModal isOpen={false} onClose={onClose} onSelect={vi.fn()} />);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});

describe('FileContextMenu', () => {
  it('Escape closes the menu (not the Workspace overlay) and puts focus back on the row', () => {
    const onClose = vi.fn();
    const overlay = vi.fn();
    function Row() {
      const row = useRef<HTMLButtonElement>(null);
      const item = { id: 'a', name: 'a.md', path: 'a.md', type: 'file' } as unknown as FileTreeItem;
      return <>
        <button ref={row} data-testid="row">a.md</button>
        <FileContextMenu item={item} x={10} y={10} onClose={onClose} returnFocusRef={row} />
      </>;
    }
    openOver(overlay, <Row />);
    const first = screen.getAllByRole('menuitem')[0];
    first.focus();
    fireEvent.keyDown(first, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(overlay).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByTestId('row'));
  });
});
