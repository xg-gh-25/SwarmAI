/**
 * ProjectAgentChip — the Brain whose Project Agent this chat session works under,
 * shown in the ChatInput bottom row next to the TSCC button.
 *
 * Data: ``SystemPromptMetadata.projectAgent`` (backend session_unit.project_agent_fact),
 * which arrives with the per-turn ``system_prompt_metadata`` SSE event and the TSCC
 * system-prompt endpoint, kept per tab like the rest of the metadata. Two cases, told
 * apart in the tooltip because they bind differently:
 *   - not pinned: the Brain's contract was given to this session with its DDD block;
 *     the agent reads it as guidance, nothing enforces it;
 *   - pinned (a session dispatched to a Hive): its hooks enforce the contract (the
 *     Brain's gates, the actions kept for the person), under the pinned mode.
 * Renders nothing when the session works under no Brain. Clicking opens the Brain in
 * Brain Hub (the General Brain has no project entry, so it opens Brain Hub itself).
 */

import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import type { ProjectAgentFact } from '../../../types';
import { openOverlayAt } from '../../../utils/overlayTarget';

export interface ProjectAgentChipProps {
  fact: ProjectAgentFact | null | undefined;
}

export function ProjectAgentChip({ fact }: ProjectAgentChipProps) {
  const { t } = useTranslation();
  if (!fact || !fact.brain) return null;
  const general = fact.brain === 'General';
  const name = general ? tf(t, 'ui.ProjectAgentChip.general', 'General Brain') : fact.brain;
  const title = fact.pinned
    ? tf(t, 'ui.ProjectAgentChip.pinnedTitle',
        'This session works under the Project Agent of {{brain}} (kind of work: {{mode}}); its rules and gates are enforced. Click to open the Brain.',
        { brain: name, mode: fact.mode || '—' })
    : tf(t, 'ui.ProjectAgentChip.title',
        "This session works for {{brain}}: its Project Agent contract was given to the agent as guidance (not enforced here). Click to open the Brain.",
        { brain: name });
  const open = () => {
    if (general) window.dispatchEvent(new CustomEvent('swarm:show-brain-hub'));
    else openOverlayAt('brain-hub', fact.brain);
  };
  return (
    <button
      type="button"
      onClick={open}
      title={title}
      aria-label={title}
      data-testid="project-agent-chip"
      className="btn-sm btn-soft inline-flex items-center gap-1 max-w-[180px]"
    >
      <span className="material-symbols-outlined icon-xs" aria-hidden="true">
        {fact.pinned ? 'lock' : 'psychology'}
      </span>
      <span className="truncate">{name}</span>
    </button>
  );
}
