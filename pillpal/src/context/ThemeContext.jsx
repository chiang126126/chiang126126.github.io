import { createContext, useContext, useState, useEffect } from 'react'

const ThemeContext = createContext()

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(() =>
    localStorage.getItem('pillpal-mode') || 'pro'
  )

  useEffect(() => {
    localStorage.setItem('pillpal-mode', mode)
    document.documentElement.className = `theme-${mode}`
  }, [mode])

  const toggleMode = () => setMode(m => (m === 'pro' ? 'care' : 'pro'))
  const isPro = mode === 'pro'
  const isCare = mode === 'care'

  // Dynamic classes based on mode
  const theme = {
    mode,
    isPro,
    isCare,
    toggleMode,
    setMode,
    // Background
    bg: isPro ? 'bg-dark-bg' : 'bg-warm-bg',
    // Cards
    card: isPro ? 'bg-dark-card border-dark-border' : 'bg-warm-card border-warm-border',
    cardHover: isPro ? 'hover:bg-dark-surface' : 'hover:bg-warm-surface',
    // Text
    text: isPro ? 'text-white' : 'text-warm-text',
    textMuted: isPro ? 'text-gray-400' : 'text-warm-muted',
    textAccent: isPro ? 'text-neon-cyan' : 'text-warm-orange',
    // Accent colors
    accent: isPro ? 'neon-cyan' : 'warm-orange',
    accentBg: isPro ? 'bg-neon-cyan' : 'bg-warm-orange',
    accentText: isPro ? 'text-neon-cyan' : 'text-warm-orange',
    accentBorder: isPro ? 'border-neon-cyan' : 'border-warm-orange',
    // Gradient text class
    gradientText: isPro ? 'gradient-text-pro' : 'gradient-text-care',
    // Glass
    glass: isPro ? 'glass-pro' : 'glass-care',
    // Borders
    border: isPro ? 'border-dark-border' : 'border-warm-border',
    // Surface
    surface: isPro ? 'bg-dark-surface' : 'bg-warm-surface',
    // Button
    btnPrimary: isPro
      ? 'bg-neon-cyan text-black font-semibold hover:bg-neon-green'
      : 'bg-warm-orange text-white font-semibold hover:bg-amber-500',
    btnSecondary: isPro
      ? 'bg-dark-surface text-white border border-dark-border hover:border-neon-cyan'
      : 'bg-warm-surface text-warm-text border border-warm-border hover:border-warm-orange',
    // Nav
    navBg: isPro ? 'bg-dark-card/95 border-dark-border' : 'bg-warm-card/95 border-warm-border',
    navActive: isPro ? 'text-neon-cyan' : 'text-warm-orange',
    navInactive: isPro ? 'text-gray-500' : 'text-warm-muted',
    // Input
    input: isPro
      ? 'bg-dark-surface border-dark-border text-white placeholder-gray-500 focus:border-neon-cyan'
      : 'bg-white border-warm-border text-warm-text placeholder-warm-muted focus:border-warm-orange',
    // Font size for care mode
    fontSize: isCare ? 'text-lg' : 'text-base',
    fontSizeLg: isCare ? 'text-xl' : 'text-lg',
    fontSizeXl: isCare ? 'text-3xl' : 'text-2xl',
  }

  return (
    <ThemeContext.Provider value={theme}>
      {children}
    </ThemeContext.Provider>
  )
}

export const useTheme = () => useContext(ThemeContext)
