import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const MedicationContext = createContext()

const STORAGE_KEY = 'pillpal-data'

const defaultData = {
  medications: [],
  logs: [],
  streak: 0,
  bestStreak: 0,
  achievements: [],
  onboardingDone: false,
  reminderStyle: 'sassy',
}

function loadData() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    return saved ? { ...defaultData, ...JSON.parse(saved) } : defaultData
  } catch {
    return defaultData
  }
}

function saveData(data) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
}

// Generate a simple unique ID
function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

export function MedicationProvider({ children }) {
  const [data, setData] = useState(loadData)

  useEffect(() => {
    saveData(data)
  }, [data])

  const update = useCallback((updater) => {
    setData(prev => {
      const next = typeof updater === 'function' ? updater(prev) : { ...prev, ...updater }
      return next
    })
  }, [])

  // Add a medication
  const addMedication = useCallback((med) => {
    const newMed = {
      id: genId(),
      createdAt: new Date().toISOString(),
      active: true,
      color: '#22d3ee',
      icon: 'pill',
      ...med,
    }
    update(prev => ({
      ...prev,
      medications: [...prev.medications, newMed],
      achievements: prev.medications.length === 0
        ? [...prev.achievements, 'first_pill']
        : prev.achievements,
    }))
    return newMed
  }, [update])

  // Update a medication
  const updateMedication = useCallback((id, changes) => {
    update(prev => ({
      ...prev,
      medications: prev.medications.map(m =>
        m.id === id ? { ...m, ...changes } : m
      ),
    }))
  }, [update])

  // Delete a medication
  const deleteMedication = useCallback((id) => {
    update(prev => ({
      ...prev,
      medications: prev.medications.filter(m => m.id !== id),
      logs: prev.logs.filter(l => l.medId !== id),
    }))
  }, [update])

  // Log a dose (take or skip)
  const logDose = useCallback((medId, status = 'taken') => {
    const log = {
      id: genId(),
      medId,
      status, // 'taken' | 'skipped'
      timestamp: new Date().toISOString(),
      date: new Date().toISOString().split('T')[0],
    }
    update(prev => {
      const newLogs = [...prev.logs, log]
      // Calculate streak
      const today = new Date().toISOString().split('T')[0]
      const todayLogs = newLogs.filter(l => l.date === today && l.status === 'taken')
      const activeMeds = prev.medications.filter(m => m.active)
      const todayScheduled = getScheduledForDate(activeMeds, today)
      const allTakenToday = todayScheduled.length > 0 &&
        todayScheduled.every(m => todayLogs.some(l => l.medId === m.id))
      const newStreak = allTakenToday ? prev.streak + 1 : prev.streak
      const newBest = Math.max(newStreak, prev.bestStreak)
      // Check achievements
      const newAchievements = [...prev.achievements]
      if (newStreak >= 7 && !newAchievements.includes('week_streak')) {
        newAchievements.push('week_streak')
      }
      if (newStreak >= 30 && !newAchievements.includes('month_streak')) {
        newAchievements.push('month_streak')
      }
      return {
        ...prev,
        logs: newLogs,
        streak: newStreak,
        bestStreak: newBest,
        achievements: newAchievements,
      }
    })
  }, [update])

  // Check if a med is taken today
  const isTakenToday = useCallback((medId) => {
    const today = new Date().toISOString().split('T')[0]
    return data.logs.some(l => l.medId === medId && l.date === today && l.status === 'taken')
  }, [data.logs])

  // Check if a med is skipped today
  const isSkippedToday = useCallback((medId) => {
    const today = new Date().toISOString().split('T')[0]
    return data.logs.some(l => l.medId === medId && l.date === today && l.status === 'skipped')
  }, [data.logs])

  // Get today's scheduled medications
  const getTodaySchedule = useCallback(() => {
    const today = new Date().toISOString().split('T')[0]
    return getScheduledForDate(data.medications.filter(m => m.active), today)
  }, [data.medications])

  // Get stats
  const getStats = useCallback(() => {
    const today = new Date()
    const last7 = []
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today)
      d.setDate(d.getDate() - i)
      const dateStr = d.toISOString().split('T')[0]
      const dayLogs = data.logs.filter(l => l.date === dateStr)
      const taken = dayLogs.filter(l => l.status === 'taken').length
      const total = Math.max(data.medications.filter(m => m.active).length, 1)
      last7.push({
        date: dateStr,
        day: d.toLocaleDateString('en', { weekday: 'short' }),
        taken,
        total,
        pct: Math.round((taken / total) * 100),
      })
    }
    const totalLogs = data.logs.filter(l => l.status === 'taken').length
    const totalScheduled = Math.max(data.logs.length, 1)
    return {
      weekly: last7,
      adherence: Math.round((totalLogs / totalScheduled) * 100),
      streak: data.streak,
      bestStreak: data.bestStreak,
      totalTaken: totalLogs,
      perfectDays: last7.filter(d => d.pct === 100).length,
    }
  }, [data])

  // Complete onboarding
  const completeOnboarding = useCallback(() => {
    update(prev => ({ ...prev, onboardingDone: true }))
  }, [update])

  // Set reminder style
  const setReminderStyle = useCallback((style) => {
    update(prev => ({ ...prev, reminderStyle: style }))
  }, [update])

  // Clear all data
  const clearAllData = useCallback(() => {
    setData(defaultData)
  }, [])

  return (
    <MedicationContext.Provider value={{
      ...data,
      addMedication,
      updateMedication,
      deleteMedication,
      logDose,
      isTakenToday,
      isSkippedToday,
      getTodaySchedule,
      getStats,
      completeOnboarding,
      setReminderStyle,
      clearAllData,
    }}>
      {children}
    </MedicationContext.Provider>
  )
}

// Helper: get scheduled medications for a given date
function getScheduledForDate(medications, dateStr) {
  const date = new Date(dateStr)
  const dayOfWeek = date.getDay()
  return medications.filter(med => {
    if (med.frequency === 'daily' || med.frequency === 'twice_daily') return true
    if (med.frequency === 'weekly' && med.weekDay === dayOfWeek) return true
    return med.frequency !== 'weekly'
  })
}

export const useMedications = () => useContext(MedicationContext)
