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

    it('resetReviewMode clears all state', () => {
      const { result } = renderHook(() => useReviewMode(SAMPLE_CONTENT));
      act(() => result.current.toggleReviewMode());
      act(() => result.current.addComment(4, 4, 'test'));
      act(() => result.current.setActivePopoverLine(4));
      act(() => result.current.resetReviewMode());
      expect(result.current.isReviewMode).toBe(false);
      expect(result.current.comments).toHaveLength(0);
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
