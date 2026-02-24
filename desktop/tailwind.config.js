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
        dark: {
          bg: '#101622',
          card: '#1a1f2e',
          hover: '#252b3d',
          border: '#2d3548',
        },
        muted: '#9da6b9',
        status: {
          online: '#22c55e',
          offline: '#6b7280',
          error: '#ef4444',
        },
      },
    },
  },
  plugins: [],
}
