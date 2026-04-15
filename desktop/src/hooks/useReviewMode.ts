/**
 * useReviewMode — State management for L3 inline review comments.
 *
 * Manages review mode toggle, comment CRUD operations, and formats
 * structured feedback messages for injection into chat input.
 *
 * Comments are persisted to sessionStorage keyed by filePath so they
 * survive tab switches within the same browser session (U10 fix).
 *
 * Key exports:
 * - `useReviewMode(content, filePath?)` — Hook returning review state + actions
 * - `ReviewComment`                     — Comment data interface
 */

import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import { findNearestHeading } from '../utils/sectionDetect';

// ── sessionStorage persistence (survives tab switch, cleared on window close) ──
const STORAGE_PREFIX = 'swarm:review-comments:';

function loadComments(filePath: string | undefined): ReviewComment[] {
  if (!filePath) return [];
  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + filePath);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveComments(filePath: string | undefined, comments: ReviewComment[]): void {
  if (!filePath) return;
  try {
    if (comments.length === 0) {
      sessionStorage.removeItem(STORAGE_PREFIX + filePath);
    } else {
      sessionStorage.setItem(STORAGE_PREFIX + filePath, JSON.stringify(comments));
    }
  } catch {
    // sessionStorage full or unavailable — degrade silently
  }
}

/** Diff context captured when a comment is made on a diff line. */
export interface DiffContext {
  type: 'added' | 'removed' | 'unchanged';
  oldLineNumber?: number;
  newLineNumber?: number;
  /** The actual line content from the diff. */
  content: string;
}

export interface ReviewComment {
  id: string;
  /** 1-based line number where the comment starts. */
  lineStart: number;
  /** 1-based line number where the comment ends (same as lineStart for single-line). */
  lineEnd: number;
  /** The user's comment text. */
  text: string;
  /** Auto-detected section heading above this line. */
  sectionHeading: string | null;
  /** Timestamp when the comment was created. */
  timestamp: number;
  /** When comment was made on a diff line, captures the diff context. */
  diffContext?: DiffContext;
}

function generateId(): string {
  return `rc-${crypto.randomUUID()}`;
}

export function useReviewMode(content: string, filePath?: string) {
  const [isReviewMode, setIsReviewMode] = useState(false);
  const [comments, setComments] = useState<ReviewComment[]>(() => loadComments(filePath));
  const [activePopoverLine, setActivePopoverLine] = useState<number | null>(null);
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null);

  // Track filePath to reload comments when switching files
  const prevFilePathRef = useRef(filePath);
  useEffect(() => {
    if (filePath !== prevFilePathRef.current) {
      // File changed — save current comments for old file, load for new
      saveComments(prevFilePathRef.current, comments);
      prevFilePathRef.current = filePath;
      const restored = loadComments(filePath);
      setComments(restored);
      if (restored.length > 0) {
        setIsReviewMode(true);  // Re-enter review mode if comments exist
      }
    }
  }, [filePath]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Sync to sessionStorage on every comment mutation
  useEffect(() => {
    saveComments(filePath, comments);
  }, [comments, filePath]);

  const contentLines = useMemo(() => content.split('\n'), [content]);

  const addComment = useCallback(
    (lineStart: number, lineEnd: number, text: string, diffContext?: DiffContext) => {
      const heading = findNearestHeading(contentLines, lineStart);
      const comment: ReviewComment = {
        id: generateId(),
        lineStart,
        lineEnd,
        text,
        sectionHeading: heading,
        timestamp: Date.now(),
        diffContext,
      };
      setComments((prev) => [...prev, comment]);
      setActivePopoverLine(null);
      setEditingCommentId(null);
    },
    [contentLines],
  );

  const updateComment = useCallback(
    (id: string, text: string) => {
      setComments((prev) =>
        prev.map((c) => (c.id === id ? { ...c, text } : c)),
      );
      setEditingCommentId(null);
      setActivePopoverLine(null);
    },
    [],
  );

  const removeComment = useCallback((id: string) => {
    setComments((prev) => prev.filter((c) => c.id !== id));
    setEditingCommentId(null);
    setActivePopoverLine(null);
  }, []);

  const clearComments = useCallback(() => {
    setComments([]);
    setActivePopoverLine(null);
    setEditingCommentId(null);
    // useEffect sync handles sessionStorage cleanup via comments=[]
  }, []);

  /** Get the comment for a specific line (if any). */
  const getCommentForLine = useCallback(
    (lineNumber: number): ReviewComment | undefined =>
      comments.find(
        (c) => lineNumber >= c.lineStart && lineNumber <= c.lineEnd,
      ),
    [comments],
  );

  /** Format all comments as structured feedback text for chat injection. */
  const formatFeedback = useCallback(
    (fileName: string): string => {
      if (comments.length === 0) return '';

      const hasDiffComments = comments.some((c) => c.diffContext);

      const lines = comments
        .sort((a, b) => a.lineStart - b.lineStart)
        .map((c, i) => {
          const section = c.sectionHeading || 'top';
          const lineRef =
            c.lineStart === c.lineEnd
              ? `Line ${c.lineStart}`
              : `Lines ${c.lineStart}-${c.lineEnd}`;

          // Base comment line
          let entry = `${i + 1}. [§${section}, ${lineRef}`;

          // Add diff type indicator when diff context is present
          if (c.diffContext) {
            entry += `, ${c.diffContext.type}`;
          }

          entry += `] ${c.text}`;

          // Add code context line for diff comments
          if (c.diffContext) {
            const prefix = c.diffContext.type === 'added' ? '+' : c.diffContext.type === 'removed' ? '-' : ' ';
            entry += `\n   \`${prefix} ${c.diffContext.content}\``;
          }

          return entry;
        });

      const header = hasDiffComments
        ? `📋 Review feedback on \`${fileName}\` (from diff view)`
        : `📋 Review feedback on \`${fileName}\``;

      return `${header}:\n\n${lines.join('\n')}\n\nPlease address each point and update the ${hasDiffComments ? 'code' : 'doc'}.`;
    },
    [comments],
  );

  const toggleReviewMode = useCallback(() => {
    setIsReviewMode((prev) => {
      if (prev) {
        // Exiting review mode — close popovers
        setActivePopoverLine(null);
        setEditingCommentId(null);
      }
      return !prev;
    });
  }, []);

  /** Reset review UI state on file switch.  Does NOT clear comments —
   *  the hook's internal file-change effect handles saving/restoring
   *  comments via sessionStorage.  Clearing here would race with the
   *  restore and wipe the user's work. */
  const resetReviewMode = useCallback(() => {
    setIsReviewMode(false);
    setActivePopoverLine(null);
    setEditingCommentId(null);
  }, []);

  return {
    isReviewMode,
    toggleReviewMode,
    resetReviewMode,
    comments,
    addComment,
    updateComment,
    removeComment,
    clearComments,
    getCommentForLine,
    formatFeedback,
    activePopoverLine,
    setActivePopoverLine,
    editingCommentId,
    setEditingCommentId,
  };
}
