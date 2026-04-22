import Foundation
import SwiftUI

// MARK: - Emoji Constants (Unicode escapes for reliable rendering)
enum Emoji {
    static let pill       = "\u{1F48A}"  // 💊
    static let camera     = "\u{1F4F8}"  // 📸
    static let fire       = "\u{1F525}"  // 🔥
    static let trophy     = "\u{1F3C6}"  // 🏆
    static let wave       = "\u{1F44B}"  // 👋
    static let cool       = "\u{1F60E}"  // 😎
    static let smile      = "\u{1F60A}"  // 😊
    static let neutral    = "\u{1F610}"  // 😐
    static let angry      = "\u{1F624}"  // 😤
    static let eyeroll    = "\u{1F644}"  // 🙄
    static let sparkles   = "\u{2728}"   // ✨
    static let seedling   = "\u{1F331}"  // 🌱
    static let herb       = "\u{1F33F}"  // 🌿
    static let sunflower  = "\u{1F33B}"  // 🌻
    static let star       = "\u{2B50}"   // ⭐
    static let gem        = "\u{1F48E}"  // 💎
    static let crown      = "\u{1F451}"  // 👑
    static let superhero  = "\u{1F9B8}"  // 🦸
    static let rainbow    = "\u{1F308}"  // 🌈
    static let party      = "\u{1F389}"  // 🎉
    static let rocket     = "\u{1F680}"  // 🚀
    static let muscle     = "\u{1F4AA}"  // 💪
    static let target     = "\u{1F3AF}"  // 🎯
    static let medal      = "\u{1F3C5}"  // 🏅
    static let shield     = "\u{1F6E1}"  // 🛡
    static let lightning   = "\u{26A1}"   // ⚡
    static let gift       = "\u{1F381}"  // 🎁
    static let bell       = "\u{1F514}"  // 🔔
    static let heart      = "\u{2764}\u{FE0F}" // ❤️
    static let check      = "\u{2705}"   // ✅
    static let clap       = "\u{1F44F}"  // 👏
    static let brain      = "\u{1F9E0}"  // 🧠
    static let droplet    = "\u{1F4A7}"  // 💧
    static let leaf       = "\u{1F343}"  // 🍃
    static let sun        = "\u{2600}\u{FE0F}" // ☀️
    static let moon       = "\u{1F319}"  // 🌙
    static let thumbsUp   = "\u{1F44D}"  // 👍
    static let hundredPts = "\u{1F4AF}"  // 💯
    static let gamepad    = "\u{1F3AE}"  // 🎮
    static let scroll     = "\u{1F4DC}"  // 📜
    static let boom       = "\u{1F4A5}"  // 💥
    static let confetti   = "\u{1F38A}"  // 🎊
    static let cherryBlossom = "\u{1F338}" // 🌸
    static let pill2      = "\u{1F489}"  // 💉 (syringe for variety)
    static let heartPulse = "\u{1F493}"  // 💓
}

// MARK: - Medication Model
struct Medication: Identifiable, Codable, Equatable {
    var id = UUID()
    var name: String
    var dosage: String
    var frequency: Frequency
    var timeOfDay: TimeOfDay
    var foodRelation: FoodRelation
    var notes: String
    var colorHex: String
    var iconName: String
    var isActive: Bool
    var createdAt: Date
    var weekDay: Int?

    init(
        name: String,
        dosage: String,
        frequency: Frequency = .daily,
        timeOfDay: TimeOfDay = .morning,
        foodRelation: FoodRelation = .withFood,
        notes: String = "",
        colorHex: String = "#22D3EE",
        iconName: String = "pill.fill",
        isActive: Bool = true,
        weekDay: Int? = nil
    ) {
        self.name = name
        self.dosage = dosage
        self.frequency = frequency
        self.timeOfDay = timeOfDay
        self.foodRelation = foodRelation
        self.notes = notes
        self.colorHex = colorHex
        self.iconName = iconName
        self.isActive = isActive
        self.createdAt = Date()
        self.weekDay = weekDay
    }

