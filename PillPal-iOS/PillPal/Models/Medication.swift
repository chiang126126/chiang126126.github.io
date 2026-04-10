import Foundation
import SwiftUI

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
    var weekDay: Int? // 0=Sun, for weekly frequency

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

    var color: Color {
        Color(hex: colorHex)
    }
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

    var localizationKey: String {
        "time_\(rawValue)"
    }

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

    var localizationKey: String {
        "food_\(rawValue)"
    }
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

// MARK: - Achievement
enum Achievement: String, CaseIterable, Codable {
    case firstPill = "first_pill"
    case weekStreak = "week_streak"
    case monthStreak = "month_streak"
    case perfectWeek = "perfect_week"
    case scanner = "scanner"

    var icon: String {
        switch self {
        case .firstPill: return "star.fill"
        case .weekStreak: return "flame.fill"
        case .monthStreak: return "crown.fill"
        case .perfectWeek: return "trophy.fill"
        case .scanner: return "camera.viewfinder"
        }
    }

    var color: Color {
        switch self {
        case .firstPill: return .blue
        case .weekStreak: return .orange
        case .monthStreak: return .yellow
        case .perfectWeek: return .green
        case .scanner: return .purple
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
        "#22D3EE", "#39FF14", "#FF6B35", "#A855F7", "#FF2D78",
        "#EAB308", "#10B981", "#3B82F6", "#F43F5E", "#8B5CF6"
    ]

    static let icons: [String] = [
        "pill.fill", "capsule.fill", "circle.fill", "drop.fill",
        "heart.fill", "leaf.fill", "sun.max.fill", "moon.fill",
        "star.fill", "bolt.fill"
    ]

    // Demo scan results
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
