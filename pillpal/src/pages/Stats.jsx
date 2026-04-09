import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { TrendingUp, Flame, Trophy, Calendar, CheckCircle, XCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../context/ThemeContext'
import { useMedications } from '../context/MedicationContext'
import WeeklyChart from '../components/WeeklyChart'
import AchievementBadge from '../components/AchievementBadge'
import MoodAvatar from '../components/MoodAvatar'

const ALL_ACHIEVEMENTS = ['first_pill', 'week_streak', 'month_streak', 'perfect_week', 'scanner']

export default function Stats() {
  const { t } = useTranslation()
  const theme = useTheme()
  const { achievements, getStats } = useMedications()
  const stats = useMemo(() => getStats(), [getStats])

  const statCards = [
    {
      icon: TrendingUp,
      label: t('stats.adherence'),
      value: `${stats.adherence}%`,
      color: '#10b981',
    },
    {
      icon: Flame,
      label: t('stats.current_streak'),
      value: `${stats.streak}`,
      sub: t('stats.days'),
      color: '#ff6b35',
    },
    {
      icon: Trophy,
      label: t('stats.best_streak'),
      value: `${stats.bestStreak}`,
      sub: t('stats.days'),
      color: '#eab308',
    },
    {
      icon: CheckCircle,
      label: t('stats.total_taken'),
      value: `${stats.totalTaken}`,
      color: '#22d3ee',
    },
    {
      icon: Calendar,
      label: t('stats.perfect_days'),
      value: `${stats.perfectDays}`,
      sub: t('stats.this_week'),
      color: '#a855f7',
    },
  ]

  return (
    <div className={`min-h-screen ${theme.bg} pb-24`}>
      <div className="max-w-lg mx-auto px-4 pt-12">
        {/* Header */}
        <motion.div
          className="flex items-center justify-between mb-6"
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <h1 className={`${theme.fontSizeXl} font-bold ${theme.text}`}>
            {t('stats.title')}
          </h1>
          <MoodAvatar adherence={stats.adherence} size="sm" />
        </motion.div>

        {/* Stat cards grid */}
        <div className="grid grid-cols-2 gap-3 mb-6">
          {statCards.map((card, i) => (
            <motion.div
              key={card.label}
              className={`rounded-2xl border p-4 ${theme.card} ${theme.border} ${
                i === 0 ? 'col-span-2' : ''
              }`}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.08 }}
            >
              <div className="flex items-center gap-2 mb-2">
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center"
                  style={{ backgroundColor: `${card.color}22` }}
                >
                  <card.icon size={16} color={card.color} />
                </div>
                <span className={`text-xs ${theme.textMuted}`}>{card.label}</span>
              </div>
              <div className="flex items-baseline gap-1">
                <motion.span
                  className={`${i === 0 ? 'text-4xl' : 'text-2xl'} font-bold ${theme.text}`}
                  initial={{ opacity: 0, scale: 0.5 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: i * 0.1 + 0.2, type: 'spring' }}
                >
                  {card.value}
                </motion.span>
                {card.sub && (
                  <span className={`text-sm ${theme.textMuted}`}>{card.sub}</span>
                )}
              </div>
            </motion.div>
          ))}
        </div>

        {/* Weekly chart */}
        <motion.div
          className={`rounded-2xl border p-4 mb-6 ${theme.card} ${theme.border}`}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <h3 className={`font-semibold mb-3 ${theme.text} ${theme.fontSize}`}>
            {t('stats.this_week')}
          </h3>
          <WeeklyChart data={stats.weekly} />
        </motion.div>

        {/* Achievements */}
        <motion.div
          className={`rounded-2xl border p-4 ${theme.card} ${theme.border}`}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
        >
          <div className="flex items-center gap-2 mb-4">
            <Trophy size={18} className={theme.accentText} />
            <h3 className={`font-semibold ${theme.text} ${theme.fontSize}`}>
              {t('achievements.first_pill').replace(t('achievements.first_pill'), 'Achievements')}
            </h3>
          </div>
          <div className="grid grid-cols-3 gap-4">
            {ALL_ACHIEVEMENTS.map(id => (
              <AchievementBadge
                key={id}
                achievementId={id}
                unlocked={achievements.includes(id)}
              />
            ))}
          </div>
        </motion.div>
      </div>
    </div>
  )
}