    var color: Color { Color(hex: colorHex) }
}

// MARK: - Enums
enum Frequency: String, Codable, CaseIterable {
    case daily
    case twiceDaily = "twice_daily"
    case weekly
    case asNeeded = "as_needed"

    var localizationKey: String {
        switch self {
        case .daily: return "freq_daily"
        case .twiceDaily: return "freq_twice_daily"
        case .weekly: return "freq_weekly"
        case .asNeeded: return "freq_as_needed"
        }
    }
}

enum TimeOfDay: String, Codable, CaseIterable {
    case morning, afternoon, evening, bedtime

    var localizationKey: String { "time_\(rawValue)" }

    var icon: String {
        switch self {
        case .morning: return "sunrise.fill"
        case .afternoon: return "sun.max.fill"
        case .evening: return "sunset.fill"
        case .bedtime: return "moon.stars.fill"
        }
    }
}

enum FoodRelation: String, Codable, CaseIterable {
    case withFood = "with_food"
    case beforeFood = "before_food"
    case afterFood = "after_food"

    var localizationKey: String { "food_\(rawValue)" }
}

// MARK: - Dose Log
struct DoseLog: Identifiable, Codable, Equatable {
    var id = UUID()
    let medicationId: UUID
    let status: DoseStatus
    let timestamp: Date
    var dateString: String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: timestamp)
    }
}

enum DoseStatus: String, Codable {
    case taken, skipped
}

// MARK: - XP Rewards
enum XPReward {
    static let takeDose = 10
    static let completeAllDaily = 50
    static let dailyCheckIn = 5
    static let firstScan = 25
    static let perfectWeek = 100
    static let streak7 = 70
    static let streak30 = 300
    static let addMedication = 15
}

// MARK: - Game Level
struct GameLevel: Equatable {
    let level: Int
    let titleKey: String
    let emoji: String         // kept for backward compat, no longer rendered directly
    let sfSymbol: String      // primary visual — always renders
    let xpRequired: Int
    let color: Color

    static func == (lhs: GameLevel, rhs: GameLevel) -> Bool {
        lhs.level == rhs.level
    }

    static let all: [GameLevel] = [
        GameLevel(level: 1, titleKey: "level_1", emoji: Emoji.seedling, sfSymbol: "leaf.fill", xpRequired: 0, color: Color(hex: "#10B981")),
        GameLevel(level: 2, titleKey: "level_2", emoji: Emoji.herb, sfSymbol: "leaf.circle.fill", xpRequired: 100, color: Color(hex: "#22D3EE")),
        GameLevel(level: 3, titleKey: "level_3", emoji: Emoji.sunflower, sfSymbol: "camera.macro", xpRequired: 300, color: Color(hex: "#3B82F6")),
        GameLevel(level: 4, titleKey: "level_4", emoji: Emoji.star, sfSymbol: "star.fill", xpRequired: 600, color: Color(hex: "#A855F7")),
        GameLevel(level: 5, titleKey: "level_5", emoji: Emoji.fire, sfSymbol: "flame.fill", xpRequired: 1000, color: Color(hex: "#F97316")),
        GameLevel(level: 6, titleKey: "level_6", emoji: Emoji.gem, sfSymbol: "diamond.fill", xpRequired: 1500, color: Color(hex: "#EC4899")),
        GameLevel(level: 7, titleKey: "level_7", emoji: Emoji.trophy, sfSymbol: "trophy.fill", xpRequired: 2200, color: Color(hex: "#EAB308")),
        GameLevel(level: 8, titleKey: "level_8", emoji: Emoji.crown, sfSymbol: "crown.fill", xpRequired: 3000, color: Color(hex: "#F59E0B")),
        GameLevel(level: 9, titleKey: "level_9", emoji: Emoji.superhero, sfSymbol: "bolt.heart.fill", xpRequired: 4000, color: Color(hex: "#EF4444")),
        GameLevel(level: 10, titleKey: "level_10", emoji: Emoji.rainbow, sfSymbol: "sparkles", xpRequired: 5500, color: Color(hex: "#8B5CF6")),
    ]

