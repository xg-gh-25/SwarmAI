/**
 * What a completed pipeline stage hands back in the chat: its one-line summary, and
 * each run document it wrote — REQUIREMENTS / DESIGN_SPEC / REVIEW / FIXING_SPEC /
 * REPORT (backend/core/pipeline_stage_events.py) — as the shared file row (design
 * 2026-10-26-turn-cards-unified.md §2, C): the row opens it in the Canvas; no excerpt
 * and no "Show full text". The row stays outside the stage fold so the produced
 * document remains discoverable.
 *
 * @exports StageResult, readRunDoc, OPEN_FILE_EVENT
 */
import type { ReactNode } from 'react';
// Same value as MarkdownRenderer.OPEN_FILE_EVENT, not imported from there: the test
// setup imports TurnZones (and so this module), and MarkdownRenderer would pull
// mermaid / katex / the Tauri client into every test file before its own mocks.
export const OPEN_FILE_EVENT = 'swarm:open-file';
import type { PipelineDocRef } from '../../../types';
import { FileItemRow, ItemBox } from './ItemRows';

type RenderText = (text: string, key: string) => ReactNode;

/** A run document's text, or null (unreadable, binary, gone). The API client is
 *  loaded on first use: TurnZones (and so this module) is imported by the test setup,
 *  and a static import would pull the HTTP client into every test before its mocks. */
export async function readRunDoc(path: string): Promise<string | null> {
  try {
    const { default: api } = await import('../../../services/api');
    const { data } = await api.get('/workspace/file', { params: { path } });
    const d = data as Record<string, unknown>;
    return d.encoding === 'utf-8' && typeof d.content === 'string' ? d.content : null;
  } catch {
    return null;
  }
}

/** A completed stage's summary line and the documents it wrote, as the shared file
 *  rows in one box (nothing when it has neither). *renderText* is kept for callers. */
export function StageResult({ summary, docs }: {
  summary?: string | null; docs?: PipelineDocRef[]; renderText?: RenderText;
}) {
  if (!summary && !docs?.length) return null;
  return (
    <div data-testid="stage-result" className="space-y-1.5">
      {summary && <div data-testid="stage-summary" className="text-[12.5px] text-[var(--color-text-secondary)] break-words">{summary}</div>}
      {!!docs?.length && (
        <ItemBox>
          {docs.map((d) => <FileItemRow key={d.absolutePath} testId="stage-doc" data={{ name: d.name }}
            file={{ path: d.name, absolutePath: d.absolutePath }} />)}
        </ItemBox>
      )}
    </div>
  );
}
