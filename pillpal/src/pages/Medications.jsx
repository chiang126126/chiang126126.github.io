import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, Trash2, Pause, Play, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useTheme } from '../context/ThemeContext'
import { useMedications } from '../context/MedicationContext'
import PillIcon from '../components/PillIcon'
import { haptic } from '../utils/helpers'

export default function Medications() {
  const { t } = useTranslation()
  const theme = useTheme()
  const navigate = useNavigate()
  const { medications, updateMedication, deleteMedication } = useMedications()
  const [expandedId, setExpandedId] = useState(null)
  const [deleteConfirm, setDeleteConfirm] = useState(null)

  const active = medications.filter(m => m.active)
  const paused = medications.filter(m => !m.active)

  const handleDelete = (id) => {
    if (deleteConfirm === id) {
      haptic('heavy')
      deleteMedication(id)
      setDeleteConfirm(null)
    } else {
      haptic('light')
      setDeleteConfirm(id)
      setTimeout(() => setDeleteConfirm(null), 3000)
    }
  }

  const renderMedCard = (med, i) => {
    const isExpanded = expandedId === med.id
    return (
      <motion.div
        key={med.id}
        layout
        className={`rounded-2xl border overflow-hidden ${theme.card} ${theme.border}`}
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: med.active ? 1 : 0.6, y: 0 }}
        transition={{ delay: i * 0.05 }}
      >
        <button
          onClick={() => { haptic('light'); setExpandedId(isExpanded ? null : med.id) }}
          className={`w-full flex items-center gap-3 p-4 text-left`}
        >
          <div
            className="w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{
              backgroundColor: `${med.color}22`,
              border: `1.5px solid ${med.color}44`,
            }}
          >
            <PillIcon icon={med.icon} size={22} color={med.color} />
          </div>
          <div className="flex-1 min-w-0">
            <p className={`font-semibold truncate ${theme.text} ${theme.fontSize}`}>
              {med.name}
            </p>
            <p className={`text-sm ${theme.textMuted}`}>
              {med.dosage} · {t(`medications.${med.frequency}`)}
            </p>
          </div>
          <ChevronRight
            size={18}
            className={`${theme.textMuted} transition-transform ${isExpanded ? 'rotate-90' : ''}`}
          />
        </button>

        <AnimatePresence>
          {isExpanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className={`px-4 pb-4 pt-1 border-t ${theme.border}`}>
                <div className="grid grid-cols-2 gap-3 mb-4">
                  <div>
                    <p className={`text-xs ${theme.textMuted}`}>{t('medications.time')}</p>
                    <p className={`text-sm font-medium ${theme.text}`}>{t(`medications.${med.time}`)}</p>
                  </div>
                  <div>
                    <p className={`text-xs ${theme.textMuted}`}>{t('medications.frequency')}</p>
                    <p className={`text-sm font-medium ${theme.text}`}>{t(`medications.${med.frequency}`)}</p>
                  </div>
                  {med.food && (
                    <div>
                      <p className={`text-xs ${theme.textMuted}`}>{t('add.food_label')}</p>
                      <p className={`text-sm font-medium ${theme.text}`}>{t(`medications.${med.food}`)}</p>
                    </div>
                  )}
                  {med.notes && (
                    <div className="col-span-2">
                      <p className={`text-xs ${theme.textMuted}`}>{t('medications.notes')}</p>
                      <p className={`text-sm ${theme.text}`}>{med.notes}</p>
                    </div>
                  )}
                </div>

                <div className="flex gap-2">
                  <motion.button
                    whileTap={{ scale: 0.95 }}
                    onClick={() => {
                      haptic('medium')
                      updateMedication(med.id, { active: !med.active })
                    }}
                    className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-sm font-medium ${theme.btnSecondary}`}
                  >
                    {med.active ? <Pause size={15} /> : <Play size={15} />}
                    {med.active ? t('medications.pause') : t('medications.resume')}
                  </motion.button>
                  <motion.button
                    whileTap={{ scale: 0.95 }}
                    onClick={() => handleDelete(med.id)}
                    className={`px-4 py-2.5 rounded-xl text-sm font-medium ${
                      deleteConfirm === med.id
                        ? 'bg-danger text-white'
                        : `${theme.surface} text-danger border border-danger/20`
                    }`}
                  >
                    <Trash2 size={15} />
                  </motion.button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    )
  }

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
            {t('medications.title')}
          </h1>
          <motion.button
            whileTap={{ scale: 0.9 }}
            onClick={() => navigate('/scan')}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-semibold ${theme.btnPrimary}`}
          >
            <Plus size={16} />
            {t('medications.add_new')}
          </motion.button>
        </motion.div>

        {/* Active */}
        {active.length > 0 && (
          <div className="mb-6">
            <p className={`text-xs font-medium uppercase tracking-wider mb-3 ${theme.textMuted}`}>
              {t('medications.active')} ({active.length})
            </p>
            <div className="grid gap-2">
              {active.map((m, i) => renderMedCard(m, i))}
            </div>
          </div>
        )}

        {/* Paused */}
        {paused.length > 0 && (
          <div className="mb-6">
            <p className={`text-xs font-medium uppercase tracking-wider mb-3 ${theme.textMuted}`}>
              {t('medications.paused')} ({paused.length})
            </p>
            <div className="grid gap-2">
              {paused.map((m, i) => renderMedCard(m, i))}
            </div>
          </div>
        )}

        {/* Empty state */}
        {medications.length === 0 && (
          <motion.div
            className={`flex flex-col items-center justify-center py-20`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <motion.div
              className="text-6xl mb-4"
              animate={{ rotate: [0, 10, -10, 0] }}
              transition={{ duration: 2, repeat: Infinity, repeatDelay: 2 }}
            >
              💊
            </motion.div>
            <p className={`${theme.text} font-semibold mb-1`}>{t('medications.empty')}</p>
            <p className={`text-sm ${theme.textMuted} text-center mb-6`}>{t('dashboard.add_first')}</p>
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
      </div>
    </div>
  )
}
