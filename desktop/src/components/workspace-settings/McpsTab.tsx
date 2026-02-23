import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { workspaceConfigService } from '../../services/workspaceConfig';
import { mcpService } from '../../services/mcp';
import PrivilegedCapabilityModal from '../modals/PrivilegedCapabilityModal';
import type { WorkspaceMcpConfig } from '../../types/workspace-config';
import type { MCPServer } from '../../types';

interface McpsTabProps {
  workspaceId: string;
}

export default function McpsTab({ workspaceId }: McpsTabProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [privilegedConfirm, setPrivilegedConfirm] = useState<{
    mcpId: string;
    mcpName: string;
  } | null>(null);
  const [disableWarning, setDisableWarning] = useState<string | null>(null);

  const { data: mcpConfigs = [], isLoading: configsLoading } = useQuery({
    queryKey: ['workspaceMcps', workspaceId],
    queryFn: () => workspaceConfigService.getMcps(workspaceId),
  });

  const { data: allMcps = [] } = useQuery({
    queryKey: ['mcpServers'],
    queryFn: () => mcpService.list(),
  });

  const updateMutation = useMutation({
    mutationFn: (configs: Partial<WorkspaceMcpConfig>[]) =>
      workspaceConfigService.updateMcps(workspaceId, configs),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaceMcps', workspaceId] });
      setDisableWarning(null);
    },
  });

  const getMcpName = (mcpServerId: string): string => {
    const mcp = allMcps.find((m: MCPServer) => m.id === mcpServerId);
    return mcp?.name ?? mcpServerId;
  };

  const isPrivileged = (mcpServerId: string): boolean => {
    const mcp = allMcps.find((m: MCPServer) => m.id === mcpServerId);
    return Boolean((mcp as unknown as Record<string, unknown>)?.isPrivileged);
  };

  const handleToggle = (config: WorkspaceMcpConfig) => {
    const newEnabled = !config.enabled;

    if (newEnabled && isPrivileged(config.mcpServerId)) {
      setPrivilegedConfirm({
        mcpId: config.mcpServerId,
        mcpName: getMcpName(config.mcpServerId),
      });
      return;
    }

    if (!newEnabled) {
      setDisableWarning(config.mcpServerId);
    }

    updateMutation.mutate([{ mcpServerId: config.mcpServerId, enabled: newEnabled }]);
  };

  const handlePrivilegedConfirm = () => {
    if (privilegedConfirm) {
      updateMutation.mutate([{ mcpServerId: privilegedConfirm.mcpId, enabled: true }]);
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

  if (mcpConfigs.length === 0) {
    return (
      <div className="text-center py-8 text-[var(--color-text-muted)]">
        {t('settings.mcps.empty')}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-sm text-[var(--color-text-muted)] mb-3">
        {t('settings.mcps.description')}
      </p>

      {mcpConfigs.map((config) => {
        const mcpName = getMcpName(config.mcpServerId);
        const privileged = isPrivileged(config.mcpServerId);
        const showWarning = disableWarning === config.mcpServerId && !config.enabled;

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
                hub
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-[var(--color-text)] truncate">
                    {mcpName}
                  </span>
                  {privileged && (
                    <span title={t('settings.mcps.privileged')} className="text-amber-500">
                      ⚠️
                    </span>
                  )}
                </div>
                {showWarning && (
                  <p className="text-xs text-amber-500 mt-1">
                    {t('settings.mcps.disableWarning')}
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
          capabilityName={privilegedConfirm.mcpName}
          capabilityType="mcp"
        />
      )}
    </div>
  );
}
