/**
 * Unit tests for ``hooks/useReviewMode.ts``.
 *
 * Tests the review mode state management hook used by L3 inline comments.
 *
 * Key properties verified:
 * - Comment CRUD operations (add, update, remove, clear)
 * - Review mode toggle and reset
 * - getCommentForLine lookup correctness
 * - formatFeedback structured output
 * - Popover state management
 */

import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useReviewMode } from '../hooks/useReviewMode';

const SAMPLE_CONTENT = [
  '# Title',
  '',
  '## Section A',
  'Line 4 content',
  'Line 5 content',
  '',
  '## Section B',
  'Line 8 content',
].join('\n');

describe('useReviewMode', () => {
  describe('toggle and reset', () => {
    it('starts with review mode off', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      expect(result.current.isReviewMode).toBe(false);
    });

    it('toggles review mode on and off', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.toggleReviewMode());
      expect(result.current.isReviewMode).toBe(true);
      act(() => result.current.toggleReviewMode());
      expect(result.current.isReviewMode).toBe(false);
    });

    it('closes popovers when exiting review mode', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.toggleReviewMode());
      act(() => result.current.setActivePopoverLine(5));
      expect(result.current.activePopoverLine).toBe(5);
      act(() => result.current.toggleReviewMode());
      expect(result.current.activePopoverLine).toBeNull();
    });

    it('resetReviewMode clears UI state but preserves comments for sessionStorage', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.toggleReviewMode());
      act(() => result.current.addComment(4, 4, 'test'));
      act(() => result.current.setActivePopoverLine(4));
      act(() => result.current.resetReviewMode());
      expect(result.current.isReviewMode).toBe(false);
      // Comments preserved — sessionStorage handles lifecycle on file switch
      expect(result.current.comments).toHaveLength(1);
      expect(result.current.activePopoverLine).toBeNull();
      expect(result.current.editingCommentId).toBeNull();
    });
  });

  describe('comment CRUD', () => {
    it('adds a comment with auto-detected section heading', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'needs work'));
      expect(result.current.comments).toHaveLength(1);
      const c = result.current.comments[0];
      expect(c.lineStart).toBe(4);
      expect(c.lineEnd).toBe(4);
      expect(c.text).toBe('needs work');
      expect(c.sectionHeading).toBe('Section A');
      expect(c.id).toMatch(/^rc-/);
    });

    it('adds comment under Section B for line 8', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(8, 8, 'fix this'));
      expect(result.current.comments[0].sectionHeading).toBe('Section B');
    });

    it('clears popover state after adding comment', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.setActivePopoverLine(4));
      act(() => result.current.addComment(4, 4, 'test'));
      expect(result.current.activePopoverLine).toBeNull();
    });

    it('updates an existing comment', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'original'));
      const id = result.current.comments[0].id;
      act(() => result.current.updateComment(id, 'updated'));
      expect(result.current.comments[0].text).toBe('updated');
      expect(result.current.comments).toHaveLength(1);
    });

    it('removes a comment by id', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'first'));
      act(() => result.current.addComment(8, 8, 'second'));
      expect(result.current.comments).toHaveLength(2);
      const idToRemove = result.current.comments[0].id;
      act(() => result.current.removeComment(idToRemove));
      expect(result.current.comments).toHaveLength(1);
      expect(result.current.comments[0].text).toBe('second');
    });

    it('clearComments removes all comments', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'a'));
      act(() => result.current.addComment(8, 8, 'b'));
      act(() => result.current.clearComments());
      expect(result.current.comments).toHaveLength(0);
    });
  });

  describe('getCommentForLine', () => {
    it('returns undefined for lines without comments', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      expect(result.current.getCommentForLine(1)).toBeUndefined();
    });

    it('returns the comment for an exact line match', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'found'));
      expect(result.current.getCommentForLine(4)?.text).toBe('found');
    });

    it('returns the comment for a line within a range', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 6, 'range comment'));
      expect(result.current.getCommentForLine(5)?.text).toBe('range comment');
      expect(result.current.getCommentForLine(3)).toBeUndefined();
      expect(result.current.getCommentForLine(7)).toBeUndefined();
    });
  });


  describe('diff context support', () => {
    it('AC1: addComment accepts optional diffContext', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      const diffCtx = {
        type: 'added' as const,
        newLineNumber: 42,
        content: 'const x = 1;',
      };
      act(() => result.current.addComment(4, 4, 'wrong name', diffCtx));
      expect(result.current.comments).toHaveLength(1);
      expect(result.current.comments[0].diffContext).toEqual(diffCtx);
    });

    it('AC1: addComment without diffContext keeps it undefined', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'no diff'));
      expect(result.current.comments[0].diffContext).toBeUndefined();
    });

    it('AC2: formatFeedback includes diff context when present', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'fix this variable', {
        type: 'added',
        newLineNumber: 42,
        content: 'const badName = getData();',
      }));
      const feedback = result.current.formatFeedback('code.ts');
      // Should contain the diff indicator
      expect(feedback).toContain('added');
      // Should contain the actual code line
      expect(feedback).toContain('const badName = getData()');
    });

    it('AC2: formatFeedback for removed line includes old line number', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'why removed?', {
        type: 'removed',
        oldLineNumber: 15,
        content: 'const oldVar = legacy();',
      }));
      const feedback = result.current.formatFeedback('code.ts');
      expect(feedback).toContain('removed');
      expect(feedback).toContain('const oldVar = legacy()');
    });

    it('AC2: formatFeedback mixes diff and non-diff comments correctly', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      // Non-diff comment
      act(() => result.current.addComment(4, 4, 'regular comment'));
      // Diff comment
      act(() => result.current.addComment(8, 8, 'diff comment', {
        type: 'added',
        newLineNumber: 50,
        content: 'new code here',
      }));
      const feedback = result.current.formatFeedback('mixed.ts');
      expect(feedback).toContain('regular comment');
      expect(feedback).toContain('diff comment');
      expect(feedback).toContain('new code here');
    });

    it('AC4: comments survive — stored in state regardless of view mode', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'persists', {
        type: 'unchanged',
        oldLineNumber: 4,
        newLineNumber: 4,
        content: 'same line',
      }));
      // Toggle review mode off and on — comments should persist
      act(() => result.current.toggleReviewMode());
      act(() => result.current.toggleReviewMode());
      expect(result.current.comments).toHaveLength(1);
      expect(result.current.comments[0].text).toBe('persists');
      expect(result.current.comments[0].diffContext?.type).toBe('unchanged');
    });

    it('AC5: clearComments clears diffContext comments', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'diff comment', {
        type: 'added',
        newLineNumber: 10,
        content: 'code',
      }));
      act(() => result.current.clearComments());
      expect(result.current.comments).toHaveLength(0);
    });

    it('AC2: formatFeedback diff comment uses diff prefix notation', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'check this', {
        type: 'added',
        newLineNumber: 22,
        content: 'const result = compute();',
      }));
      const feedback = result.current.formatFeedback('file.ts');
      // Should use + prefix for added lines
      expect(feedback).toMatch(/\+\s*const result = compute\(\)/);
    });
  });

  describe('formatFeedback', () => {
    it('returns empty string when no comments', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      expect(result.current.formatFeedback('test.md')).toBe('');
    });

    it('formats single comment with section and line reference', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 4, 'needs metrics'));
      const feedback = result.current.formatFeedback('review.md');
      expect(feedback).toContain('📋 Review feedback on `review.md`');
      expect(feedback).toContain('§Section A');
      expect(feedback).toContain('Line 4');
      expect(feedback).toContain('needs metrics');
      expect(feedback).toContain('Please address each point');
    });

    it('formats multiple comments sorted by line number', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(8, 8, 'second'));
      act(() => result.current.addComment(4, 4, 'first'));
      const feedback = result.current.formatFeedback('doc.md');
      const firstIdx = feedback.indexOf('first');
      const secondIdx = feedback.indexOf('second');
      expect(firstIdx).toBeLessThan(secondIdx);
      expect(feedback).toContain('1. [§Section A');
      expect(feedback).toContain('2. [§Section B');
    });

    it('formats range comments with Lines X-Y', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.addComment(4, 6, 'range issue'));
      const feedback = result.current.formatFeedback('doc.md');
      expect(feedback).toContain('Lines 4-6');
    });

    it('uses "top" when no section heading found', () => {
      const content = 'no headings\njust text';
      const { result } = renderHook(() => useReviewMode(content));
      act(() => result.current.addComment(1, 1, 'comment'));
      const feedback = result.current.formatFeedback('doc.md');
      expect(feedback).toContain('§top');
    });
  });
});
