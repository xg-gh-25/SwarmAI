/**
 * Cycle C (run_b454ce39): image + pdf renderers stream from
 * /api/workspace/file/raw?path=  instead of decoding a base64 `content` prop.
 *
 * WHY: base64-in-JSON is +33% over the wire and lives in the JS cache until the
 * tab closes; /raw is a streaming FileResponse (VideoRenderer/AudioRenderer
 * already use it). This locks that image/pdf now build a raw-URL src from
 * filePath — reverting to the base64 dataUri turns these RED.
 *
 * Mutation check: revert ImageRenderer to `src={dataUri}` → the first test's
 * "src is a /raw URL" assertion goes RED.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import ImageRenderer from '../ImageRenderer';

// react-pdf pulls in a worker + ESM that jsdom can't load; we only need to
// assert the `file` prop react-pdf receives, so mock Document/Page to echo it.
vi.mock('react-pdf', () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({ file }: { file: unknown }) => (
    <div data-testid="pdf-document" data-file={typeof file === 'string' ? file : JSON.stringify(file)} />
  ),
  Page: () => <div data-testid="pdf-page" />,
}));

describe('Cycle C — image/pdf stream via /raw URL', () => {
  it('ImageRenderer builds an <img src> pointing at /api/workspace/file/raw?path=', () => {
    const { container } = render(
      <ImageRenderer
        filePath="Knowledge/Signals/chart.png"
        fileName="chart.png"
        content={null}
        encoding="base64"
        mimeType="image/png"
        fileSize={1234}
      />,
    );
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    const src = img!.getAttribute('src') ?? '';
    expect(src).toContain('/api/workspace/file/raw?path=');
    expect(src).toContain(encodeURIComponent('Knowledge/Signals/chart.png'));
    // Must NOT be a base64 data URI anymore.
    expect(src.startsWith('data:')).toBe(false);
  });

  it('ImageRenderer resets zoom on file switch (Gate-2 HIGH: filePath dep, not content)', () => {
    // Zoom in on image1, then switch to image2 — the zoom INDICATOR (not the
    // "100%" reset button) must return to 100%. With the old [content]-only dep
    // it stayed at the prior zoom because content is '' for both (streaming path).
    // Target the indicator span (tabular-nums) specifically so we don't match the
    // literal "100%" text on the resetTo100 button (that made the first draft vacuous).
    const zoomText = (c: HTMLElement) =>
      (c.querySelector('.tabular-nums')?.textContent ?? '').trim();

    const { container, rerender, getByTitle } = render(
      <ImageRenderer filePath="a/one.png" fileName="one.png" content={null}
        encoding="base64" mimeType="image/png" fileSize={1} />,
    );
    const zoomIn = getByTitle('Zoom in');
    fireEvent.click(zoomIn); fireEvent.click(zoomIn);
    // Indicator now shows >100% (zoomed in).
    expect(zoomText(container)).not.toBe('100%');
    // Switch to a different image (same viewType → React reuses the instance).
    rerender(
      <ImageRenderer filePath="b/two.png" fileName="two.png" content={null}
        encoding="base64" mimeType="image/png" fileSize={1} />,
    );
    expect(container.querySelector('img')!.getAttribute('src'))
      .toContain(encodeURIComponent('b/two.png'));
    // …and the zoom indicator reset to 100% (fit), not the carried-over zoom.
    expect(zoomText(container)).toBe('100%');
  });

  it('PdfRenderer passes a /raw URL string as react-pdf `file`', async () => {
    const PdfRenderer = (await import('../PdfRenderer')).default;
    const { getByTestId } = render(
      <PdfRenderer
        filePath="Reports/deck.pdf"
        fileName="deck.pdf"
        content={null}
        encoding="base64"
        mimeType="application/pdf"
        fileSize={9999}
      />,
    );
    const fileAttr = getByTestId('pdf-document').getAttribute('data-file') ?? '';
    expect(fileAttr).toContain('/api/workspace/file/raw?path=');
    expect(fileAttr).toContain(encodeURIComponent('Reports/deck.pdf'));
  });
});
