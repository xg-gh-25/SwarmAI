/**
 * VideoRenderer -- Native HTML5 video player for browser-compatible formats
 * (MP4, WebM, MOV, M4V, OGV).
 *
 * Features:
 *   - Streams from /api/workspace/file/raw endpoint (no base64 in memory)
 *   - Native browser controls (play, pause, seek, volume, fullscreen)
 *   - Centered in the panel
 *   - Reports duration and dimensions via onStatusInfo on loadedmetadata
 */
import { useCallback, useRef } from 'react';

interface RendererProps {
  filePath: string;
  fileName: string;
  content: string | null;
  encoding: 'utf-8' | 'base64';
  mimeType: string;
  fileSize: number;
  onStatusInfo?: (info: { dimensions?: string; pageInfo?: string; rowColCount?: string; customInfo?: string }) => void;
}

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function VideoRenderer({
  filePath,
  fileName,
  onStatusInfo,
}: RendererProps) {
  const videoRef = useRef<HTMLVideoElement>(null);

  const handleLoadedMetadata = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    const duration = formatDuration(video.duration);
    const w = video.videoWidth;
    const h = video.videoHeight;
    const dimensions = w > 0 && h > 0 ? `${w} × ${h}` : undefined;
    const customInfo = dimensions ? `${duration}  |  ${dimensions}` : duration;

    onStatusInfo?.({ dimensions, customInfo });
  }, [onStatusInfo]);

  const src = `/api/workspace/file/raw?path=${encodeURIComponent(filePath)}`;

  return (
    <div className="flex flex-col items-center justify-center h-full w-full p-4">
      <video
        ref={videoRef}
        controls
        preload="metadata"
        onLoadedMetadata={handleLoadedMetadata}
        className="max-w-full max-h-full rounded-lg shadow-lg"
        style={{ backgroundColor: '#000' }}
      >
        <source src={src} type={filePath.endsWith('.webm') ? 'video/webm' : undefined} />
        <p className="text-sm text-[var(--color-text-muted)]">
          Your browser does not support this video format.
        </p>
      </video>
      <p className="mt-3 text-xs text-[var(--color-text-muted)] truncate max-w-full">
        {fileName}
      </p>
    </div>
  );
}
