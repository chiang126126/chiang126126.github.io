import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Camera, ScanLine, Check, RotateCcw, Keyboard, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useTheme } from '../context/ThemeContext'
import { useMedications } from '../context/MedicationContext'
import { PILL_COLORS, PILL_ICONS, DEMO_SCAN_RESULTS, haptic } from '../utils/helpers'
import PillIcon from '../components/PillIcon'

export default function ScanAdd() {
  const { t } = useTranslation()
  const theme = useTheme()
  const navigate = useNavigate()
  const { addMedication } = useMedications()

  const [mode, setMode] = useState('choose') // 'choose' | 'scan' | 'manual' | 'confirm'
  const [scanning, setScanning] = useState(false)
  const [scanResult, setScanResult] = useState(null)
  const [form, setForm] = useState({
    name: '',
    dosage: '',
    frequency: 'daily',
    time: 'morning',
    food: 'with_food',
    notes: '',
    color: PILL_COLORS[0],
    icon: 'pill',
  })
  const [saved, setSaved] = useState(false)

  const handleScan = () => {
    setMode('scan')
    setScanning(true)
    haptic('medium')

    // Simulate AI scan with random result
    setTimeout(() => {
      const result = DEMO_SCAN_RESULTS[Math.floor(Math.random() * DEMO_SCAN_RESULTS.length)]
      const color = PILL_COLORS[Math.floor(Math.random() * PILL_COLORS.length)]
      setScanResult(result)
      setForm(prev => ({ ...prev, ...result, color }))
      setScanning(false)
      setMode('confirm')
      haptic('success')
    }, 2500)
  }

  const handleSave = () => {
    if (!form.name.trim()) return
    haptic('success')
    addMedication(form)
    setSaved(true)
    setTimeout(() => {
      navigate('/')
    }, 1200)
  }

  const updateForm = (key, value) => setForm(prev => ({ ...prev, [key]: value }))

  // Choose mode screen
  if (mode === 'choose') {
    return (
      <div className={`min-h-screen ${theme.bg} pb-24`}>
        <div className="max-w-lg mx-auto px-4 pt-12">
          <motion.h1
            className={`${theme.fontSizeXl} font-bold ${theme.text} mb-8`}
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
          >
            {t('add.title')}
          </motion.h1>

          {/* Scan option */}
          <motion.button
            onClick={handleScan}
            className={`w-full rounded-3xl border-2 border-dashed p-8 mb-4 flex flex-col items-center gap-4 ${
              theme.isPro ? 'border-neon-cyan/30 hover:border-neon-cyan/60' : 'border-warm-orange/30 hover:border-warm-orange/60'
            }`}
            whileTap={{ scale: 0.98 }}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <motion.div
              className={`w-20 h-20 rounded-2xl flex items-center justify-center ${
                theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'
              }`}
              animate={{ scale: [1, 1.05, 1] }}
              transition={{ duration: 2, repeat: Infinity }}
            >
              <Camera size={36} className={theme.accentText} />
            </motion.div>
            <div className="text-center">
              <p className={`font-semibold ${theme.text} ${theme.fontSizeLg}`}>
                {t('add.scan_button')}
              </p>
              <p className={`text-sm ${theme.textMuted} mt-1`}>
                {t('add.scan_desc')}
              </p>
            </div>
          </motion.button>

          {/* Manual option */}
          <motion.button
            onClick={() => setMode('manual')}
            className={`w-full rounded-2xl border p-5 flex items-center gap-4 ${theme.card} ${theme.border}`}
            whileTap={{ scale: 0.98 }}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${theme.surface}`}>
              <Keyboard size={24} className={theme.textMuted} />
            </div>
            <div className="text-left">
              <p className={`font-semibold ${theme.text}`}>{t('add.manual_button')}</p>
            </div>
          </motion.button>

          <p className={`text-xs ${theme.textMuted} text-center mt-6`}>
            {t('scan.demo_note')}
          </p>
        </div>
      </div>
    )
  }

  // Scanning animation
  if (mode === 'scan' && scanning) {
    return (
      <div className={`min-h-screen ${theme.bg} flex flex-col items-center justify-center p-6`}>
        <motion.div
          className="relative w-64 h-64 rounded-3xl overflow-hidden mb-8"
          style={{
            background: theme.isPro
              ? 'linear-gradient(135deg, #1a1a1a, #0a0a0a)'
              : 'linear-gradient(135deg, #fff8ee, #fdf6e3)',
            border: `2px solid ${theme.isPro ? '#22d3ee' : '#f97316'}`,
          }}
        >
          {/* Scan line animation */}
          <motion.div
            className="absolute left-0 right-0 h-0.5"
            style={{
              background: theme.isPro
                ? 'linear-gradient(90deg, transparent, #22d3ee, transparent)'
                : 'linear-gradient(90deg, transparent, #f97316, transparent)',
              boxShadow: `0 0 15px ${theme.isPro ? '#22d3ee' : '#f97316'}`,
            }}
            animate={{ top: ['10%', '90%', '10%'] }}
            transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
          />

          {/* Corner brackets */}
          {[['top-3 left-3', 'border-t-2 border-l-2'], ['top-3 right-3', 'border-t-2 border-r-2'],
            ['bottom-3 left-3', 'border-b-2 border-l-2'], ['bottom-3 right-3', 'border-b-2 border-r-2']].map(([pos, border], i) => (
            <div
              key={i}
              className={`absolute ${pos} w-8 h-8 ${border} ${theme.accentBorder} rounded-sm opacity-60`}
            />
          ))}

          <div className="absolute inset-0 flex items-center justify-center">
            <motion.div
              animate={{ opacity: [0.3, 0.8, 0.3] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            >
              <ScanLine size={48} className={theme.accentText} />
            </motion.div>
          </div>
        </motion.div>

        <motion.p
          className={`${theme.text} font-semibold ${theme.fontSizeLg}`}
          animate={{ opacity: [0.5, 1, 0.5] }}
          transition={{ duration: 1.5, repeat: Infinity }}
        >
          {t('add.scan_processing')}
        </motion.p>
      </div>
    )
  }

  // Confirm scan result or manual form
  const isConfirm = mode === 'confirm'

  if (saved) {
    return (
      <div className={`min-h-screen ${theme.bg} flex items-center justify-center`}>
        <motion.div
          className="flex flex-col items-center"
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: 'spring', stiffness: 200 }}
        >
          <motion.div
            className={`w-24 h-24 rounded-full flex items-center justify-center mb-4 ${
              theme.isPro ? 'bg-success/20' : 'bg-success/10'
            }`}
            animate={{ scale: [1, 1.1, 1] }}
            transition={{ duration: 0.5 }}
          >
            <Check size={48} className="text-success" />
          </motion.div>
          <p className={`text-xl font-bold ${theme.text}`}>{t('add.saved')}</p>
        </motion.div>
      </div>
    )
  }

  return (
    <div className={`min-h-screen ${theme.bg} pb-24`}>
      <div className="max-w-lg mx-auto px-4 pt-12">
        {/* Header */}
        <motion.div
          className="flex items-center justify-between mb-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
        >
          <h1 className={`${theme.fontSizeXl} font-bold ${theme.text}`}>
            {isConfirm ? t('scan.found') : t('add.title')}
          </h1>
          <button onClick={() => setMode('choose')} className={`p-2 rounded-xl ${theme.surface}`}>
            <X size={20} className={theme.textMuted} />
          </button>
        </motion.div>

        {isConfirm && (
          <motion.div
            className={`flex items-center gap-2 px-3 py-2 rounded-xl mb-6 ${
              theme.isPro ? 'bg-success/10 text-success' : 'bg-success/10 text-success'
            }`}
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <Check size={16} />
            <span className="text-sm font-medium">{t('scan.found')}</span>
          </motion.div>
        )}

        {/* Form fields */}
        <div className="space-y-5">
          {/* Name */}
          <div>
            <label className={`text-sm font-medium ${theme.text} mb-1.5 block`}>{t('add.name_label')}</label>
            <input
              type="text"
              value={form.name}
              onChange={e => updateForm('name', e.target.value)}
              placeholder={t('add.name_placeholder')}
              className={`w-full px-4 py-3 rounded-xl border outline-none transition-colors ${theme.input} ${theme.fontSize}`}
            />
          </div>

          {/* Dosage */}
          <div>
            <label className={`text-sm font-medium ${theme.text} mb-1.5 block`}>{t('add.dosage_label')}</label>
            <input
              type="text"
              value={form.dosage}
              onChange={e => updateForm('dosage', e.target.value)}
              placeholder={t('add.dosage_placeholder')}
              className={`w-full px-4 py-3 rounded-xl border outline-none transition-colors ${theme.input}`}
            />
          </div>

          {/* Frequency */}
          <div>
            <label className={`text-sm font-medium ${theme.text} mb-1.5 block`}>{t('add.frequency_label')}</label>
            <div className="grid grid-cols-2 gap-2">
              {['daily', 'twice_daily', 'weekly', 'as_needed'].map(freq => (
                <button
                  key={freq}
                  onClick={() => updateForm('frequency', freq)}
                  className={`px-3 py-2.5 rounded-xl text-sm font-medium border transition-all ${
                    form.frequency === freq
                      ? `${theme.accentBorder} ${theme.accentText} ${theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'}`
                      : `${theme.border} ${theme.textMuted} ${theme.card}`
                  }`}
                >
                  {t(`medications.${freq}`)}
                </button>
              ))}
            </div>
          </div>

          {/* Time */}
          <div>
            <label className={`text-sm font-medium ${theme.text} mb-1.5 block`}>{t('add.time_label')}</label>
            <div className="grid grid-cols-4 gap-2">
              {['morning', 'afternoon', 'evening', 'bedtime'].map(time => (
                <button
                  key={time}
                  onClick={() => updateForm('time', time)}
                  className={`px-2 py-2.5 rounded-xl text-xs font-medium border transition-all ${
                    form.time === time
                      ? `${theme.accentBorder} ${theme.accentText} ${theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'}`
                      : `${theme.border} ${theme.textMuted} ${theme.card}`
                  }`}
                >
                  {t(`medications.${time}`)}
                </button>
              ))}
            </div>
          </div>

          {/* Food */}
          <div>
            <label className={`text-sm font-medium ${theme.text} mb-1.5 block`}>{t('add.food_label')}</label>
            <div className="grid grid-cols-3 gap-2">
              {['with_food', 'before_food', 'after_food'].map(food => (
                <button
                  key={food}
                  onClick={() => updateForm('food', food)}
                  className={`px-2 py-2.5 rounded-xl text-xs font-medium border transition-all ${
                    form.food === food
                      ? `${theme.accentBorder} ${theme.accentText} ${theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'}`
                      : `${theme.border} ${theme.textMuted} ${theme.card}`
                  }`}
                >
                  {t(`medications.${food}`)}
                </button>
              ))}
            </div>
          </div>

          {/* Color picker */}
          <div>
            <label className={`text-sm font-medium ${theme.text} mb-1.5 block`}>{t('add.color_label')}</label>
            <div className="flex gap-2 flex-wrap">
              {PILL_COLORS.map(color => (
                <button
                  key={color}
                  onClick={() => updateForm('color', color)}
                  className={`w-9 h-9 rounded-full border-2 transition-all ${
                    form.color === color ? 'scale-110 border-white' : 'border-transparent'
                  }`}
                  style={{ backgroundColor: color }}
                />
              ))}
            </div>
          </div>

          {/* Icon picker */}
          <div>
            <label className={`text-sm font-medium ${theme.text} mb-1.5 block`}>{t('add.icon_label')}</label>
            <div className="flex gap-2 flex-wrap">
              {PILL_ICONS.map(icon => (
                <button
                  key={icon}
                  onClick={() => updateForm('icon', icon)}
                  className={`w-10 h-10 rounded-xl flex items-center justify-center border transition-all ${
                    form.icon === icon
                      ? `${theme.accentBorder} ${theme.isPro ? 'bg-neon-cyan/10' : 'bg-warm-orange/10'}`
                      : `${theme.border} ${theme.card}`
                  }`}
                >
                  <PillIcon icon={icon} size={20} color={form.icon === icon ? form.color : undefined} />
                </button>
              ))}
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className={`text-sm font-medium ${theme.text} mb-1.5 block`}>{t('add.notes_label')}</label>
            <textarea
              value={form.notes}
              onChange={e => updateForm('notes', e.target.value)}
              placeholder={t('add.notes_placeholder')}
              rows={2}
              className={`w-full px-4 py-3 rounded-xl border outline-none transition-colors resize-none ${theme.input}`}
            />
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3 mt-8">
          {isConfirm && (
            <motion.button
              whileTap={{ scale: 0.95 }}
              onClick={() => { setMode('scan'); handleScan() }}
              className={`flex-1 py-3.5 rounded-2xl font-semibold flex items-center justify-center gap-2 ${theme.btnSecondary}`}
            >
              <RotateCcw size={16} />
              {t('scan.retry')}
            </motion.button>
          )}
          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={handleSave}
            disabled={!form.name.trim()}
            className={`flex-1 py-3.5 rounded-2xl font-semibold ${theme.btnPrimary} disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            {isConfirm ? t('scan.confirm') : t('add.save')}
          </motion.button>
        </div>
      </div>
    </div>
  )
}
