/**
 * EvalModal Component
 *
 * Wraps the EvalDashboard content in a modal overlay (same pattern as SettingsModal).
 * Opens from Left Sidebar "OS Eval" icon.
 */

import { useQuery } from '@tanstack/react-query';
import Modal from '../common/Modal';
import EvalDashboard from '../../pages/EvalDashboard';
import api from '../../services/api';

interface EvalModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface EvalHealth {
  overall_score: number | null;
  trend: { delta: number; direction: string } | null;
}

export default function EvalModal({ isOpen, onClose }: EvalModalProps) {
  // Fetch health for title badge (lightweight, cached)
  const { data: health } = useQuery<EvalHealth>({
    queryKey: ['eval-health'],
    queryFn: async () => (await api.get<EvalHealth>('/eval/health')).data,
    staleTime: 60_000,
    enabled: isOpen,
  });

  const score = health?.overall_score;
  const title = score != null ? `OS Eval — ${score}%` : 'OS Eval';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      size="fullscreen"
      mode="EVAL"
    >
      <EvalDashboard />
    </Modal>
  );
}
