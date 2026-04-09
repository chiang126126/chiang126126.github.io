import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { MessageSquare, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../context/ThemeContext'
import { useMedications } from '../context/MedicationContext'
import { getRandomReminder, haptic } from '../utils/helpers'

export default function ReminderCard() {
  const { t } = useTranslation()
  const theme = useTheme()
  const { reminderStyle } = useMedications()
  const [message, setMessage] = useState('')
  const [key, setKey] = useState(0)

  useEffect(() => {
    setMessage(getRandomReminder(t, reminderStyle))
  }, [t, reminderStyle])

  const refresh = () => {
    haptic('light')
    setKey(k => k + 1)
    setMessage(getRandomReminder(t, reminderStyle))
  }

  if (!message) return null

  return (
    <motion.div
      className={`relative rounded-2xl border p-4 ${theme.card} ${theme.border} overflow-hidden`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
    >
      {/* Decorative corner */}
      <div
        className="absolute top-0 right-0 w-20 h-20 opacity-10 rounded-bl-full"
        style={{
          background: theme.isPro
            ? 'linear-gradient(135deg, #22d3ee, #39ff14)'
            : 'linear-gradient(135deg, #f97316, #f59e0b)',
        }}
      />

      <div className="flex items-start gap-3">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${
          theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'
        }`}>
          <MessageSquare size={18} className={theme.accentText} />
        </div>
        <div className="flex-1 min-w-0">
          <AnimatePresence mode="wait">
            <motion.p
              key={key}
              className={`${theme.fontSize} ${theme.text} font-medium leading-relaxed`}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
            >
              "{message}"
            </motion.p>
          </AnimatePresence>
        </div>
        <button
          onClick={refresh}
          className={`p-1.5 rounded-lg ${theme.surface} flex-shrink-0`}
        >
          <RefreshCw size={14} className={theme.textMuted} />
        </button>
      </div>
    </motion.div>
  )
}
