import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { Plus, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useTheme } from '../context/ThemeContext'
import { useMedications } from '../context/MedicationContext'
import { getGreetingKey } from '../utils/helpers'
import BubblePop from '../components/BubblePop'
import StreakCounter from '../components/StreakCounter'
import MoodAvatar from '../components/MoodAvatar'
import ReminderCard from '../components/ReminderCard'
import WeeklyChart from '../components/WeeklyChart'

export default function Dashboard() {
  const { t } = useTranslation()
  const theme = useTheme()
  const navigate = useNavigate()
  const {
    medications, streak, logDose, isTakenToday, isSkippedToday,
    getTodaySchedule, getStats
  } = useMedications()

  const todayMeds = useMemo(() => getTodaySchedule(), [getTodaySchedule])
  const stats = useMemo(() => getStats(), [getStats])
  const remaining = todayMeds.filter(m => !isTakenToday(m.id) && !isSkippedToday(m.id)).length
  const adherence = todayMeds.length > 0
    ? Math.round(((todayMeds.length - remaining) / todayMeds.length) * 100)
    : 100

  return (
    <div className={`min-h-screen ${theme.bg} pb-24`}>
      <div className="max-w-lg mx-auto px-4 pt-12">
        {/* Header */}
        <motion.div
          className="flex items-center justify-between mb-6"
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div>
            <p className={`${theme.textMuted} ${theme.fontSize}`}>
              {t(getGreetingKey())} 👋
            </p>
            <h1 className={`${theme.fontSizeXl} font-bold ${theme.gradientText}`}>
              {t('app.name')}
            </h1>
          </div>
          <MoodAvatar adherence={adherence} size="sm" />
        </motion.div>

        {/* Streak + Status */}
        <motion.div
          className="flex items-center gap-3 mb-6"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <StreakCounter streak={streak} />
          <div className={`flex-1 px-4 py-2 rounded-2xl border ${theme.card} ${theme.border}`}>
            <p className={`text-xs ${theme.textMuted}`}>{t('dashboard.today_schedule')}</p>
            <p className={`font-semibold ${theme.text}`}>
              {remaining === 0
                ? t('dashboard.all_done') + ' ✨'
                : t('dashboard.remaining', { count: remaining })}
            </p>
          </div>
        </motion.div>

        {/* Reminder Card */}
        {remaining > 0 && (
          <motion.div
            className="mb-6"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
          >
            <ReminderCard />
          </motion.div>
        )}

        {/* Today's Medications */}
        {todayMeds.length > 0 ? (
          <motion.div
            className="mb-6"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
          >
            <div className="grid gap-3">
              {todayMeds.map((med, i) => {
                const taken = isTakenToday(med.id)
                const skipped = isSkippedToday(med.id)
                return (
                  <motion.div
                    key={med.id}
                    className={`flex items-center gap-4 p-4 rounded-2xl border ${theme.card} ${theme.border} ${
                      taken ? 'opacity-60' : ''
                    }`}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: taken ? 0.6 : 1, x: 0 }}
                    transition={{ delay: 0.1 * i }}
                  >
                    <BubblePop
                      medication={med}
                      isTaken={taken}
                      isSkipped={skipped}
                      onTake={() => logDose(med.id, 'taken')}
                      onSkip={() => logDose(med.id, 'skipped')}
                    />
                    <div className="flex-1 min-w-0">
                      <p className={`font-semibold truncate ${theme.text} ${theme.fontSize}`}>
                        {med.name}
                      </p>
                      <p className={`text-sm ${theme.textMuted}`}>
                        {med.dosage} · {t(`medications.${med.time}`)}
                      </p>
                      {med.food && (
                        <p className={`text-xs ${theme.textMuted} mt-0.5`}>
                          {t(`medications.${med.food}`)}
                        </p>
                      )}
                    </div>
                    <div className="text-right">
                      {taken && (
                        <motion.span
                          className="text-xs font-medium text-success bg-success/10 px-2 py-1 rounded-full"
                          initial={{ scale: 0 }}
                          animate={{ scale: 1 }}
                        >
                          {t('dashboard.taken')} ✓
                        </motion.span>
                      )}
                      {skipped && (
                        <span className={`text-xs ${theme.textMuted} bg-gray-500/10 px-2 py-1 rounded-full`}>
                          {t('dashboard.skipped')}
                        </span>
                      )}
                      {!taken && !skipped && (
                        <span className={`text-xs font-medium ${theme.accentText}`}>
                          {t('dashboard.take_now')}
                        </span>
                      )}
                    </div>
                  </motion.div>
                )
              })}
            </div>
          </motion.div>
        ) : (
          <motion.div
            className={`flex flex-col items-center justify-center py-16 rounded-3xl border border-dashed ${theme.border}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
          >
            <motion.div
              className="text-6xl mb-4"
              animate={{ y: [0, -8, 0] }}
              transition={{ duration: 2, repeat: Infinity }}
            >
              💊
            </motion.div>
            <p className={`${theme.text} font-semibold mb-1`}>{t('dashboard.no_meds')}</p>
            <p className={`text-sm ${theme.textMuted} mb-6 text-center px-8`}>{t('dashboard.add_first')}</p>
            <motion.button
              onClick={() => navigate('/scan')}
              className={`flex items-center gap-2 px-6 py-3 rounded-2xl ${theme.btnPrimary}`}
              whileTap={{ scale: 0.95 }}
            >
              <Plus size={18} />
              {t('medications.add_new')}
            </motion.button>
          </motion.div>
        )}

        {/* Weekly Progress */}
        {todayMeds.length > 0 && (
          <motion.div
            className={`rounded-2xl border p-4 ${theme.card} ${theme.border}`}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
          >
            <div className="flex items-center gap-2 mb-3">
              <Sparkles size={16} className={theme.accentText} />
              <h3 className={`font-semibold ${theme.text} ${theme.fontSize}`}>
                {t('dashboard.weekly_progress')}
              </h3>
            </div>
            <WeeklyChart data={stats.weekly} />
          </motion.div>
        )}
      </div>
    </div>
  )
}
