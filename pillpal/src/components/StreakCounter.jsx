import { motion } from 'framer-motion'
import { Flame } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../context/ThemeContext'

export default function StreakCounter({ streak = 0 }) {
  const { t } = useTranslation()
  const theme = useTheme()

  const isOnFire = streak >= 7

  return (
    <motion.div
      className={`flex items-center gap-2 px-4 py-2 rounded-2xl border ${theme.card} ${theme.border}`}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <motion.div
        animate={isOnFire ? {
          scale: [1, 1.2, 1],
          rotate: [0, -10, 10, 0],
        } : {}}
        transition={{ duration: 0.6, repeat: Infinity, repeatDelay: 2 }}
      >
        <Flame
          size={theme.isCare ? 28 : 22}
          className={isOnFire ? 'text-neon-orange' : theme.textMuted}
          fill={isOnFire ? '#ff6b35' : 'none'}
        />
      </motion.div>
      <div>
        <motion.span
          key={streak}
          className={`text-xl font-bold ${theme.isPro ? 'text-neon-orange' : 'text-warm-orange'}`}
          initial={{ scale: 1.5, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
        >
          {streak}
        </motion.span>
        <span className={`text-xs ml-1 ${theme.textMuted}`}>
          {t('dashboard.streak')}
        </span>
      </div>
    </motion.div>
  )
}
