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
    (lineStart: number, lineEnd: number, text: string) => {
      const heading = findNearestHeading(contentLines, lineStart);
      const comment: ReviewComment = {
        id: generateId(),
        lineStart,
        lineEnd,
        text,
        sectionHeading: heading,
        timestamp: Date.now(),
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

      const lines = comments
        .sort((a, b) => a.lineStart - b.lineStart)
        .map((c, i) => {
          const section = c.sectionHeading || 'top';
          const lineRef =
            c.lineStart === c.lineEnd
              ? `Line ${c.lineStart}`
              : `Lines ${c.lineStart}-${c.lineEnd}`;
          return `${i + 1}. [§${section}, ${lineRef}] ${c.text}`;
        });

      return `📋 Review feedback on \`${fileName}\`:\n\n${lines.join('\n')}\n\nPlease address each point and update the doc.`;
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
