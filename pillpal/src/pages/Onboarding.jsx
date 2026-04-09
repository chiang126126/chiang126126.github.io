import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ScanLine, MessageSquare, Trophy, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../context/ThemeContext'
import { useMedications } from '../context/MedicationContext'

const steps = [
  { icon: ScanLine, key: 'step1', color: '#22d3ee', emoji: '📸' },
  { icon: MessageSquare, key: 'step2', color: '#ff6b35', emoji: '🔥' },
  { icon: Trophy, key: 'step3', color: '#10b981', emoji: '🏆' },
]

export default function Onboarding() {
  const { t } = useTranslation()
  const theme = useTheme()
  const { completeOnboarding } = useMedications()
  const [step, setStep] = useState(-1) // -1 = welcome

  const handleNext = () => {
    if (step < steps.length - 1) {
      setStep(s => s + 1)
    } else {
      completeOnboarding()
    }
  }

  return (
    <div className={`min-h-screen ${theme.bg} flex flex-col items-center justify-center p-6`}>
      <AnimatePresence mode="wait">
        {step === -1 ? (
          <motion.div
            key="welcome"
            className="flex flex-col items-center text-center max-w-sm"
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -30 }}
          >
            {/* Logo */}
            <motion.div
              className="w-28 h-28 rounded-3xl flex items-center justify-center mb-8 shadow-2xl"
              style={{
                background: theme.isPro
                  ? 'linear-gradient(135deg, #22d3ee, #39ff14)'
                  : 'linear-gradient(135deg, #f97316, #f59e0b)',
              }}
              animate={{ rotate: [0, 5, -5, 0], scale: [1, 1.05, 1] }}
              transition={{ duration: 3, repeat: Infinity }}
            >
              <span className="text-5xl">💊</span>
            </motion.div>

            <h1 className={`text-3xl font-bold mb-3 ${theme.gradientText}`}>
              {t('onboarding.welcome')}
            </h1>
            <p className={`${theme.textMuted} text-lg mb-12`}>
              {t('onboarding.welcome_sub')}
            </p>

            <motion.button
              onClick={handleNext}
              className={`w-full py-4 rounded-2xl font-semibold text-lg ${theme.btnPrimary} shadow-lg`}
              whileTap={{ scale: 0.97 }}
            >
              {t('onboarding.get_started')}
            </motion.button>

            <button
              onClick={completeOnboarding}
              className={`mt-4 text-sm ${theme.textMuted}`}
            >
              {t('onboarding.skip')}
            </button>
          </motion.div>
        ) : (
          <motion.div
            key={step}
            className="flex flex-col items-center text-center max-w-sm"
            initial={{ opacity: 0, x: 100 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -100 }}
          >
            {/* Step illustration */}
            <motion.div
              className="w-40 h-40 rounded-full flex items-center justify-center mb-10"
              style={{
                background: `radial-gradient(circle, ${steps[step].color}22 0%, transparent 70%)`,
                border: `2px solid ${steps[step].color}33`,
              }}
              animate={{ scale: [1, 1.08, 1] }}
              transition={{ duration: 2, repeat: Infinity }}
            >
              <span className="text-7xl">{steps[step].emoji}</span>
            </motion.div>

            <h2 className={`text-2xl font-bold mb-3 ${theme.text}`}>
              {t(`onboarding.${steps[step].key}_title`)}
            </h2>
            <p className={`${theme.textMuted} text-base mb-12 leading-relaxed`}>
              {t(`onboarding.${steps[step].key}_desc`)}
            </p>

            {/* Step indicators */}
            <div className="flex gap-2 mb-8">
              {steps.map((_, i) => (
                <motion.div
                  key={i}
                  className={`h-1.5 rounded-full ${
                    i === step
                      ? `w-8 ${theme.accentBg}`
                      : i < step
                        ? `w-4 ${theme.accentBg} opacity-40`
                        : `w-4 ${theme.isPro ? 'bg-dark-border' : 'bg-warm-border'}`
                  }`}
                  layout
                />
              ))}
            </div>

            <motion.button
              onClick={handleNext}
              className={`w-full py-4 rounded-2xl font-semibold text-lg flex items-center justify-center gap-2 ${theme.btnPrimary} shadow-lg`}
              whileTap={{ scale: 0.97 }}
            >
              {step === steps.length - 1 ? t('onboarding.get_started') : t('onboarding.next')}
              <ChevronRight size={20} />
            </motion.button>

            <button
              onClick={completeOnboarding}
              className={`mt-4 text-sm ${theme.textMuted}`}
            >
              {t('onboarding.skip')}
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
