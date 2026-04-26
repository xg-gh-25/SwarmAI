/**
 * Swarm Output section — builds + content + recent files.
 *
 * Click → open file in editor (no chat input).
 * Three sub-groups rendered as inline tabs or vertical list.
 * Shared across WelcomeScreen and RadarSidebar.
 */

import { useState } from 'react';
import type { SwarmOutput } from '../../../../services/system';
import { openWorkspaceFile, formatRelativeTime } from './BriefingUtils';

const CONTENT_TYPE_ICON: Record<string, string> = {
  video: '🎬',
  poster: '🖼',
  podcast: '🎙',
  article: '📄',
};

type OutputTab = 'builds' | 'content' | 'files';

interface SwarmOutputSectionProps {
  output: SwarmOutput;
  compact?: boolean;
}

export function SwarmOutputSection({ output }: SwarmOutputSectionProps) {
  const hasBuild = output.builds.length > 0;
  const hasContent = output.content.length > 0;
  const hasFiles = output.files.length > 0;
  const totalCount = output.builds.length + output.content.length + output.files.length;

  if (totalCount === 0) return null;

  const tabs: OutputTab[] = [];
  if (hasBuild) tabs.push('builds');
  if (hasContent) tabs.push('content');
  if (hasFiles) tabs.push('files');

  const [activeTab, setActiveTab] = useState<OutputTab>(tabs[0]);

  // Guard: if active tab's data disappears on refresh, fall back to first available
  const effectiveTab = tabs.includes(activeTab) ? activeTab : tabs[0];

  const tabLabels: Record<OutputTab, { icon: string; label: string }> = {
    builds: { icon: '🔧', label: 'Builds' },
    content: { icon: '🎬', label: 'Content' },
    files: { icon: '📦', label: 'Files' },
  };

  return (
    <div>
      {/* Tab bar */}
      {tabs.length > 1 && (
        <div className="flex gap-1 mb-1.5">
          {tabs.map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`text-[11px] px-2 py-0.5 rounded transition-colors cursor-pointer ${
                effectiveTab === tab
                  ? 'bg-[var(--color-bg-hover)] text-[var(--color-text)]'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              {tabLabels[tab].icon} {tabLabels[tab].label}
            </button>
          ))}
        </div>
      )}

      {/* Builds */}
      {effectiveTab === 'builds' && (
        <div className="space-y-0.5">
          {output.builds.map((build) => (
            <button
              key={build.runId}
              type="button"
              onClick={() => openWorkspaceFile(build.reportFile)}
              className="flex items-center gap-2 w-full text-left px-1 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer"
            >
              <span className="text-[11px]">✅</span>
              <span className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1">
                {build.title}
              </span>
              {build.confidence != null && (
                <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] font-mono">
                  {build.confidence}/10
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      {effectiveTab === 'content' && (
        <div className="space-y-0.5">
          {output.content.map((item) => (
            <button
              key={item.slug}
              type="button"
              onClick={() => openWorkspaceFile(item.contentPackage)}
              className="flex items-center gap-2 w-full text-left px-1 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer"
            >
              <span className="text-[11px]">
                {CONTENT_TYPE_ICON[item.type] ?? '📄'}
              </span>
              <span className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1">
                {item.title}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Files (artifacts) */}
      {effectiveTab === 'files' && (
        <div className="space-y-0.5">
          {output.files.map((file) => (
            <button
              key={file.path}
              type="button"
              onClick={() => openWorkspaceFile(file.path)}
              className="flex items-center gap-2 w-full text-left px-1 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer"
            >
              <span className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1">
                {file.title}
              </span>
              <span className="shrink-0 text-[10px] text-[var(--color-text-muted)]">
                {formatRelativeTime(file.modifiedAt)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
