import { NavLink, useLocation } from 'react-router-dom'
import { Home, Pill, ScanLine, BarChart3, Settings } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../context/ThemeContext'
import { motion } from 'framer-motion'

const navItems = [
  { path: '/', icon: Home, label: 'nav.home' },
  { path: '/medications', icon: Pill, label: 'nav.meds' },
  { path: '/scan', icon: ScanLine, label: 'nav.scan', isCenter: true },
  { path: '/stats', icon: BarChart3, label: 'nav.stats' },
  { path: '/settings', icon: Settings, label: 'nav.settings' },
]

export default function BottomNav() {
  const { t } = useTranslation()
  const theme = useTheme()
  const location = useLocation()

  return (
    <nav className={`fixed bottom-0 left-0 right-0 ${theme.navBg} border-t backdrop-blur-xl z-50 pb-safe`}>
      <div className="flex items-center justify-around max-w-lg mx-auto px-2 h-16">
        {navItems.map(({ path, icon: Icon, label, isCenter }) => {
          const isActive = location.pathname === path
          return (
            <NavLink
              key={path}
              to={path}
              className="flex flex-col items-center justify-center flex-1 py-1 relative"
            >
              {isCenter ? (
                <motion.div
                  whileTap={{ scale: 0.9 }}
                  className={`w-14 h-14 rounded-2xl flex items-center justify-center -mt-6 shadow-lg ${
                    theme.isPro
                      ? 'bg-gradient-to-br from-neon-cyan to-neon-green text-black'
                      : 'bg-gradient-to-br from-warm-orange to-warm-amber text-white'
                  }`}
                >
                  <Icon size={26} strokeWidth={2.5} />
                </motion.div>
              ) : (
                <>
                  <motion.div
                    whileTap={{ scale: 0.85 }}
                    className="relative"
                  >
                    <Icon
                      size={theme.isCare ? 26 : 22}
                      className={`transition-colors ${
                        isActive ? theme.navActive : theme.navInactive
                      }`}
                    />
                    {isActive && (
                      <motion.div
                        layoutId="nav-dot"
                        className={`absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full ${theme.accentBg}`}
                      />
                    )}
                  </motion.div>
                  <span className={`text-[10px] mt-0.5 ${
                    isActive ? theme.navActive : theme.navInactive
                  } ${theme.isCare ? 'text-xs' : ''}`}>
                    {t(label)}
                  </span>
                </>
              )}
            </NavLink>
          )
        })}
      </div>
    </nav>
  )
}
