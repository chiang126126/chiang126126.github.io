import { motion } from 'framer-motion'
import { useTheme } from '../context/ThemeContext'

// SVG mood faces based on adherence
const moods = {
  perfect: { emoji: '😎', label: 'Perfect!', color: '#10b981' },
  good: { emoji: '😊', label: 'Great', color: '#22d3ee' },
  ok: { emoji: '😐', label: 'OK', color: '#eab308' },
  bad: { emoji: '😤', label: 'Come on!', color: '#f97316' },
  terrible: { emoji: '🙄', label: 'Really?', color: '#ef4444' },
}

function getMoodFromAdherence(pct) {
  if (pct >= 100) return 'perfect'
  if (pct >= 80) return 'good'
  if (pct >= 50) return 'ok'
  if (pct >= 20) return 'bad'
  return 'terrible'
}

export default function MoodAvatar({ adherence = 100, size = 'md' }) {
  const theme = useTheme()
  const moodKey = getMoodFromAdherence(adherence)
  const mood = moods[moodKey]

  const sizeClasses = {
    sm: 'w-10 h-10 text-2xl',
    md: 'w-16 h-16 text-4xl',
    lg: 'w-24 h-24 text-6xl',
  }

  return (
    <motion.div
      className="flex flex-col items-center gap-1"
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: 'spring', stiffness: 300, damping: 20 }}
    >
      <motion.div
        className={`${sizeClasses[size]} rounded-full flex items-center justify-center`}
        style={{
          background: theme.isPro
            ? `radial-gradient(circle, ${mood.color}22 0%, transparent 70%)`
            : `radial-gradient(circle, ${mood.color}33 0%, transparent 70%)`,
          boxShadow: theme.isPro
            ? `0 0 20px ${mood.color}33`
            : 'none',
        }}
        animate={moodKey === 'terrible' ? { rotate: [0, -5, 5, -5, 0] } : {}}
        transition={{ repeat: Infinity, duration: 2, repeatDelay: 3 }}
      >
        <span role="img" aria-label={mood.label}>{mood.emoji}</span>
      </motion.div>
      <span className={`text-xs font-medium ${theme.textMuted}`}>
        {mood.label}
      </span>
    </motion.div>
  )
}
