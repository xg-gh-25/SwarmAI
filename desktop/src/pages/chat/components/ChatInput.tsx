import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { FileAttachment, WorkspaceConfig, Skill, MCPServer, Plugin } from '../../../types';
import { FileAttachmentButton, FileAttachmentPreview, AttachedFileChips } from '../../../components/chat';
import { ReadOnlyChips } from '../../../components/common';
import { SLASH_COMMANDS } from '../constants';
import type { FileTreeItem } from '../../../components/workspace-explorer/FileTreeNode';

interface ChatInputProps {
  inputValue: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  isStreaming: boolean;
  selectedAgentId: string | null;
  selectedWorkspace: WorkspaceConfig | null;
  attachments: FileAttachment[];
  onAddFiles: (files: File[]) => void;
  onRemoveFile: (id: string) => void;
  isProcessingFiles: boolean;
  fileError: string | null;
  canAddMore: boolean;
  agentSkills: Skill[];
  agentMCPs: MCPServer[];
  agentPlugins: Plugin[];
  isLoadingSkills: boolean;
  isLoadingMCPs: boolean;
  isLoadingPlugins: boolean;
  allowAllSkills?: boolean;
  /** Files attached from Workspace Explorer (context files) */
  attachedContextFiles?: FileTreeItem[];
  /** Callback to remove a context file */
  onRemoveContextFile?: (file: FileTreeItem) => void;
}

/**
 * Chat Input Component with file attachments, workspace selector, and slash commands
 */
