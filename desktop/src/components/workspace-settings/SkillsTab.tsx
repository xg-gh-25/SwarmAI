/**
 * Workspace skills configuration tab.
 *
 * Allows enabling/disabling skills for a specific workspace. Skills are
 * identified by folder name (filesystem-based) rather than database UUIDs.
 * Built-in and plugin skills are marked as readOnly.
 *
 * Key exports:
 * - ``SkillsTab`` — Workspace settings tab component
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { workspaceConfigService } from '../../services/workspaceConfig';
import { skillsService } from '../../services/skills';
import PrivilegedCapabilityModal from '../modals/PrivilegedCapabilityModal';
import type { WorkspaceSkillConfig } from '../../types/workspace-config';
import type { Skill } from '../../types';

interface SkillsTabProps {
  workspaceId: string;
}

export default function SkillsTab({ workspaceId }: SkillsTabProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [privilegedConfirm, setPrivilegedConfirm] = useState<{
    skillId: string;
    skillName: string;
  } | null>(null);
  const [disableWarning, setDisableWarning] = useState<string | null>(null);

  const { data: skillConfigs = [], isLoading: configsLoading } = useQuery({
    queryKey: ['workspaceSkills', workspaceId],
    queryFn: () => workspaceConfigService.getSkills(workspaceId),
  });

  const { data: allSkills = [] } = useQuery({
    queryKey: ['skills'],
    queryFn: () => skillsService.list(),
  });

  const updateMutation = useMutation({
    mutationFn: (configs: Partial<WorkspaceSkillConfig>[]) =>
      workspaceConfigService.updateSkills(workspaceId, configs),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaceSkills', workspaceId] });
      setDisableWarning(null);
    },
  });

  const getSkillName = (skillId: string): string => {
    const skill = allSkills.find((s: Skill) => s.folderName === skillId);
    return skill?.name ?? skillId;
  };

  const isPrivileged = (skillId: string): boolean => {
    const skill = allSkills.find((s: Skill) => s.folderName === skillId);
    // In the filesystem model, built-in and plugin skills are readOnly
    return skill?.readOnly ?? false;
  };

  const handleToggle = (config: WorkspaceSkillConfig) => {
    const newEnabled = !config.enabled;

    // If enabling a privileged skill, show confirmation
    if (newEnabled && isPrivileged(config.skillId)) {
      setPrivilegedConfirm({
        skillId: config.skillId,
        skillName: getSkillName(config.skillId),
      });
      return;
    }

    // If disabling, show warning
    if (!newEnabled) {
      setDisableWarning(config.skillId);
    }

    updateMutation.mutate([{ skillId: config.skillId, enabled: newEnabled }]);
  };

  const handlePrivilegedConfirm = () => {
    if (privilegedConfirm) {
      updateMutation.mutate([{ skillId: privilegedConfirm.skillId, enabled: true }]);
      setPrivilegedConfirm(null);
    }
  };

  if (configsLoading) {
    return (
      <div className="flex items-center justify-center py-8 text-[var(--color-text-muted)]">
        {t('common.loading')}
      </div>
    );
  }

  if (skillConfigs.length === 0) {
    return (
      <div className="text-center py-8 text-[var(--color-text-muted)]">
        {t('settings.skills.empty')}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-sm text-[var(--color-text-muted)] mb-3">
        {t('settings.skills.description')}
      </p>

      {skillConfigs.map((config) => {
        const skillName = getSkillName(config.skillId);
        const privileged = isPrivileged(config.skillId);
        const showWarning = disableWarning === config.skillId && !config.enabled;

        return (
          <div
            key={config.id}
            className={clsx(
              'flex items-center justify-between p-3 rounded-lg border',
              'border-[var(--color-border)] bg-[var(--color-card)]'
            )}
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
                psychology
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-[var(--color-text)] truncate">
                    {skillName}
                  </span>
                  {privileged && (
                    <span title={t('settings.skills.privileged')} className="text-amber-500">
                      ⚠️
                    </span>
                  )}
                </div>
                {showWarning && (
                  <p className="text-xs text-amber-500 mt-1">
                    {t('settings.skills.disableWarning')}
                  </p>
                )}
              </div>
            </div>

            {/* Toggle Switch */}
            <label className="relative inline-flex items-center cursor-pointer shrink-0">
              <input
                type="checkbox"
                checked={config.enabled}
                onChange={() => handleToggle(config)}
                className="sr-only peer"
                disabled={updateMutation.isPending}
              />
              <div
                className={clsx(
                  'w-9 h-5 rounded-full transition-colors',
                  'peer-focus:ring-2 peer-focus:ring-primary/30',
                  config.enabled
                    ? 'bg-primary'
                    : 'bg-[var(--color-border)]'
                )}
              >
                <div
                  className={clsx(
                    'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                    config.enabled && 'translate-x-4'
                  )}
                />
              </div>
            </label>
          </div>
        );
      })}

      {/* Privileged Capability Confirmation */}
      {privilegedConfirm && (
        <PrivilegedCapabilityModal
          isOpen={true}
          onClose={() => setPrivilegedConfirm(null)}
          onConfirm={handlePrivilegedConfirm}
          capabilityName={privilegedConfirm.skillName}
          capabilityType="skill"
        />
      )}
    </div>
  );
}
