/**
 * Images the platform may draw (canvas-file-types.md P2).
 *
 * - heic / heif / tiff / tif route to the image renderer (macOS WebKit draws them);
 * - when the image cannot be decoded (onerror) the tab shows the info card with
 *   the image's own label — capability, not platform guessing;
 * - camera raw stays on the card; mkv/avi go to the card (WebKit cannot play them).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { classifyFileForViewer } from '../utils/fileViewTypes';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(async () => ({ data: { size: 10, mime_type: 'image/heic' } })) },
}));
import FileViewer from '../FileViewer';

describe('routing', () => {
  it.each(['a.heic', 'a.HEIF', 'a.tiff', 'a.tif'])('%s → image', (n) => expect(classifyFileForViewer(n)).toBe('image'));
  it.each(['a.cr2', 'a.nef', 'v.mkv', 'v.avi'])('%s → card', (n) => expect(classifyFileForViewer(n)).toBe('unsupported'));
});

describe('an image this system cannot decode', () => {
  it('falls back to the info card with its own label', async () => {
    const { container } = render(<FileViewer variant="panel" onClose={vi.fn()} initialFile={{ filePath: '/ws/p.heic', fileName: 'p.heic' }} />);
    const img = await waitFor(() => {
      const el = container.querySelector('img[alt="p.heic"]');
      expect(el).toBeTruthy();
      return el!;
    });
    fireEvent.error(img);
    // the card's own guidance (the status bar also says "HEIC Image")
    expect(await screen.findByText(/requires a native viewer/)).toBeTruthy();
    expect(screen.getAllByText('HEIC Image').length).toBeGreaterThan(0);
    expect(container.querySelector('img[alt="p.heic"]')).toBeNull();
  });
});
