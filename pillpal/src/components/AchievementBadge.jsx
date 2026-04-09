import { motion } from 'framer-motion'
import { Trophy, Star, Flame, Crown, ScanLine } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../context/ThemeContext'

const achievementIcons = {
  first_pill: Star,
  week_streak: Flame,
  month_streak: Crown,
  perfect_week: Trophy,
  scanner: ScanLine,
}

const achievementColors = {
  first_pill: '#3b82f6',
  week_streak: '#f97316',
  month_streak: '#eab308',
  perfect_week: '#10b981',
  scanner: '#a855f7',
}

export default function AchievementBadge({ achievementId, unlocked = false, showLabel = true }) {
  const { t } = useTranslation()
  const theme = useTheme()
  const Icon = achievementIcons[achievementId] || Star
  const color = achievementColors[achievementId] || '#22d3ee'

  return (
    <motion.div
      className="flex flex-col items-center gap-1.5"
      initial={unlocked ? { scale: 0, rotate: -180 } : {}}
      animate={unlocked ? { scale: 1, rotate: 0 } : {}}
      transition={{ type: 'spring', stiffness: 200, damping: 15 }}
    >
      <div
        className={`w-14 h-14 rounded-2xl flex items-center justify-center relative ${
          unlocked ? '' : 'opacity-30 grayscale'
        }`}
        style={{
          background: unlocked
            ? `linear-gradient(135deg, ${color}33, ${color}11)`
            : theme.isPro ? '#1a1a1a' : '#f0ebe0',
          border: `2px solid ${unlocked ? color : 'transparent'}`,
          boxShadow: unlocked && theme.isPro ? `0 0 20px ${color}22` : 'none',
        }}
      >
        <Icon size={24} color={unlocked ? color : '#666'} />
        {unlocked && (
          <motion.div
            className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-success flex items-center justify-center"
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ delay: 0.3 }}
          >
            <span className="text-white text-[10px]">✓</span>
          </motion.div>
        )}
      </div>
      {showLabel && (
        <div className="text-center">
          <p className={`text-xs font-medium ${theme.text}`}>
            {t(`achievements.${achievementId}`)}
          </p>
          <p className={`text-[10px] ${theme.textMuted}`}>
            {t(`achievements.${achievementId}_desc`)}
          </p>
        </div>
      )}
    </motion.div>
  )
}
