import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Check } from 'lucide-react'
import { useTheme } from '../context/ThemeContext'
import { haptic } from '../utils/helpers'
import PillIcon from './PillIcon'

export default function BubblePop({ medication, onTake, onSkip, isTaken, isSkipped }) {
  const theme = useTheme()
  const [particles, setParticles] = useState([])
  const [isPopping, setIsPopping] = useState(false)

  const handlePop = () => {
    if (isTaken || isSkipped) return
    setIsPopping(true)
    haptic('success')

    // Create burst particles
    const newParticles = Array.from({ length: 8 }, (_, i) => ({
      id: Date.now() + i,
      angle: (i * 45) * (Math.PI / 180),
      color: medication.color || '#22d3ee',
    }))
    setParticles(newParticles)

    setTimeout(() => {
      onTake()
      setIsPopping(false)
      setTimeout(() => setParticles([]), 600)
    }, 300)
  }

  const done = isTaken || isSkipped

  return (
    <div className="relative flex items-center justify-center">
      {/* Burst particles */}
      <AnimatePresence>
        {particles.map(p => (
          <motion.div
            key={p.id}
            className="absolute w-2 h-2 rounded-full"
            style={{ backgroundColor: p.color }}
            initial={{ x: 0, y: 0, opacity: 1, scale: 1 }}
            animate={{
              x: Math.cos(p.angle) * 60,
              y: Math.sin(p.angle) * 60,
              opacity: 0,
              scale: 0,
            }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.6, ease: 'easeOut' }}
          />
        ))}
      </AnimatePresence>

      {/* Main bubble */}
      <motion.button
        onClick={handlePop}
        onContextMenu={(e) => { e.preventDefault(); if (!done) { haptic('light'); onSkip(); } }}
        disabled={done}
        className={`relative rounded-full flex items-center justify-center transition-all ${
          done
            ? 'opacity-60 cursor-default'
            : 'cursor-pointer active:scale-95'
        }`}
        style={{
          width: theme.isCare ? 64 : 52,
          height: theme.isCare ? 64 : 52,
          backgroundColor: done
            ? (theme.isPro ? '#1e1e1e' : '#f5f0e8')
            : `${medication.color || '#22d3ee'}22`,
          border: `2px solid ${done ? (theme.isPro ? '#333' : '#e0d8cc') : (medication.color || '#22d3ee')}`,
          boxShadow: !done && theme.isPro
            ? `0 0 15px ${medication.color || '#22d3ee'}33`
            : 'none',
        }}
        whileHover={!done ? { scale: 1.1 } : {}}
        whileTap={!done ? { scale: 0.8 } : {}}
        animate={isPopping ? { scale: [1, 1.3, 0.9, 1] } : {}}
      >
        {isTaken ? (
          <motion.div
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: 'spring', stiffness: 500 }}
          >
            <Check size={theme.isCare ? 28 : 22} className="text-success" />
          </motion.div>
        ) : isSkipped ? (
          <span className={`text-xs ${theme.textMuted}`}>-</span>
        ) : (
          <motion.div animate={{ rotate: [0, 5, -5, 0] }} transition={{ duration: 2, repeat: Infinity, repeatDelay: 3 }}>
            <PillIcon
              icon={medication.icon}
              size={theme.isCare ? 28 : 22}
              color={medication.color || '#22d3ee'}
            />
          </motion.div>
        )}
      </motion.button>
    </div>
  )
}
