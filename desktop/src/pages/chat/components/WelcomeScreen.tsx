/**
 * WelcomeScreen — branded landing view for new chat tabs.
 *
 * Displays a centered, presentational welcome screen with:
 * - Circular SwarmAI brand icon (`<img>` element, NOT the 🐝 emoji)
 *   with a radial gradient glow effect behind it
 * - "Welcome to SwarmAI!" heading with cyan-to-purple gradient text
 * - "Your AI Team, 24/7" slogan
 * - "Work smarter, move faster, and enjoy the journey." tagline
 *
 * This component renders as a sibling to the message list — it is NOT
 * a message bubble. It appears when `messages.length === 0` for the
 * active tab and disappears when the first message is sent.
 *
 * Per Design Decision #5, the welcome screen uses the polished brand
 * icon image while assistant messages use the 🐝 emoji.
 *
 * @exports WelcomeScreen — The welcome screen React component
 *
 * Validates: Requirements 5.1, 5.2, 5.3, 9.1
 */

import React from 'react';

export const WelcomeScreen: React.FC = () => {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center select-none">
      {/* Icon with gradient glow */}
      <div className="relative mb-6">
        <div
          className="absolute top-1/2 left-1/2 w-[120px] h-[120px] -translate-x-1/2 -translate-y-1/2 rounded-full pointer-events-none"
          style={{
            background:
              'radial-gradient(circle, rgba(0, 212, 255, 0.2) 0%, transparent 70%)',
          }}
          aria-hidden="true"
        />
        <img
          src="/swarmai-icon-3.png"
          alt="SwarmAI icon"
          className="relative w-16 h-16 rounded-full"
          draggable={false}
        />
      </div>

      {/* Heading with gradient text */}
      <h1
        className="text-3xl font-bold mb-3"
        style={{
          background: 'linear-gradient(135deg, #00d4ff 0%, #a855f7 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
        }}
      >
        Welcome to SwarmAI!
      </h1>

      {/* Slogan */}
      <p className="text-lg text-[var(--color-text)] mb-2">
        Your AI Team, 24/7
      </p>

      {/* Tagline */}
      <p className="text-sm text-[var(--color-text-secondary)]">
        Work smarter, move faster, and enjoy the journey.
      </p>
    </div>
  );
};

export default WelcomeScreen;
