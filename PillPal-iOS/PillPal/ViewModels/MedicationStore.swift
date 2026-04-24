import Foundation
import SwiftUI
import UserNotifications

@Observable
final class MedicationStore {
    // MARK: - Persisted Data
    var medications: [Medication] = []
    var logs: [DoseLog] = []
    var streak: Int = 0
    var bestStreak: Int = 0
    var achievements: [Achievement] = []
    var onboardingDone: Bool = false
    var reminderStyle: String = "sassy"
    var totalXP: Int = 0
    var lastCheckInDate: String = ""
    var appLanguage: String = "en"

    // MARK: - Transient UI State
    var showLevelUp: Bool = false
    var levelUpTo: Int = 0
    var recentXPGain: Int = 0
    var showXPPopup: Bool = false

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
            totalXP = meta.totalXP ?? 0
            lastCheckInDate = meta.lastCheckInDate ?? ""
            appLanguage = meta.appLanguage ?? "en"
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
            reminderStyle: reminderStyle,
            totalXP: totalXP, lastCheckInDate: lastCheckInDate,
            appLanguage: appLanguage
        )
        if let data = try? JSONEncoder().encode(meta) {
            UserDefaults.standard.set(data, forKey: metaKey)
        }
    }

    // MARK: - XP & Leveling
    var currentLevel: GameLevel { GameLevel.forXP(totalXP) }

    var nextLevel: GameLevel? { GameLevel.nextAfter(currentLevel) }

    var xpProgress: Double {
        guard let next = nextLevel else { return 1.0 }
        let current = currentLevel
        let xpInLevel = totalXP - current.xpRequired
        let xpNeeded = next.xpRequired - current.xpRequired
        return Double(xpInLevel) / Double(xpNeeded)
    }

    var xpToNext: Int {
        guard let next = nextLevel else { return 0 }
        return next.xpRequired - totalXP
    }

    func awardXP(_ amount: Int) {
        let oldLevel = currentLevel
        totalXP += amount
        let newLevel = currentLevel

        recentXPGain = amount
        showXPPopup = true

        if newLevel.level > oldLevel.level {
            levelUpTo = newLevel.level
            showLevelUp = true
            checkLevelAchievements()
        }

        checkDoseAchievements()
        save()
    }

    // MARK: - Daily Check-In
    var hasCheckedInToday: Bool {
        lastCheckInDate == todayString()
    }

    func performDailyCheckIn() {
        guard !hasCheckedInToday else { return }
        lastCheckInDate = todayString()
        awardXP(XPReward.dailyCheckIn)
        save()
    }

    // MARK: - Daily Missions
    func dailyMissions() -> [DailyMission] {
        let schedule = todaySchedule()
        let morningMeds = schedule.filter { $0.timeOfDay == .morning }
        let allMorningTaken = !morningMeds.isEmpty && morningMeds.allSatisfy { isTakenToday($0.id) }
        let anyTaken = schedule.contains { isTakenToday($0.id) }
        let allTaken = !schedule.isEmpty && schedule.allSatisfy { isTakenToday($0.id) }
        let noSkips = anyTaken && !schedule.contains { isSkippedToday($0.id) }

        return [
            DailyMission(id: "checkin", titleKey: "mission_checkin", icon: "hand.wave.fill", xpReward: XPReward.dailyCheckIn, isCompleted: hasCheckedInToday),
            DailyMission(id: "morning", titleKey: "mission_morning", icon: "sunrise.fill", xpReward: 20, isCompleted: allMorningTaken),
            DailyMission(id: "no_skip", titleKey: "mission_no_skip", icon: "shield.fill", xpReward: 30, isCompleted: noSkips),
            DailyMission(id: "all_done", titleKey: "mission_all_done", icon: "checkmark.seal.fill", xpReward: XPReward.completeAllDaily, isCompleted: allTaken),
        ]
    }

    var completedMissionsCount: Int {
        dailyMissions().filter(\.isCompleted).count
    }

    // MARK: - Medication CRUD
    func addMedication(_ med: Medication) {
        var newMed = med
        newMed.createdAt = Date()
        medications.append(newMed)

        if !achievements.contains(.firstPill) {
            unlockAchievement(.firstPill)
        }
        if !achievements.contains(.pillCollector) && medications.filter(\.isActive).count >= 5 {
            unlockAchievement(.pillCollector)
        }
        awardXP(XPReward.addMedication)
        NotificationManager.shared.schedule(for: newMed, style: reminderStyle, language: appLanguage)
        save()
    }

    func updateMedication(_ id: UUID, with changes: (inout Medication) -> Void) {
        if let idx = medications.firstIndex(where: { $0.id == id }) {
            changes(&medications[idx])
            NotificationManager.shared.cancelPending(for: id)
            if medications[idx].isActive {
                NotificationManager.shared.schedule(for: medications[idx], style: reminderStyle, language: appLanguage)
            }
            save()
        }
    }

    func deleteMedication(_ id: UUID) {
        NotificationManager.shared.cancelPending(for: id)
        medications.removeAll { $0.id == id }
        logs.removeAll { $0.medicationId == id }
        save()
    }

    func toggleActive(_ id: UUID) {
        if let idx = medications.firstIndex(where: { $0.id == id }) {
            medications[idx].isActive.toggle()
            if medications[idx].isActive {
                NotificationManager.shared.schedule(for: medications[idx], style: reminderStyle, language: appLanguage)
            } else {
                NotificationManager.shared.cancelPending(for: id)
            }
            save()
        }
    }

    // MARK: - Dose Logging
    func logDose(_ medicationId: UUID, status: DoseStatus) {
        let log = DoseLog(medicationId: medicationId, status: status, timestamp: Date())
        logs.append(log)

        if status == .taken {
            awardXP(XPReward.takeDose)
            NotificationManager.shared.removeDelivered(for: medicationId)
        }

        recalculateStreak()
        checkLifestyleAchievements()

        // Check if all daily done
        let schedule = todaySchedule()
        if !schedule.isEmpty && schedule.allSatisfy({ isTakenToday($0.id) }) {
            awardXP(XPReward.completeAllDaily)
        }

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
                unlockAchievement(.weekStreak)
                awardXP(XPReward.streak7)
            }
            if streak >= 30 && !achievements.contains(.monthStreak) {
                unlockAchievement(.monthStreak)
                awardXP(XPReward.streak30)
            }
            if streak >= 90 && !achievements.contains(.quarterStreak) {
                unlockAchievement(.quarterStreak)
            }
        }
    }

    // MARK: - Achievements
    private func unlockAchievement(_ achievement: Achievement) {
        guard !achievements.contains(achievement) else { return }
        achievements.append(achievement)
        save()
    }

    private func checkDoseAchievements() {
        let taken = totalTaken
        if taken >= 10 && !achievements.contains(.tenDoses) {
            unlockAchievement(.tenDoses)
        }
        if taken >= 50 && !achievements.contains(.fiftyDoses) {
            unlockAchievement(.fiftyDoses)
        }
        if taken >= 100 && !achievements.contains(.hundredDoses) {
            unlockAchievement(.hundredDoses)
        }
        if taken >= 500 && !achievements.contains(.fiveHundredDoses) {
            unlockAchievement(.fiveHundredDoses)
        }
    }

    private func checkLevelAchievements() {
        let lvl = currentLevel.level
        if lvl >= 5 && !achievements.contains(.level5) {
            unlockAchievement(.level5)
        }
        if lvl >= 10 && !achievements.contains(.level10) {
            unlockAchievement(.level10)
        }
    }

    private func checkLifestyleAchievements() {
        // Morning Person: taken morning meds on 7+ different days
        if !achievements.contains(.morningPerson) {
            let morningMedIds = Set(medications.filter { $0.timeOfDay == .morning && $0.isActive }.map(\.id))
            if !morningMedIds.isEmpty {
                let morningLogs = logs.filter { morningMedIds.contains($0.medicationId) && $0.status == .taken }
                let uniqueDays = Set(morningLogs.map(\.dateString))
                if uniqueDays.count >= 7 { unlockAchievement(.morningPerson) }
            }
        }

        // Night Owl: taken bedtime meds on 7+ different days
        if !achievements.contains(.nightOwl) {
            let bedtimeMedIds = Set(medications.filter { $0.timeOfDay == .bedtime && $0.isActive }.map(\.id))
            if !bedtimeMedIds.isEmpty {
                let bedtimeLogs = logs.filter { bedtimeMedIds.contains($0.medicationId) && $0.status == .taken }
                let uniqueDays = Set(bedtimeLogs.map(\.dateString))
                if uniqueDays.count >= 7 { unlockAchievement(.nightOwl) }
            }
        }

        // Pill Collector: 5+ active medications
        if !achievements.contains(.pillCollector) {
            if medications.filter(\.isActive).count >= 5 {
                unlockAchievement(.pillCollector)
            }
        }

        // Weekend Warrior: taken meds on a weekend day
        if !achievements.contains(.weekendWarrior) {
            let weekday = Calendar.current.component(.weekday, from: Date())
            if (weekday == 1 || weekday == 7) && logs.contains(where: { $0.dateString == todayString() && $0.status == .taken }) {
                unlockAchievement(.weekendWarrior)
            }
        }

        // YOLO Casual: 30%+ skip rate over 30+ logged doses (anti-shame badge)
        if !achievements.contains(.yoloCasual) {
            if logs.count >= 30 {
                let skipped = logs.filter { $0.status == .skipped }.count
                if Double(skipped) / Double(logs.count) >= 0.3 {
                    unlockAchievement(.yoloCasual)
                }
            }
        }

        // Comeback Kid: logged a dose after 3+ day gap
        if !achievements.contains(.comebackKid) {
            let takenDates = Set(logs.filter { $0.status == .taken }.map(\.dateString)).sorted()
            if takenDates.count >= 2 {
                let df = DateFormatter()
                df.dateFormat = "yyyy-MM-dd"
                for i in 1..<takenDates.count {
                    if let d1 = df.date(from: takenDates[i - 1]),
                       let d2 = df.date(from: takenDates[i]),
                       Calendar.current.dateComponents([.day], from: d1, to: d2).day ?? 0 >= 3 {
                        unlockAchievement(.comebackKid)
                        break
                    }
                }
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

            let shortF = DateFormatter()
            shortF.dateFormat = "EEE"
            let dayLabel = shortF.string(from: date)

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
        NotificationManager.shared.rescheduleAll(medications: medications, style: style, language: appLanguage)
        save()
    }

    func setAppLanguage(_ lang: String) {
        appLanguage = lang
        UserDefaults.standard.set([lang], forKey: "AppleLanguages")
        UserDefaults.standard.synchronize()
        NotificationManager.shared.rescheduleAll(medications: medications, style: reminderStyle, language: lang)
        save()
    }

    func clearAllData() {
        medications = []
        logs = []
        streak = 0
        bestStreak = 0
        achievements = []
        totalXP = 0
        lastCheckInDate = ""
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
    var totalXP: Int?
    var lastCheckInDate: String?
    var appLanguage: String?
}