export function ChatInput({
  inputValue,
  onInputChange,
  onSend,
  onStop,
  isStreaming,
  selectedAgentId,
  selectedWorkspace,
  attachments,
  onAddFiles,
  onRemoveFile,
  isProcessingFiles,
  fileError,
  canAddMore,
  agentSkills,
  agentMCPs,
  agentPlugins,
  isLoadingSkills,
  isLoadingMCPs,
  isLoadingPlugins,
  allowAllSkills,
  attachedContextFiles,
  onRemoveContextFile,
}: ChatInputProps) {
  const { t } = useTranslation();
  const [showCommandSuggestions, setShowCommandSuggestions] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [isDragging, setIsDragging] = useState(false);

  // Filter commands based on input
  const filteredCommands = SLASH_COMMANDS.filter((cmd) =>
    cmd.name.toLowerCase().startsWith(inputValue.toLowerCase())
  );

  // Handle input change with slash command detection
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    onInputChange(value);

    if (value.startsWith('/') && !value.includes(' ')) {
      setShowCommandSuggestions(true);
      setSelectedCommandIndex(0);
    } else {
      setShowCommandSuggestions(false);
    }
  };

  // Handle command selection
  const handleSelectCommand = (command: string) => {
    onInputChange(command + ' ');
    setShowCommandSuggestions(false);
  };

  // Handle paste event for images
  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const imageFiles: File[] = [];
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) {
            imageFiles.push(file);
          }
        }
      }
      if (imageFiles.length > 0) {
        e.preventDefault();
        onAddFiles(imageFiles);
      }
    },
    [onAddFiles]
  );

  // Drag handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('Files')) {
      e.preventDefault();
      setIsDragging(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('Files')) {
      e.preventDefault();
      setIsDragging(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const files = Array.from(e.dataTransfer.files);
      if (files.length > 0) {
        onAddFiles(files);
      }
    },
    [onAddFiles]
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Handle slash command navigation
    if (showCommandSuggestions && filteredCommands.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedCommandIndex((prev) => (prev + 1) % filteredCommands.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedCommandIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        handleSelectCommand(filteredCommands[selectedCommandIndex].name);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setShowCommandSuggestions(false);
        return;
      }
    }

    // Normal enter to send
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const hasAttachments = attachments.some((a) => a.base64);
  const canSend = (inputValue.trim() || hasAttachments) && selectedAgentId;

  return (
    <div className="p-6">
      <div className="max-w-3xl mx-auto">
        {/* Input Container with drag-and-drop */}
        <div
          className={clsx(
            'bg-[var(--color-card)] border rounded-2xl p-3 relative transition-all',
            isDragging
              ? 'border-primary bg-primary/5'
              : 'border-[var(--color-border)]'
          )}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {/* Drag Overlay */}
          {isDragging && (
            <div className="absolute inset-0 bg-primary/10 flex items-center justify-center rounded-2xl z-10 pointer-events-none">
              <div className="flex flex-col items-center gap-2">
                <span className="material-symbols-outlined text-primary text-3xl">upload_file</span>
                <span className="text-primary font-medium">Drop files here</span>
              </div>
            </div>
          )}

          {/* File Attachment Preview */}
          {attachments.length > 0 && <FileAttachmentPreview attachments={attachments} onRemove={onRemoveFile} />}

          {/* File Error */}
          {fileError && (
            <div className="mb-3 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
              {fileError}
            </div>
          )}

          {/* Attached Context Files (from Workspace Explorer) */}
          {attachedContextFiles && attachedContextFiles.length > 0 && onRemoveContextFile && (
            <AttachedFileChips files={attachedContextFiles} onRemoveFile={onRemoveContextFile} />
          )}

          {/* Workspace Indicator */}
          {selectedWorkspace && (
            <div className="mb-3 px-3 py-2 bg-primary/10 border border-primary/30 rounded-lg flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm">
                <span className="text-lg">{selectedWorkspace.icon || '📁'}</span>
                <span className="text-primary font-medium">{selectedWorkspace.name}</span>
                <span className="text-[var(--color-text-muted)] truncate max-w-[300px]">
                  {selectedWorkspace.filePath}
                </span>
              </div>
            </div>
          )}

          {/* Input Row */}
          <div className="relative flex items-center gap-3">
            {/* File Attachment Button */}
            <FileAttachmentButton onFilesSelected={onAddFiles} disabled={isProcessingFiles || isStreaming} canAddMore={canAddMore} />

            {/* Slash Command Suggestions */}
            {showCommandSuggestions && filteredCommands.length > 0 && (
              <div className="absolute bottom-full left-0 mb-2 w-64 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl overflow-hidden z-10">
                <div className="px-3 py-2 border-b border-[var(--color-border)]">
                  <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">
                    Commands
                  </span>
                </div>
                {filteredCommands.map((cmd, index) => (
                  <button
                    key={cmd.name}
                    onClick={() => handleSelectCommand(cmd.name)}
                    className={clsx(
                      'w-full px-3 py-2.5 flex items-start gap-3 text-left transition-colors',
                      index === selectedCommandIndex
                        ? 'bg-primary text-white'
                        : 'text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                    )}
                  >
                    <span className="material-symbols-outlined text-lg mt-0.5">terminal</span>
                    <div>
                      <p className="font-medium">{cmd.name}</p>
                      <p
                        className={clsx(
                          'text-xs',
                          index === selectedCommandIndex ? 'text-white/70' : 'text-[var(--color-text-muted)]'
                        )}
                      >
                        {cmd.description}
                      </p>
                    </div>
                  </button>
                ))}
                <div className="px-3 py-1.5 border-t border-[var(--color-border)] bg-[var(--color-hover)]/50">
                  <span className="text-xs text-[var(--color-text-muted)]">
                    <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">↑↓</kbd> navigate
                    <span className="mx-2">·</span>
                    <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">Tab</kbd> select
                    <span className="mx-2">·</span>
                    <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">Esc</kbd> close
                  </span>
                </div>
              </div>
            )}

            {/* Text Input — disabled during streaming to prevent concurrent sessions */}
            <textarea
              value={inputValue}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder={isStreaming ? t('chat.streamingPlaceholder', 'Waiting for response...') : t('chat.placeholder')}
              rows={1}
              disabled={isStreaming}
              className={clsx(
                'flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none py-2',
                isStreaming && 'opacity-50 cursor-not-allowed'
              )}
            />

            {/* Send Button */}
            <button
              onClick={isStreaming ? onStop : onSend}
              disabled={!isStreaming && !canSend}
              className={clsx(
                'w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 transition-colors',
                isStreaming
                  ? 'bg-red-500 hover:bg-red-600'
                  : 'bg-primary hover:bg-primary-hover',
                !isStreaming && !canSend && 'opacity-50 cursor-not-allowed'
              )}
              title={
                isStreaming
                  ? 'Stop generation'
                  : attachments.length > 0
                      ? 'Send with attachments'
                      : 'Send message'
              }
            >
              {isStreaming ? (
                <span className="material-symbols-outlined text-white text-xl">stop</span>
              ) : (
                <span className="material-symbols-outlined text-white text-xl">arrow_upward</span>
              )}
            </button>
          </div>

          {/* Bottom Row - Skills & Commands hint */}
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-[var(--color-border)]/50">
            <div className="flex items-center gap-4">
              <ReadOnlyChips
                label="Plugins"
                icon="extension"
                items={agentPlugins.map((p) => ({
                  id: p.id,
                  name: p.name,
                  description: p.description,
                }))}
                emptyText=""
                loading={isLoadingPlugins}
              />

              <ReadOnlyChips
                label="Skills"
                icon="auto_fix_high"
                items={agentSkills.map((s) => ({
                  id: s.id,
                  name: s.name,
                  description: s.description,
                }))}
                emptyText=""
                loading={isLoadingSkills}
                badgeOverride={allowAllSkills ? 'All' : undefined}
              />

              <ReadOnlyChips
                label="MCPs"
                icon="widgets"
                items={agentMCPs.map((m) => ({
                  id: m.id,
                  name: m.name,
                  description: m.description,
                }))}
                emptyText=""
                loading={isLoadingMCPs}
              />
            </div>

            <span className="text-xs text-[var(--color-text-muted)]">
              Type <kbd className="px-1.5 py-0.5 bg-[var(--color-hover)] rounded text-xs mx-1">/</kbd> for commands
            </span>
          </div>
        </div>

        {/* Footer */}
        <p className="text-center text-xs text-[var(--color-text-muted)]/60 mt-4 uppercase tracking-wider">
          {'Immersive Workspace • Powered by Claude Code'}
        </p>
      </div>
    </div>
  );
}
