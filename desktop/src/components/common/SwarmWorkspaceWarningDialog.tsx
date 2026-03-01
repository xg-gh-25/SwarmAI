import { useTranslation } from 'react-i18next';
import Modal from './Modal';
import Button from './Button';

interface SwarmWorkspaceWarningDialogProps {
  isOpen: boolean;
  action: 'edit' | 'delete';
  fileName?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Warning dialog displayed when users attempt to modify files in the Swarm Workspace.
 * Requires explicit confirmation before allowing modifications to the protected system workspace.
 * 
 * Validates: Requirements 4.3, 4.5
 */
export default function SwarmWorkspaceWarningDialog({
  isOpen,
  action,
  fileName,
  onConfirm,
  onCancel,
}: SwarmWorkspaceWarningDialogProps) {
  const { t } = useTranslation();

  const isDelete = action === 'delete';
  const title = isDelete
    ? t('swarmWorkspace.warning.deleteTitle', 'Cannot Delete System Workspace')
    : t('swarmWorkspace.warning.editTitle', 'System Workspace Warning');

  const message = isDelete
    ? t(
        'swarmWorkspace.warning.deleteMessage',
        'The Swarm Workspace is a protected system workspace and cannot be deleted. It is required for SwarmAI\'s internal operations.'
      )
    : t(
        'swarmWorkspace.warning.editMessage',
        'You are about to modify a file in the Swarm Workspace. This is a protected system workspace used by SwarmAI for internal operations. Modifying these files may affect SwarmAI\'s functionality.'
      );

  return (
    <Modal isOpen={isOpen} onClose={onCancel} title={title} size="sm">
      <div className="text-center">
        <div
          className="w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4"
          style={{ backgroundColor: 'var(--color-status-warning-bg, rgba(234, 179, 8, 0.1))' }}
        >
          <span
            className="material-symbols-outlined text-3xl"
            style={{ color: 'var(--color-status-warning)' }}
          >
            warning
          </span>
        </div>

        {fileName && (
          <div
            className="text-sm mb-3 px-3 py-2 rounded-md font-mono truncate"
            style={{
              backgroundColor: 'var(--color-bg-tertiary)',
              color: 'var(--color-text-secondary)',
            }}
          >
            {fileName}
          </div>
        )}

        <div className="mb-6" style={{ color: 'var(--color-text-muted)' }}>
          {message}
        </div>

        {isDelete ? (
          <div className="flex justify-center">
            <Button variant="secondary" onClick={onCancel}>
              {t('common.button.ok', 'OK')}
            </Button>
          </div>
        ) : (
          <div className="flex gap-3">
            <Button variant="secondary" className="flex-1" onClick={onCancel}>
              {t('common.button.cancel', 'Cancel')}
            </Button>
            <Button variant="primary" className="flex-1" onClick={onConfirm}>
              {t('swarmWorkspace.warning.continueAnyway', 'Continue Anyway')}
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
