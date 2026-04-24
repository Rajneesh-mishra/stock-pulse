/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Modern Fintech Dark — Linear × Robinhood × Vercel
      colors: {
        ink: {
          950: '#06080A',
          900: '#0A0B0D',
          850: '#0E1014',
          800: '#111317',
          750: '#16181C',
          700: '#1B1E23',
          600: '#25292F',
          500: '#33383F',
        },
        line: {
          DEFAULT: '#1F2227',
          strong: '#2A2D33',
          soft:   '#181A1E',
        },
        fg: {
          DEFAULT: '#EDEEF0',
          muted: '#9BA1A8',
          subtle: '#6B7078',
          faint: '#4A5058',
        },
        bull: {
          DEFAULT: '#22D7A0',
          dim:     'rgba(34,215,160,0.12)',
          glow:    'rgba(34,215,160,0.35)',
          50:      '#E6FBF5',
        },
        bear: {
          DEFAULT: '#F43F5E',
          dim:     'rgba(244,63,94,0.12)',
          glow:    'rgba(244,63,94,0.35)',
        },
        amber: {
          DEFAULT: '#FFB547',
          dim:     'rgba(255,181,71,0.12)',
        },
        sky: {
          DEFAULT: '#7DD3FC',
          dim:     'rgba(125,211,252,0.12)',
        },
        violet: {
          DEFAULT: '#A78BFA',
          dim:     'rgba(167,139,250,0.12)',
        },
      },
      fontFamily: {
        sans:    ['Geist', 'Manrope', 'system-ui', 'sans-serif'],
        display: ['Geist', 'Manrope', 'system-ui', 'sans-serif'],
        mono:    ['"Geist Mono"', '"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        '2xs': ['10px',  { lineHeight: '1.4' }],
      },
      borderRadius: {
        xl2: '14px',
      },
      boxShadow: {
        card: '0 1px 0 0 rgba(255,255,255,0.03) inset, 0 0 0 1px rgba(255,255,255,0.04)',
        lift: '0 10px 30px -12px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.05)',
        glow: '0 0 0 1px rgba(34,215,160,0.4), 0 0 20px rgba(34,215,160,0.15)',
      },
      animation: {
        'pulse-dot':         'pulseDot 2.2s cubic-bezier(0, 0, 0.2, 1) infinite',
        'flash-up':          'flashUp 700ms ease-out',
        'flash-dn':          'flashDn 700ms ease-out',
        'event-in':          'eventIn 450ms cubic-bezier(.2,.7,.2,1)',
        'skeleton':          'skeleton 1.6s ease-in-out infinite',
        'topbar-progress':   'topbarProgress 1.4s cubic-bezier(.4,0,.2,1) infinite',
      },
      keyframes: {
        pulseDot: {
          '0%,100%': { boxShadow: '0 0 0 0 rgba(34,215,160,0.45)' },
          '70%':     { boxShadow: '0 0 0 7px rgba(34,215,160,0)' },
        },
        flashUp: {
          '0%':   { backgroundColor: 'rgba(34,215,160,0.25)', color: '#22D7A0' },
          '100%': { backgroundColor: 'transparent', color: '#EDEEF0' },
        },
        flashDn: {
          '0%':   { backgroundColor: 'rgba(244,63,94,0.25)', color: '#F43F5E' },
          '100%': { backgroundColor: 'transparent', color: '#EDEEF0' },
        },
        eventIn: {
          '0%':   { opacity: '0', transform: 'translateY(-4px)', backgroundColor: 'rgba(255,181,71,0.06)' },
          '100%': { opacity: '1', transform: 'translateY(0)',  backgroundColor: 'transparent' },
        },
        skeleton: {
          '0%':   { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(200%)' },
        },
        topbarProgress: {
          '0%':   { transform: 'translateX(-100%)' },
          '60%':  { transform: 'translateX(180%)' },
          '100%': { transform: 'translateX(180%)' },
        },
      },
    },
  },
  plugins: [],
};
