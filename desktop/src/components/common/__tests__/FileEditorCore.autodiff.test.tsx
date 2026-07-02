/**
 * Regression test for the auto-diff-on-open feature (Radar ✍ Changes, Run 2).
 *
 * Gate-1 caught that a bare `initialShowDiff` useState initializer would be
 * clobbered by the file-switch reset effect (which unconditionally set
 * showDiff=false on mount, because its deps include filePath/committedContent).
 * The fix makes the reset effect ALSO honor initialShowDiff. This test proves
 * the diff view is shown on open when initialShowDiff=true — i.e. the reset
 * effect no longer undoes it. Mutation check: revert the effect line to
 * `setShowDiff(false)` and this test goes RED.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import FileEditorCore from '../FileEditorCore';

function renderCore(initialShowDiff: boolean) {
  return render(
    <FileEditorCore
      filePath="src/mod.ts"
      fileName="mod.ts"
      workspaceId="ws"
      initialContent={"line1\nline2-changed\nline3"}
      committedContent={"line1\nline2\nline3"}
      initialShowDiff={initialShowDiff}
      variant="panel"
      readonly
      onSave={vi.fn().mockResolvedValue(undefined)}
      onClose={vi.fn()}
    />,
  );
}

describe('FileEditorCore auto-diff on open', () => {
  it('initialShowDiff=true → diff view is shown on open (survives the reset effect)', () => {
    renderCore(true);
    // The DiffView renders with data-testid="diff-view".
    expect(screen.getByTestId('diff-view')).toBeInTheDocument();
  });

  it('initialShowDiff=false → diff view NOT shown (default edit view)', () => {
    renderCore(false);
    expect(screen.queryByTestId('diff-view')).not.toBeInTheDocument();
  });
});
