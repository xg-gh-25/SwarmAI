/**
 * editorLimits — the size limits of the text editor and its previews, in one
 * module so FileEditorCore and filePreviews share them without an import cycle.
 *
 * @exports HIGHLIGHT_MAX_CHARS, shouldProcessSync
 */

/**
 * Character-count ceiling above which O(n) main-thread text work is skipped to
 * avoid freezing the UI. `hljs.highlight`, `computeLineDiff`, and
 * `findAllMatches` all run synchronously over the full content on the render
 * path; on a large file (e.g. a 379K-char doc) each blocks the main thread for
 * hundreds of ms — on every keystroke while editing. Above this threshold the
 * editor degrades gracefully (plaintext, diff/search disabled) rather than hang.
 *
 * 100K chars: a typical source file is well under (fast to highlight); the files
 * that froze the UII in practice were far above. `content.length` (not line
 * count) is the right metric — hljs/diff/search cost scales with total
 * characters, and a single-line minified/base64 blob freezes just as hard as a
 * many-line file of the same size.
 */
export const HIGHLIGHT_MAX_CHARS = 100_000;

/** Whether synchronous main-thread text processing (highlight/diff/search) is
 *  safe for content of this length. Pure — exported for testing. */
export function shouldProcessSync(contentLength: number): boolean {
  return contentLength <= HIGHLIGHT_MAX_CHARS;
}
