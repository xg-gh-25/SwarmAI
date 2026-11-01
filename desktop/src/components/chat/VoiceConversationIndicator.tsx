/**
 * Voice conversation mode status indicator.
 *
 * Shows current state above the chat input: listening, processing,
 * thinking, or speaking with appropriate visual feedback.
 *
 * @module VoiceConversationIndicator
 */

import { useTranslation } from 'react-i18next';
import type { VoiceConversationState } from '../../hooks/useVoiceConversation';

interface VoiceConversationIndicatorProps {
  state: VoiceConversationState;
  onInterrupt?: () => void;
}

const STATE_CONFIG: Record<
  Exclude<VoiceConversationState, 'off'>,
  { labelKey: string; icon: string; color: string; animate: boolean }
> = {
  listening: {
    labelKey: 'ui.VoiceConversationIndicator.listening',
    icon: 'radio_button_checked',
    color: 'text-red-500',
    animate: true,
  },
  processing: {
    labelKey: 'ui.VoiceConversationIndicator.transcribing',
    icon: 'hourglass_top',
    color: 'text-yellow-500',
    animate: false,
  },
  thinking: {
    labelKey: 'ui.VoiceConversationIndicator.thinking',
    icon: 'psychology',
    color: 'text-blue-500',
    animate: true,
  },
  speaking: {
    labelKey: 'ui.VoiceConversationIndicator.speaking',
    icon: 'volume_up',
    color: 'text-green-500',
    animate: true,
  },
  interrupted: {
    labelKey: 'ui.VoiceConversationIndicator.interrupted',
    icon: 'mic',
    color: 'text-orange-500',
    animate: false,
  },
};

export function VoiceConversationIndicator({
  state,
  onInterrupt,
}: VoiceConversationIndicatorProps) {
  const { t } = useTranslation();
  if (state === 'off') return null;

  const config = STATE_CONFIG[state];
  if (!config) return null;

  const handleClick = () => {
    if (state === 'speaking' && onInterrupt) {
      onInterrupt();
    }
  };

  return (
    <div
      className={`flex items-center gap-1.5 px-2 py-1 text-xs ${config.color} ${
        state === 'speaking' ? 'cursor-pointer hover:opacity-80' : ''
      }`}
      onClick={handleClick}
      title={state === 'speaking' ? t('ui.VoiceConversationIndicator.clickToInterrupt') : undefined}
    >
      <span
        className={`material-symbols-outlined icon-xs ${
          config.animate ? 'animate-pulse' : ''
        }`}
      >
        {config.icon}
      </span>
      <span>{t(config.labelKey)}</span>
      {state === 'speaking' && (
        <span className="text-[11px] opacity-60 ml-1">{t('ui.VoiceConversationIndicator.clickToInterruptHint')}</span>
      )}
    </div>
  );
}
