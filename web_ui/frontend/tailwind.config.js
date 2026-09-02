/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Custom colors for pipeline stages
        'stage-find': '#3B82F6',      // Blue
        'stage-analyze': '#F59E0B',   // Amber
        'stage-download': '#10B981',  // Emerald
        'stage-complete': '#8B5CF6',  // Violet
        'stage-error': '#EF4444',     // Red
        'stage-pending': '#6B7280',   // Gray
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      }
    },
  },
  plugins: [],
}