import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import App from './App'
import { ThemeProvider } from './context/ThemeContext'
import { MedicationProvider } from './context/MedicationContext'
import './i18n'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <HashRouter>
      <ThemeProvider>
        <MedicationProvider>
          <App />
        </MedicationProvider>
      </ThemeProvider>
    </HashRouter>
  </React.StrictMode>
)
