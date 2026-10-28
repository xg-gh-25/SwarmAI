/**
 * Method — the reference a person reads once, not every day: the Eval-First guide
 * (how to know whether an agent is good, and where each part lives here), then the
 * judges as they run (model, prompt, settings, calibration) with the judge model
 * setting. The judges sit under the guide's anchor ``judges``.
 */
import { GuideTab } from './GuideTab';
import { JudgesTab } from './JudgesTab';

export function MethodTab() {
  return (
    <div data-testid="eval-method">
      <GuideTab />
      <div className="max-w-[880px] mx-auto px-7 -mt-12 pb-20"><JudgesTab /></div>
    </div>
  );
}
