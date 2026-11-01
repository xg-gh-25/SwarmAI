/**
 * A file that is gone from its path (moved or deleted on disk) says so.
 *
 * Media and info-card types only fetch /workspace/file/meta. Before, a 404 there
 * was swallowed as "best effort": the image renderer then failed to stream, its
 * onError switched the tab to the info card, and the user read "Swarm doesn't
 * preview this file type" for a PNG that had simply been moved.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../../services/api', () => ({
  // The api-service interceptor throws an ApiError carrying `.statusCode`.
  default: { get: vi.fn(async () => { throw Object.assign(new Error('Resource not found'), { statusCode: 404 }); }) },
}));
import FileViewer from '../FileViewer';

describe('a file gone from its path', () => {
  it.each([
    ['an image', 'Knowledge/Reports/gone.png', 'gone.png'],
    ['an info-card type', 'Knowledge/Reports/gone.zip', 'gone.zip'],
  ])('%s shows "no longer available", not "type not supported"', async (_label, filePath, fileName) => {
    const { container } = render(<FileViewer variant="panel" onClose={vi.fn()} initialFile={{ filePath, fileName }} />);
    expect(await screen.findByText(/no longer available/)).toBeTruthy();
    expect(screen.queryByText(/doesn't preview this file type/)).toBeNull();
    expect(container.querySelector(`img[alt="${fileName}"]`)).toBeNull();
  });
});
