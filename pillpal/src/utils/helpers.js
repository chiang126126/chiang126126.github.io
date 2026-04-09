// Haptic feedback (if supported)
export function haptic(style = 'light') {
  if (navigator.vibrate) {
    switch (style) {
      case 'light': navigator.vibrate(10); break
      case 'medium': navigator.vibrate(20); break
      case 'heavy': navigator.vibrate(40); break
      case 'success': navigator.vibrate([10, 50, 20]); break
      case 'error': navigator.vibrate([30, 100, 30, 100, 30]); break
    }
  }
}

// Get greeting based on time of day
export function getGreetingKey() {
  const h = new Date().getHours()
  if (h < 12) return 'dashboard.greeting_morning'
  if (h < 18) return 'dashboard.greeting_afternoon'
  return 'dashboard.greeting_evening'
}

// Get a random sassy/gentle reminder
export function getRandomReminder(t, style = 'sassy') {
  const key = `reminders.${style}`
  const messages = t(key, { returnObjects: true })
  if (Array.isArray(messages)) {
    return messages[Math.floor(Math.random() * messages.length)]
  }
  return ''
}

// Pill/supplement color options
export const PILL_COLORS = [
  '#22d3ee', // cyan
  '#39ff14', // neon green
  '#ff6b35', // orange
  '#a855f7', // purple
  '#ff2d78', // pink
  '#eab308', // yellow
  '#10b981', // emerald
  '#3b82f6', // blue
  '#f43f5e', // rose
  '#8b5cf6', // violet
]

// Pill icon options
export const PILL_ICONS = [
  'pill', 'capsule', 'tablet', 'drop', 'heart', 'leaf', 'sun', 'moon', 'star', 'zap'
]

// Demo scan results for simulation
export const DEMO_SCAN_RESULTS = [
  { name: 'Vitamin D3', dosage: '2000 IU', frequency: 'daily', time: 'morning', food: 'with_food' },
  { name: 'Omega-3 Fish Oil', dosage: '1000 mg', frequency: 'daily', time: 'morning', food: 'with_food' },
  { name: 'Magnesium Glycinate', dosage: '400 mg', frequency: 'daily', time: 'evening', food: 'after_food' },
  { name: 'Melatonin', dosage: '3 mg', frequency: 'daily', time: 'bedtime', food: 'before_food' },
  { name: 'Ashwagandha', dosage: '600 mg', frequency: 'daily', time: 'morning', food: 'with_food' },
  { name: 'Probiotics', dosage: '50B CFU', frequency: 'daily', time: 'morning', food: 'before_food' },
  { name: 'Vitamin C', dosage: '1000 mg', frequency: 'daily', time: 'morning', food: 'with_food' },
  { name: 'CoQ10', dosage: '200 mg', frequency: 'daily', time: 'morning', food: 'with_food' },
  { name: 'Iron', dosage: '18 mg', frequency: 'daily', time: 'morning', food: 'before_food' },
  { name: 'B12', dosage: '1000 mcg', frequency: 'daily', time: 'morning', food: 'with_food' },
]

// Format date for display
export function formatDate(dateStr, locale = 'en') {
  const d = new Date(dateStr)
  return d.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
}

// Get icon component name from pill icon
export function getPillIconName(icon) {
  const map = {
    pill: 'Pill',
    capsule: 'Pill',
    tablet: 'Circle',
    drop: 'Droplets',
    heart: 'Heart',
    leaf: 'Leaf',
    sun: 'Sun',
    moon: 'Moon',
    star: 'Star',
    zap: 'Zap',
  }
  return map[icon] || 'Pill'
}
