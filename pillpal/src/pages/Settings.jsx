import { useState } from 'react'
import { motion } from 'framer-motion'
import {
  Sun, Moon, Globe, Bell, MessageSquare, Volume2, Vibrate,
  Download, Upload, Trash2, Info, Star, Shield, FileText,
  Crown, ChevronRight, Zap
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../context/ThemeContext'
import { useMedications } from '../context/MedicationContext'
import { haptic } from '../utils/helpers'

const languages = [
  { code: 'en', label: 'English', flag: '🇺🇸' },
  { code: 'zh', label: '中文', flag: '🇨🇳' },
  { code: 'fr', label: 'Français', flag: '🇫🇷' },
]

export default function Settings() {
  const { t, i18n } = useTranslation()
  const theme = useTheme()
  const { reminderStyle, setReminderStyle, clearAllData } = useMedications()
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [showLangPicker, setShowLangPicker] = useState(false)

  const changeLang = (code) => {
    i18n.changeLanguage(code)
    localStorage.setItem('pillpal-lang', code)
    setShowLangPicker(false)
    haptic('light')
  }

  const currentLang = languages.find(l => l.code === i18n.language) || languages[0]

  const Section = ({ title, children }) => (
    <div className="mb-6">
      <p className={`text-xs font-medium uppercase tracking-wider mb-2 px-1 ${theme.textMuted}`}>
        {title}
      </p>
      <div className={`rounded-2xl border overflow-hidden ${theme.card} ${theme.border}`}>
        {children}
      </div>
    </div>
  )

  const Row = ({ icon: Icon, label, value, onClick, danger, accent }) => (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-4 py-3.5 border-b last:border-b-0 ${theme.border} text-left`}
    >
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${
        danger ? 'bg-danger/10' : accent ? `${theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'}` : theme.surface
      }`}>
        <Icon size={17} className={danger ? 'text-danger' : accent ? theme.accentText : theme.textMuted} />
      </div>
      <span className={`flex-1 ${theme.fontSize} ${danger ? 'text-danger' : theme.text}`}>
        {label}
      </span>
      {value && (
        <span className={`text-sm ${theme.textMuted}`}>{value}</span>
      )}
      <ChevronRight size={16} className={theme.textMuted} />
    </button>
  )

  return (
    <div className={`min-h-screen ${theme.bg} pb-24`}>
      <div className="max-w-lg mx-auto px-4 pt-12">
        <motion.h1
          className={`${theme.fontSizeXl} font-bold ${theme.text} mb-6`}
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          {t('settings.title')}
        </motion.h1>

        {/* Premium Banner */}
        <motion.div
          className={`rounded-2xl p-5 mb-6 relative overflow-hidden`}
          style={{
            background: theme.isPro
              ? 'linear-gradient(135deg, #22d3ee15, #a855f715)'
              : 'linear-gradient(135deg, #f9731615, #f59e0b15)',
            border: `1px solid ${theme.isPro ? '#22d3ee33' : '#f9731633'}`,
          }}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className="flex items-center gap-3">
            <div className={`w-12 h-12 rounded-2xl flex items-center justify-center ${
              theme.isPro ? 'bg-neon-purple/20' : 'bg-warm-amber/20'
            }`}>
              <Crown size={24} className={theme.isPro ? 'text-neon-purple' : 'text-warm-amber'} />
            </div>
            <div className="flex-1">
              <p className={`font-bold ${theme.text} ${theme.fontSize}`}>{t('settings.upgrade')}</p>
              <p className={`text-xs ${theme.textMuted}`}>{t('settings.premium_desc')}</p>
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <span className={`text-xs px-2 py-1 rounded-full ${theme.surface} ${theme.text}`}>
              {t('settings.premium_price_monthly')}
            </span>
            <span className={`text-xs px-2 py-1 rounded-full font-medium ${
              theme.isPro ? 'bg-neon-cyan/10 text-neon-cyan' : 'bg-warm-orange/10 text-warm-orange'
            }`}>
              {t('settings.premium_price_yearly')} ✨
            </span>
          </div>
        </motion.div>

        {/* Appearance */}
        <Section title={t('settings.appearance')}>
          <div className="p-4 flex gap-3">
            {/* Pro Mode toggle */}
            <motion.button
              whileTap={{ scale: 0.97 }}
              onClick={() => { theme.setMode('pro'); haptic('medium') }}
              className={`flex-1 rounded-xl p-3 border-2 transition-all ${
                theme.isPro
                  ? 'border-neon-cyan bg-dark-bg'
                  : 'border-transparent bg-gray-100'
              }`}
            >
              <div className="flex items-center gap-2 mb-1.5">
                <Moon size={16} className={theme.isPro ? 'text-neon-cyan' : 'text-gray-400'} />
                <span className={`text-sm font-semibold ${theme.isPro ? 'text-white' : 'text-gray-600'}`}>
                  {t('settings.mode_pro')}
                </span>
              </div>
              <p className={`text-[10px] ${theme.isPro ? 'text-gray-400' : 'text-gray-400'}`}>
                {t('settings.mode_pro_desc')}
              </p>
              <div className="flex gap-1 mt-2">
                {['#22d3ee', '#39ff14', '#ff6b35'].map(c => (
                  <div key={c} className="w-4 h-4 rounded-full" style={{ backgroundColor: c }} />
                ))}
              </div>
            </motion.button>

            {/* Care Mode toggle */}
            <motion.button
              whileTap={{ scale: 0.97 }}
              onClick={() => { theme.setMode('care'); haptic('medium') }}
              className={`flex-1 rounded-xl p-3 border-2 transition-all ${
                theme.isCare
                  ? 'border-warm-orange bg-warm-bg'
                  : 'border-transparent bg-gray-100'
              }`}
            >
              <div className="flex items-center gap-2 mb-1.5">
                <Sun size={16} className={theme.isCare ? 'text-warm-orange' : 'text-gray-400'} />
                <span className={`text-sm font-semibold ${theme.isCare ? 'text-warm-text' : 'text-gray-600'}`}>
                  {t('settings.mode_care')}
                </span>
              </div>
              <p className={`text-[10px] text-gray-400`}>
                {t('settings.mode_care_desc')}
              </p>
              <div className="flex gap-1 mt-2">
                {['#f97316', '#f59e0b', '#fbbf24'].map(c => (
                  <div key={c} className="w-4 h-4 rounded-full" style={{ backgroundColor: c }} />
                ))}
              </div>
            </motion.button>
          </div>
        </Section>

        {/* Language */}
        <Section title={t('settings.language')}>
          {showLangPicker ? (
            <div className="p-2">
              {languages.map(lang => (
                <button
                  key={lang.code}
                  onClick={() => changeLang(lang.code)}
                  className={`w-full flex items-center gap-3 px-3 py-3 rounded-xl transition-colors ${
                    i18n.language === lang.code
                      ? theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'
                      : ''
                  }`}
                >
                  <span className="text-xl">{lang.flag}</span>
                  <span className={`font-medium ${theme.text} ${theme.fontSize}`}>{lang.label}</span>
                  {i18n.language === lang.code && (
                    <Zap size={14} className={theme.accentText} />
                  )}
                </button>
              ))}
            </div>
          ) : (
            <Row
              icon={Globe}
              label={t('settings.language')}
              value={`${currentLang.flag} ${currentLang.label}`}
              onClick={() => setShowLangPicker(true)}
            />
          )}
        </Section>

        {/* Notifications */}
        <Section title={t('settings.notifications')}>
          <div className="p-4">
            <p className={`text-sm font-medium ${theme.text} mb-3`}>{t('settings.reminder_style')}</p>
            <div className="grid grid-cols-3 gap-2">
              {['sassy', 'gentle', 'neutral'].map(style => (
                <button
                  key={style}
                  onClick={() => { setReminderStyle(style); haptic('light') }}
                  className={`px-3 py-2.5 rounded-xl text-xs font-medium border transition-all ${
                    reminderStyle === style
                      ? `${theme.accentBorder} ${theme.accentText} ${theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'}`
                      : `${theme.border} ${theme.textMuted}`
                  }`}
                >
                  {style === 'sassy' ? '🔥 ' : style === 'gentle' ? '🌸 ' : '📋 '}
                  {t(`settings.style_${style}`)}
                </button>
              ))}
            </div>
          </div>
        </Section>

        {/* Data */}
        <Section title={t('settings.data')}>
          <Row icon={Download} label={t('settings.export_data')} onClick={() => {
            const dataStr = JSON.stringify(localStorage.getItem('pillpal-data'), null, 2)
            const blob = new Blob([dataStr], { type: 'application/json' })
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = 'pillpal-backup.json'
            a.click()
            URL.revokeObjectURL(url)
          }} />
          <Row
            icon={Trash2}
            label={showClearConfirm ? t('settings.clear_confirm') : t('settings.clear_data')}
            danger
            onClick={() => {
              if (showClearConfirm) {
                clearAllData()
                setShowClearConfirm(false)
                haptic('heavy')
              } else {
                setShowClearConfirm(true)
                haptic('light')
                setTimeout(() => setShowClearConfirm(false), 3000)
              }
            }}
          />
        </Section>

        {/* About */}
        <Section title={t('settings.about')}>
          <Row icon={Info} label={t('settings.version')} value="1.0.0 MVP" onClick={() => {}} />
          <Row icon={Shield} label={t('settings.privacy')} onClick={() => {}} />
          <Row icon={FileText} label={t('settings.terms')} onClick={() => {}} />
        </Section>

        <p className={`text-center text-xs ${theme.textMuted} mt-4 mb-8`}>
          Made with 💊 by PillPal Team
        </p>
      </div>
    </div>
  )
}
