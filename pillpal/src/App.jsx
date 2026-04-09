import { Routes, Route } from 'react-router-dom'
import { AnimatePresence } from 'framer-motion'
import { useTheme } from './context/ThemeContext'
import { useMedications } from './context/MedicationContext'
import BottomNav from './components/BottomNav'
import Onboarding from './pages/Onboarding'
import Dashboard from './pages/Dashboard'
import Medications from './pages/Medications'
import ScanAdd from './pages/ScanAdd'
import Stats from './pages/Stats'
import Settings from './pages/Settings'

export default function App() {
  const theme = useTheme()
  const { onboardingDone } = useMedications()

  if (!onboardingDone) {
    return <Onboarding />
  }

  return (
    <div className={`h-full ${theme.bg} overflow-y-auto`}>
      <AnimatePresence mode="wait">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/medications" element={<Medications />} />
          <Route path="/scan" element={<ScanAdd />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </AnimatePresence>
      <BottomNav />
    </div>
  )
}
