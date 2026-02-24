/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Space Grotesk', 'system-ui', 'sans-serif'],
      },
      colors: {
        primary: {
          DEFAULT: '#2b6cee',
          hover: '#1d5cd6',
          light: '#3d7ef0',
        },
        // Theme-aware colors using CSS variables
        dark: {
          bg: 'var(--color-bg)',
          card: 'var(--color-card)',
          hover: 'var(--color-hover)',
          border: 'var(--color-border)',
        },
        muted: 'var(--color-muted)',
        status: {
          online: '#22c55e',
          offline: '#6b7280',
          error: '#ef4444',
          warning: '#f59e0b',
          success: '#22c55e',
        },
      },
    },
  },
  plugins: [],
}
