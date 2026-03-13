import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import Modal from '../common/Modal';
import SkillsTab from '../workspace-settings/SkillsTab';
import MCPSettingsPanel from '../workspace-settings/MCPSettingsPanel';
import KnowledgebasesTab from '../workspace-settings/KnowledgebasesTab';

type SettingsTab = 'skills' | 'mcps' | 'knowledgebases';

interface WorkspaceSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  workspaceId: string;
}

const TABS: { key: SettingsTab; icon: string }[] = [
  { key: 'skills', icon: 'psychology' },
  { key: 'mcps', icon: 'hub' },
  { key: 'knowledgebases', icon: 'menu_book' },
];

export default function WorkspaceSettingsModal({
  isOpen,
  onClose,
  workspaceId,
}: WorkspaceSettingsModalProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<SettingsTab>('skills');

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('settings.title')}
      size="lg"
    >
      {/* Tab Navigation */}
      <div className="flex gap-1 mb-4 border-b border-[var(--color-border)]">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={clsx(
              'flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg transition-colors -mb-px',
              activeTab === tab.key
                ? 'text-[var(--color-text)] border-b-2 border-primary bg-primary/5'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
            )}
          >
            <span className="material-symbols-outlined text-lg">{tab.icon}</span>
            {t(`settings.tabs.${tab.key}`)}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="min-h-[300px]">
        {activeTab === 'skills' && <SkillsTab workspaceId={workspaceId} />}
        {activeTab === 'mcps' && <MCPSettingsPanel />}
        {activeTab === 'knowledgebases' && <KnowledgebasesTab workspaceId={workspaceId} />}
      </div>
    </Modal>
  );
}