    static func forXP(_ xp: Int) -> GameLevel {
        all.last(where: { $0.xpRequired <= xp }) ?? all[0]
    }

    static func nextAfter(_ current: GameLevel) -> GameLevel? {
        guard current.level < all.count else { return nil }
        return all[current.level]
    }

    var localizedTitle: String {
        NSLocalizedString(titleKey, comment: "")
    }
}

// MARK: - Daily Mission
struct DailyMission: Identifiable {
    let id: String
    let titleKey: String
    let icon: String
    let xpReward: Int
    let isCompleted: Bool

    var localizedTitle: String {
        NSLocalizedString(titleKey, comment: "")
    }
}

// MARK: - Achievement (expanded with meme badges)
enum Achievement: String, CaseIterable, Codable {
    // Chapter 1: Getting Started
    case firstPill = "first_pill"
    case scanner = "scanner"

    // Chapter 2: Dose Milestones
    case tenDoses = "ten_doses"
    case fiftyDoses = "fifty_doses"
    case hundredDoses = "hundred_doses"
    case fiveHundredDoses = "five_hundred_doses"

    // Chapter 3: Streak Champions
    case weekStreak = "week_streak"
    case monthStreak = "month_streak"
    case quarterStreak = "quarter_streak"
    case perfectWeek = "perfect_week"

    // Chapter 4: Lifestyle
    case morningPerson = "morning_person"
    case nightOwl = "night_owl"
    case pillCollector = "pill_collector"

    // Chapter 5: Leveling Up
    case level5 = "level_5_reached"
    case level10 = "level_10_reached"

    // Chapter 6: Anti-shame (Buddhist)
    case yoloCasual = "yolo_casual"
    case comebackKid = "comeback_kid"
    case weekendWarrior = "weekend_warrior"

    var icon: String {
        switch self {
        case .firstPill:        return "wand.and.stars"
        case .scanner:          return "qrcode.viewfinder"
        case .tenDoses:         return "heart.fill"
        case .fiftyDoses:       return "bolt.circle.fill"
        case .hundredDoses:     return "checkmark.seal.fill"
        case .fiveHundredDoses: return "sparkles"
        case .weekStreak:       return "flame.fill"
        case .monthStreak:      return "crown.fill"
        case .quarterStreak:    return "medal.fill"
        case .perfectWeek:      return "trophy.fill"
        case .morningPerson:    return "sunrise.fill"
        case .nightOwl:         return "moon.stars.fill"
        case .pillCollector:    return "cross.case.fill"
        case .level5:           return "sun.max.fill"
        case .level10:          return "star.fill"
        case .yoloCasual:       return "figure.mind.and.body"
        case .comebackKid:      return "arrow.triangle.2.circlepath"
        case .weekendWarrior:   return "cup.and.saucer.fill"
        }
    }

    var color: Color {
        switch self {
        case .firstPill:        return Color(hex: "#4ADE80")
        case .scanner:          return Color(hex: "#A855F7")
        case .tenDoses:         return Color(hex: "#34D399")
        case .fiftyDoses:       return Color(hex: "#F97316")
        case .hundredDoses:     return Color(hex: "#EF4444")
        case .fiveHundredDoses: return Color(hex: "#8B5CF6")
        case .weekStreak:       return Color(hex: "#F97316")
        case .monthStreak:      return Color(hex: "#EAB308")
        case .quarterStreak:    return Color(hex: "#EC4899")
        case .perfectWeek:      return Color(hex: "#10B981")
        case .morningPerson:    return Color(hex: "#FB923C")
        case .nightOwl:         return Color(hex: "#818CF8")
        case .pillCollector:    return Color(hex: "#22D3EE")
        case .level5:           return Color(hex: "#F97316")
        case .level10:          return Color(hex: "#8B5CF6")
        case .yoloCasual:       return Color(hex: "#6EE7B7")
        case .comebackKid:      return Color(hex: "#F472B6")
        case .weekendWarrior:   return Color(hex: "#FBBF24")
        }
    }

