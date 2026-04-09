import { Pill, Circle, Droplets, Heart, Leaf, Sun, Moon, Star, Zap } from 'lucide-react'

const iconMap = {
  pill: Pill,
  capsule: Pill,
  tablet: Circle,
  drop: Droplets,
  heart: Heart,
  leaf: Leaf,
  sun: Sun,
  moon: Moon,
  star: Star,
  zap: Zap,
}

export default function PillIcon({ icon = 'pill', size = 20, color, className = '' }) {
  const IconComp = iconMap[icon] || Pill
  return <IconComp size={size} color={color} className={className} />
}
