/**
 * useReviewMode — State management for L3 inline review comments.
 *
 * Manages review mode toggle, comment CRUD operations, and formats
 * structured feedback messages for injection into chat input.
 *
 * Comments are a batch: every entry point (a gutter line, a diff line, a text
 * selection in the source view or the markdown preview) adds to the same list, and
 * the Canvas sends the whole list as ONE message (`formatFeedback`), so the agent
 * revises once for all of them.
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
  /** 1-based line number where the comment starts; 0 when the place has no line
   *  (a selection in the markdown preview, which has no line map — `quote` says where). */
  lineStart: number;
  /** 1-based line number where the comment ends (same as lineStart for single-line); 0 with lineStart. */
  lineEnd: number;
  /** The user's comment text. */
  text: string;
  /** Auto-detected section heading above this line. */
  sectionHeading: string | null;
  /** Timestamp when the comment was created. */
  timestamp: number;
  /** When comment was made on a diff line, captures the diff context. */
  diffContext?: DiffContext;
  /** Snapshot of the target line content at comment creation time. */
  originalLineContent?: string;
  /** The text the person selected, for a comment made on a selection. */
  quote?: string;
}

/** Whether a comment is anchored to source lines (gutter/diff/source selection). */
export function hasLines(c: ReviewComment): boolean {
  return c.lineStart >= 1;
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
   
  // excluded: we read current comments at file-switch time only. Including it would
  // cause infinite loop (comment mutation → save → re-render → effect → save → ...).
  }, [filePath]); // eslint-disable-line react-hooks/exhaustive-deps -- comments intentionally excluded

  // Sync to sessionStorage on every comment mutation
  useEffect(() => {
    saveComments(filePath, comments);
  }, [comments, filePath]);

  const contentLines = useMemo(() => content.split('\n'), [content]);

  const addComment = useCallback(
    (lineStart: number, lineEnd: number, text: string, diffContext?: DiffContext) => {
      const heading = findNearestHeading(contentLines, lineStart);
      // Capture the original line content for "applied" detection
      const originalLineContent = contentLines[lineStart - 1] ?? '';
      const comment: ReviewComment = {
        id: generateId(),
        lineStart,
        lineEnd,
        text,
        sectionHeading: heading,
        timestamp: Date.now(),
        diffContext,
        originalLineContent,
      };
      setComments((prev) => [...prev, comment]);
      setActivePopoverLine(null);
      setEditingCommentId(null);
    },
    [contentLines],
  );

  /** Add a comment on a selection. `lines` is the selection's source lines, or null
   *  in the markdown preview (no line map). */
  const addSelectionComment = useCallback(
    (quote: string, text: string, lines: [number, number] | null): ReviewComment => {
      const [lineStart, lineEnd] = lines ?? [0, 0];
      const comment: ReviewComment = {
        id: generateId(),
        lineStart,
        lineEnd,
        text,
        sectionHeading: lines ? findNearestHeading(contentLines, lineStart) : null,
        timestamp: Date.now(),
        originalLineContent: lines ? contentLines[lineStart - 1] ?? '' : undefined,
        quote,
      };
      setComments((prev) => [...prev, comment]);
      return comment;
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
        (c) => hasLines(c) && lineNumber >= c.lineStart && lineNumber <= c.lineEnd,
      ),
    [comments],
  );

  /** Format all comments as ONE structured message for chat injection: comments on
   *  lines in line order, then preview-selection comments in the order they were made.
   *  Each comment is shown whole, with the text it was made on. */
  const formatFeedback = useCallback(
    (fileRef: string): string => {
      if (comments.length === 0) return '';

      const hasDiffComments = comments.some((c) => c.diffContext);
      const ordered = [...comments].sort((a, b) => {
        const ka = hasLines(a) ? a.lineStart : Number.MAX_SAFE_INTEGER;
        const kb = hasLines(b) ? b.lineStart : Number.MAX_SAFE_INTEGER;
        return ka - kb || a.timestamp - b.timestamp;
      });

      const lines = ordered.map((c, i) => {
        const tags: string[] = [];
        if (hasLines(c)) {
          tags.push(`§${c.sectionHeading || 'top'}`);
          tags.push(c.lineStart === c.lineEnd ? `Line ${c.lineStart}` : `Lines ${c.lineStart}-${c.lineEnd}`);
        } else {
          tags.push('preview selection');
        }
        if (c.diffContext) tags.push(c.diffContext.type);

        let entry = `${i + 1}. [${tags.join(', ')}] ${c.text}`;

        if (c.quote) {
          const quoted = c.quote.replace(/\s+$/, '').split('\n').map((l) => `   > ${l}`).join('\n');
          entry += `\n${quoted}`;
        } else if (c.diffContext) {
          const prefix = c.diffContext.type === 'added' ? '+' : c.diffContext.type === 'removed' ? '-' : ' ';
          entry += `\n   \`${prefix} ${c.diffContext.content}\``;
        } else if (c.originalLineContent?.trim()) {
          // A gutter comment: the line it was made on, so a stale line number still points somewhere.
          entry += `\n   > ${c.originalLineContent}`;
        }
        return entry;
      });

      const header = hasDiffComments
        ? `📋 Review feedback on \`${fileRef}\` (from diff view)`
        : `📋 Review feedback on \`${fileRef}\``;
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

  /** Check if a comment has been "applied" — its target line content changed. */
  const isCommentApplied = useCallback(
    (comment: ReviewComment): boolean => {
      if (!hasLines(comment) || !comment.originalLineContent) return false;
      const currentLine = contentLines[comment.lineStart - 1];
      // If line no longer exists or content differs → applied
      return currentLine === undefined || currentLine !== comment.originalLineContent;
    },
    [contentLines],
  );

  return {
    isReviewMode,
    toggleReviewMode,
    resetReviewMode,
    comments,
    addComment,
    addSelectionComment,
    updateComment,
    removeComment,
    clearComments,
    getCommentForLine,
    formatFeedback,
    isCommentApplied,
    activePopoverLine,
    setActivePopoverLine,
    editingCommentId,
    setEditingCommentId,
  };
}
