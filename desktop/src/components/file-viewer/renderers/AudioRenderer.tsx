/**
 * AudioRenderer -- Native HTML5 audio player for browser-compatible formats
 * (MP3, WAV, OGG, M4A, FLAC, AAC, OPUS).
 *
 * Features:
 *   - Large centered audio icon with file name
 *   - Streams from /api/workspace/file/raw endpoint
 *   - Native browser controls (play, pause, seek, volume)
 *   - Reports duration via onStatusInfo on loadedmetadata
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

export default function AudioRenderer({
  filePath,
  fileName,
  onStatusInfo,
}: RendererProps) {
  const audioRef = useRef<HTMLAudioElement>(null);

  const handleLoadedMetadata = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !isFinite(audio.duration)) return;

    const duration = formatDuration(audio.duration);
    onStatusInfo?.({ customInfo: `Duration: ${duration}` });
  }, [onStatusInfo]);

  const src = `/api/workspace/file/raw?path=${encodeURIComponent(filePath)}`;

  return (
    <div className="flex flex-col items-center justify-center h-full w-full gap-6 p-8">
      {/* Large icon */}
      <div
        className="flex items-center justify-center w-24 h-24 rounded-2xl"
        style={{ backgroundColor: 'var(--color-hover)' }}
      >
        <span
          className="material-symbols-outlined"
          style={{ fontSize: '48px', color: 'var(--color-primary)' }}
        >
          audiotrack
        </span>
      </div>

      {/* File name */}
      <p
        className="text-sm font-medium text-[var(--color-text)] truncate max-w-full text-center"
        title={fileName}
      >
        {fileName}
      </p>

      {/* Native audio player */}
      <audio
        ref={audioRef}
        controls
        preload="metadata"
        onLoadedMetadata={handleLoadedMetadata}
        className="w-full max-w-md"
      >
        <source src={src} />
        <p className="text-sm text-[var(--color-text-muted)]">
          Your browser does not support this audio format.
        </p>
      </audio>
    </div>
  );
}
