import { motion } from 'framer-motion'
import { useTheme } from '../context/ThemeContext'

export default function WeeklyChart({ data = [] }) {
  const theme = useTheme()

  return (
    <div className="flex items-end justify-between gap-1.5 h-24 px-1">
      {data.map((day, i) => (
        <div key={day.date} className="flex flex-col items-center gap-1 flex-1">
          <div className="relative w-full flex items-end justify-center h-16">
            <motion.div
              className="w-full max-w-[28px] rounded-lg relative overflow-hidden"
              style={{
                backgroundColor: theme.isPro ? '#1e1e1e' : '#f0ebe0',
              }}
              initial={{ height: 0 }}
              animate={{ height: '100%' }}
            >
              <motion.div
                className="absolute bottom-0 left-0 right-0 rounded-lg"
                style={{
                  background: day.pct === 100
                    ? theme.isPro
                      ? 'linear-gradient(to top, #39ff14, #22d3ee)'
                      : 'linear-gradient(to top, #f97316, #f59e0b)'
                    : day.pct > 0
                      ? theme.isPro ? '#22d3ee66' : '#f9731666'
                      : 'transparent',
                }}
                initial={{ height: 0 }}
                animate={{ height: `${Math.max(day.pct, 0)}%` }}
                transition={{ delay: i * 0.1, duration: 0.5, ease: 'easeOut' }}
              />
            </motion.div>
          </div>
          <span className={`text-[10px] ${
            day.pct === 100 ? theme.accentText : theme.textMuted
          } ${theme.isCare ? 'text-xs font-medium' : ''}`}>
            {day.day}
          </span>
        </div>
      ))}
    </div>
  )
}
