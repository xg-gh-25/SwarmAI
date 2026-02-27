/**
 * Autonomous job list with "System" and "Recurring" sub-sections.
 *
 * Renders two groups of AutonomousJobItem components, separated by
 * sub-section headers. Receives pre-sorted, pre-partitioned data
 * from the useJobZone hook.
 *
 * Exports:
 * - AutonomousJobList — Renders system and user-defined job items
 */

import type { RadarAutonomousJob } from '../../../../types';
import { AutonomousJobItem } from './AutonomousJobItem';

interface AutonomousJobListProps {
  systemJobs: RadarAutonomousJob[];
  userJobs: RadarAutonomousJob[];
  onJobClick: (jobId: string) => void;
}

export function AutonomousJobList({
  systemJobs,
  userJobs,
  onJobClick,
}: AutonomousJobListProps) {
  if (systemJobs.length === 0 && userJobs.length === 0) return null;

  return (
    <>
      {systemJobs.length > 0 && (
        <div className="radar-job-subsection">
          <h4 aria-label="System jobs">System</h4>
          <ul role="list">
            {systemJobs.map((job) => (
              <AutonomousJobItem
                key={job.id}
                job={job}
                onClick={() => onJobClick(job.id)}
              />
            ))}
          </ul>
        </div>
      )}
      {userJobs.length > 0 && (
        <div className="radar-job-subsection">
          <h4 aria-label="Recurring jobs">Recurring</h4>
          <ul role="list">
            {userJobs.map((job) => (
              <AutonomousJobItem
                key={job.id}
                job={job}
                onClick={() => onJobClick(job.id)}
              />
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
