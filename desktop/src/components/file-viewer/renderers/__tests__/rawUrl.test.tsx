/**
 * Raw-URL contract for the streaming renderers (image/pdf/video/audio).
 *
 * Cycle C (run_b454ce39): these renderers stream from /api/workspace/file/raw?path=
 * instead of decoding a base64 `content` prop.
 *
 * BUGFIX (run_1dea02e1): the URL MUST be ABSOLUTE (getApiBaseUrl()-prefixed). In the
 * packaged Tauri app the webview origin is `tauri://localhost`; a BARE-RELATIVE
 * `/api/workspace/file/raw?...` resolves to the asset protocol and never reaches the
 * daemon → pdf.js throws "Invalid PDF structure", <img>/<video>/<audio> get a non-media
 * body. All four renderers now build the URL via the shared rawFileUrl() helper.
 *
 * Mutation check (mutation-proof against BOTH regressions):
 *   - revert any renderer to a BARE-RELATIVE `/api/...` (drop the getApiBaseUrl prefix)
 *     → the "starts with the mocked base" assertion goes RED.
 *   - revert ImageRenderer to `src={dataUri}` → the "not a data: URI" assertion goes RED.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import ImageRenderer from '../ImageRenderer';

// The origin resolver. Mocked to a concrete daemon origin so we can assert the
// renderers produce an ABSOLUTE URL (the whole point of the fix). If a renderer
// still used a bare-relative URL, it would NOT start with this base → RED.
const API_BASE = 'http://localhost:18321';
vi.mock('../../../../services/tauri', () => ({
  getApiBaseUrl: () => API_BASE,
}));

// react-pdf pulls in a worker + ESM that jsdom can't load; we only need to
// assert the `file` prop react-pdf receives, so mock Document/Page to echo it.
vi.mock('react-pdf', () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({ file }: { file: unknown }) => (
    <div data-testid="pdf-document" data-file={typeof file === 'string' ? file : JSON.stringify(file)} />
  ),
  Page: () => <div data-testid="pdf-page" />,
}));

const RAW = (path: string) =>
  `${API_BASE}/api/workspace/file/raw?path=${encodeURIComponent(path)}`;

beforeEach(() => {
  vi.clearAllMocks();
});

describe('streaming renderers build an ABSOLUTE /raw URL (getApiBaseUrl-prefixed)', () => {
  it('ImageRenderer <img src> is the absolute daemon URL, not bare-relative', () => {
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
    expect(src).toBe(RAW('Knowledge/Signals/chart.png'));
    // Absolute — reaches the daemon, not tauri://localhost. RED if bare-relative.
    expect(src.startsWith(`${API_BASE}/`)).toBe(true);
    // Must NOT be a base64 data URI.
    expect(src.startsWith('data:')).toBe(false);
  });

  it('PdfRenderer passes the absolute /raw URL string as react-pdf `file`', async () => {
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
    expect(fileAttr).toBe(RAW('Reports/deck.pdf'));
    expect(fileAttr.startsWith(`${API_BASE}/`)).toBe(true);
  });

  it('VideoRenderer <video src> is the absolute /raw URL', async () => {
    const VideoRenderer = (await import('../VideoRenderer')).default;
    const { container } = render(
      <VideoRenderer
        filePath="Media/clip.mp4"
        fileName="clip.mp4"
        content={null}
        encoding="base64"
        mimeType="video/mp4"
        fileSize={5000}
      />,
    );
    // src is on the <source> child element, not <video> directly.
    const source = container.querySelector('video source');
    expect(source).not.toBeNull();
    expect(source!.getAttribute('src')).toBe(RAW('Media/clip.mp4'));
  });

  it('AudioRenderer <audio src> is the absolute /raw URL', async () => {
    const AudioRenderer = (await import('../AudioRenderer')).default;
    const { container } = render(
      <AudioRenderer
        filePath="Media/track.mp3"
        fileName="track.mp3"
        content={null}
        encoding="base64"
        mimeType="audio/mpeg"
        fileSize={5000}
      />,
    );
    const source = container.querySelector('audio source');
    expect(source).not.toBeNull();
    expect(source!.getAttribute('src')).toBe(RAW('Media/track.mp3'));
  });

  it('ImageRenderer resets zoom on file switch (Gate-2 HIGH: filePath dep, not content)', () => {
    // Zoom in on image1, then switch to image2 — the zoom INDICATOR (not the
    // "100%" reset button) must return to 100%.
    const zoomText = (c: HTMLElement) =>
      (c.querySelector('.tabular-nums')?.textContent ?? '').trim();

    const { container, rerender, getByTitle } = render(
      <ImageRenderer filePath="a/one.png" fileName="one.png" content={null}
        encoding="base64" mimeType="image/png" fileSize={1} />,
    );
    const zoomIn = getByTitle('Zoom in');
    fireEvent.click(zoomIn); fireEvent.click(zoomIn);
    expect(zoomText(container)).not.toBe('100%');
    rerender(
      <ImageRenderer filePath="b/two.png" fileName="two.png" content={null}
        encoding="base64" mimeType="image/png" fileSize={1} />,
    );
    expect(container.querySelector('img')!.getAttribute('src'))
      .toBe(RAW('b/two.png'));
    expect(zoomText(container)).toBe('100%');
  });
});
