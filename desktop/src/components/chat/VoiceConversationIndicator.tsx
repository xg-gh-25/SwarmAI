/**
 * Voice conversation mode status indicator.
 *
 * Shows current state above the chat input: listening, processing,
 * thinking, or speaking with appropriate visual feedback.
 *
 * @module VoiceConversationIndicator
 */

import type { VoiceConversationState } from '../../hooks/useVoiceConversation';

interface VoiceConversationIndicatorProps {
  state: VoiceConversationState;
  onInterrupt?: () => void;
}

const STATE_CONFIG: Record<
  Exclude<VoiceConversationState, 'off'>,
  { label: string; icon: string; color: string; animate: boolean }
> = {
  listening: {
    label: 'Listening...',
    icon: 'radio_button_checked',
    color: 'text-red-500',
    animate: true,
  },
  processing: {
    label: 'Transcribing...',
    icon: 'hourglass_top',
    color: 'text-yellow-500',
    animate: false,
  },
  thinking: {
    label: 'Thinking...',
    icon: 'psychology',
    color: 'text-blue-500',
    animate: true,
  },
  speaking: {
    label: 'Speaking...',
    icon: 'volume_up',
    color: 'text-green-500',
    animate: true,
  },
  interrupted: {
    label: 'Interrupted',
    icon: 'mic',
    color: 'text-orange-500',
    animate: false,
  },
};

export function VoiceConversationIndicator({
  state,
  onInterrupt,
}: VoiceConversationIndicatorProps) {
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
      title={state === 'speaking' ? 'Click to interrupt' : undefined}
    >
      <span
        className={`material-symbols-outlined text-[14px] ${
          config.animate ? 'animate-pulse' : ''
        }`}
      >
        {config.icon}
      </span>
      <span>{config.label}</span>
      {state === 'speaking' && (
        <span className="text-[10px] opacity-60 ml-1">(click to interrupt)</span>
      )}
    </div>
  );
}
