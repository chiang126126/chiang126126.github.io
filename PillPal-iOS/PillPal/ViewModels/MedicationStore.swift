import Foundation
import SwiftUI

@Observable
final class MedicationStore {
    // MARK: - Persisted Data
    var medications: [Medication] = []
    var logs: [DoseLog] = []
    var streak: Int = 0
    var bestStreak: Int = 0
    var achievements: [Achievement] = []
    var onboardingDone: Bool = false
    var reminderStyle: String = "sassy" // "sassy" | "gentle" | "neutral"

    private let medsKey = "pillpal-medications"
    private let logsKey = "pillpal-logs"
    private let metaKey = "pillpal-meta"

    // MARK: - Init
    init() {
        loadData()
    }

    // MARK: - Persistence
    private func loadData() {
        if let data = UserDefaults.standard.data(forKey: medsKey),
           let decoded = try? JSONDecoder().decode([Medication].self, from: data) {
            medications = decoded
        }
        if let data = UserDefaults.standard.data(forKey: logsKey),
           let decoded = try? JSONDecoder().decode([DoseLog].self, from: data) {
            logs = decoded
        }
        if let data = UserDefaults.standard.data(forKey: metaKey),
           let meta = try? JSONDecoder().decode(StoreMeta.self, from: data) {
            streak = meta.streak
            bestStreak = meta.bestStreak
            achievements = meta.achievements
            onboardingDone = meta.onboardingDone
            reminderStyle = meta.reminderStyle
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(medications) {
            UserDefaults.standard.set(data, forKey: medsKey)
        }
        if let data = try? JSONEncoder().encode(logs) {
            UserDefaults.standard.set(data, forKey: logsKey)
        }
        let meta = StoreMeta(
            streak: streak, bestStreak: bestStreak,
            achievements: achievements, onboardingDone: onboardingDone,
            reminderStyle: reminderStyle
        )
        if let data = try? JSONEncoder().encode(meta) {
            UserDefaults.standard.set(data, forKey: metaKey)
        }
    }

    // MARK: - Medication CRUD
    func addMedication(_ med: Medication) {
        var newMed = med
        newMed.createdAt = Date()
        medications.append(newMed)

        if achievements.isEmpty || (!achievements.contains(.firstPill) && medications.count == 1) {
            achievements.append(.firstPill)
        }
        save()
    }

    func updateMedication(_ id: UUID, with changes: (inout Medication) -> Void) {
        if let idx = medications.firstIndex(where: { $0.id == id }) {
            changes(&medications[idx])
            save()
        }
    }

    func deleteMedication(_ id: UUID) {
        medications.removeAll { $0.id == id }
        logs.removeAll { $0.medicationId == id }
        save()
    }

    func toggleActive(_ id: UUID) {
        if let idx = medications.firstIndex(where: { $0.id == id }) {
            medications[idx].isActive.toggle()
            save()
        }
    }

    // MARK: - Dose Logging
    func logDose(_ medicationId: UUID, status: DoseStatus) {
        let log = DoseLog(medicationId: medicationId, status: status, timestamp: Date())
        logs.append(log)
        recalculateStreak()
        save()
    }

    func isTakenToday(_ medicationId: UUID) -> Bool {
        let today = todayString()
        return logs.contains { $0.medicationId == medicationId && $0.dateString == today && $0.status == .taken }
    }

    func isSkippedToday(_ medicationId: UUID) -> Bool {
        let today = todayString()
        return logs.contains { $0.medicationId == medicationId && $0.dateString == today && $0.status == .skipped }
    }

    // MARK: - Today's Schedule
    func todaySchedule() -> [Medication] {
        let dayOfWeek = Calendar.current.component(.weekday, from: Date()) - 1
        return medications.filter { med in
            guard med.isActive else { return false }
            switch med.frequency {
            case .daily, .twiceDaily: return true
            case .weekly: return med.weekDay == dayOfWeek
            case .asNeeded: return true
            }
        }
    }

    var todayRemaining: Int {
        todaySchedule().filter { !isTakenToday($0.id) && !isSkippedToday($0.id) }.count
    }

    var todayAdherence: Double {
        let schedule = todaySchedule()
        guard !schedule.isEmpty else { return 100 }
        let done = schedule.filter { isTakenToday($0.id) || isSkippedToday($0.id) }.count
        return Double(done) / Double(schedule.count) * 100
    }

    // MARK: - Streak
    private func recalculateStreak() {
        let today = todayString()
        let schedule = todaySchedule()
        let todayTaken = logs.filter { $0.dateString == today && $0.status == .taken }
        let allTaken = schedule.allSatisfy { med in todayTaken.contains { $0.medicationId == med.id } }

        if allTaken && !schedule.isEmpty {
            streak += 1
            bestStreak = max(streak, bestStreak)

            if streak >= 7 && !achievements.contains(.weekStreak) {
                achievements.append(.weekStreak)
            }
            if streak >= 30 && !achievements.contains(.monthStreak) {
                achievements.append(.monthStreak)
            }
        }
    }

    // MARK: - Stats
    func weeklyStats() -> [DayStat] {
        let calendar = Calendar.current
        let today = Date()
        let activeMeds = medications.filter(\.isActive)
        let total = max(activeMeds.count, 1)

        return (0..<7).reversed().map { daysAgo in
            let date = calendar.date(byAdding: .day, value: -daysAgo, to: today)!
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd"
            let dateStr = f.string(from: date)
            let taken = logs.filter { $0.dateString == dateStr && $0.status == .taken }.count

            let dayLabel: String
            let shortF = DateFormatter()
            shortF.dateFormat = "EEE"
            dayLabel = shortF.string(from: date)

            return DayStat(date: date, dayLabel: dayLabel, taken: taken, total: total)
        }
    }

    var totalTaken: Int {
        logs.filter { $0.status == .taken }.count
    }

    var overallAdherence: Int {
        guard !logs.isEmpty else { return 100 }
        let taken = logs.filter { $0.status == .taken }.count
        return Int(Double(taken) / Double(logs.count) * 100)
    }

    // MARK: - Onboarding
    func completeOnboarding() {
        onboardingDone = true
        save()
    }

    // MARK: - Settings
    func setReminderStyle(_ style: String) {
        reminderStyle = style
        save()
    }

    func clearAllData() {
        medications = []
        logs = []
        streak = 0
        bestStreak = 0
        achievements = []
        reminderStyle = "sassy"
        save()
    }

    // MARK: - Helpers
    private func todayString() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date())
    }
}

// MARK: - Persistence Meta
private struct StoreMeta: Codable {
    let streak: Int
    let bestStreak: Int
    let achievements: [Achievement]
    let onboardingDone: Bool
    let reminderStyle: String
}
