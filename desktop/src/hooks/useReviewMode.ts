/**
 * useReviewMode — State management for L3 inline review comments.
 *
 * Manages review mode toggle, comment CRUD operations, and formats
 * structured feedback messages for injection into chat input.
 *
 * Key exports:
 * - `useReviewMode(content)` — Hook returning review state + actions
 * - `ReviewComment`          — Comment data interface
 */

import { useState, useCallback, useMemo } from 'react';
import { findNearestHeading } from '../utils/sectionDetect';

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

export function useReviewMode(content: string) {
  const [isReviewMode, setIsReviewMode] = useState(false);
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [activePopoverLine, setActivePopoverLine] = useState<number | null>(null);
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null);

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

  /** Hard-reset all review state. Safe to call from useEffect without
   *  depending on isReviewMode (avoids stale-closure / missing-dep issues). */
  const resetReviewMode = useCallback(() => {
    setIsReviewMode(false);
    setComments([]);
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