    var xpReward: Int {
        switch self {
        case .firstPill:        return 25
        case .scanner:          return 25
        case .tenDoses:         return 30
        case .fiftyDoses:       return 75
        case .hundredDoses:     return 150
        case .fiveHundredDoses: return 500
        case .weekStreak:       return 70
        case .monthStreak:      return 300
        case .quarterStreak:    return 500
        case .perfectWeek:      return 100
        case .morningPerson:    return 50
        case .nightOwl:         return 50
        case .pillCollector:    return 40
        case .level5:           return 50
        case .level10:          return 100
        case .yoloCasual:       return 20
        case .comebackKid:      return 30
        case .weekendWarrior:   return 15
        }
    }

    var tier: AchievementTier {
        switch self {
        case .firstPill, .scanner, .tenDoses, .weekendWarrior:
            return .bronze
        case .weekStreak, .perfectWeek, .fiftyDoses, .level5, .morningPerson, .nightOwl, .yoloCasual, .comebackKid, .pillCollector:
            return .silver
        case .monthStreak, .quarterStreak, .hundredDoses, .fiveHundredDoses, .level10:
            return .gold
        }
    }
}

enum AchievementTier: String {
    case bronze, silver, gold

    var borderGradient: LinearGradient {
        switch self {
        case .bronze: return LinearGradient(colors: [Color(hex: "#CD7F32"), Color(hex: "#B87333")], startPoint: .top, endPoint: .bottom)
        case .silver: return LinearGradient(colors: [Color(hex: "#C0C0C0"), Color(hex: "#A8A9AD")], startPoint: .top, endPoint: .bottom)
        case .gold: return LinearGradient(colors: [Color(hex: "#FFD700"), Color(hex: "#FFA500")], startPoint: .top, endPoint: .bottom)
        }
    }
}

// MARK: - Weekly Stat
struct DayStat: Identifiable {
    let id = UUID()
    let date: Date
    let dayLabel: String
    let taken: Int
    let total: Int
    var pct: Double { total > 0 ? Double(taken) / Double(total) * 100 : 0 }
}

// MARK: - Pill colors & icons
struct PillOptions {
    static let colors: [String] = [
        "#C4B5FD", "#F9A8D4", "#93C5FD", "#86EFAC",
        "#FDE68A", "#FDBA74", "#FCA5A5", "#A7F3D0"
    ]

    static let icons: [String] = [
        "pill.fill", "capsule.fill", "cross.vial.fill", "drop.fill",
        "syringe", "stethoscope", "leaf.fill", "heart.fill"
    ]

    static let demoScans: [(name: String, dosage: String, frequency: Frequency, time: TimeOfDay, food: FoodRelation)] = [
        ("Vitamin D3", "2000 IU", .daily, .morning, .withFood),
        ("Omega-3 Fish Oil", "1000 mg", .daily, .morning, .withFood),
        ("Magnesium Glycinate", "400 mg", .daily, .evening, .afterFood),
        ("Melatonin", "3 mg", .daily, .bedtime, .beforeFood),
        ("Ashwagandha", "600 mg", .daily, .morning, .withFood),
        ("Probiotics", "50B CFU", .daily, .morning, .beforeFood),
        ("Vitamin C", "1000 mg", .daily, .morning, .withFood),
        ("CoQ10", "200 mg", .daily, .morning, .withFood),
        ("Iron", "18 mg", .daily, .morning, .beforeFood),
        ("Vitamin B12", "1000 mcg", .daily, .morning, .withFood),
    ]
}

// MARK: - Color Hex Extension
extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r, g, b: Double
        r = Double((int >> 16) & 0xFF) / 255.0
        g = Double((int >> 8) & 0xFF) / 255.0
        b = Double(int & 0xFF) / 255.0
        self.init(red: r, green: g, blue: b)
    }
}
