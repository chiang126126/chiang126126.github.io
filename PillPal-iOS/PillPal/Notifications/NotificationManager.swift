import Foundation
import UserNotifications

final class NotificationManager {
    static let shared = NotificationManager()
    private init() {}

    // MARK: - Permission

    func requestPermission() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional: return true
        case .notDetermined:
            return (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        default: return false
        }
    }

    // MARK: - Schedule All

    /// Cancels every pending notification and re-schedules from scratch.
    /// Call this when reminder style or language changes.
    func rescheduleAll(medications: [Medication], style: String, language: String) {
        UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
        for med in medications where med.isActive && med.frequency != .asNeeded {
            schedule(for: med, style: style, language: language)
        }
    }

    // MARK: - Schedule Single

    func schedule(for medication: Medication, style: String, language: String) {
        guard medication.isActive, medication.frequency != .asNeeded else { return }

        let times = reminderTimes(for: medication)
        for (index, time) in times.enumerated() {
            let content = UNMutableNotificationContent()
            content.title = appDisplayName(language: language)
            content.body = randomBody(style: style, language: language, medName: medication.name)
            content.sound = .default
            content.userInfo = ["medicationId": medication.id.uuidString]

            var components = Calendar.current.dateComponents([.hour, .minute], from: time)
            if medication.frequency == .weekly {
                // weekDay stored as 0=Sun…6=Sat; UNCalendar uses 1=Sun…7=Sat
                components.weekday = (medication.weekDay ?? 0) + 1
            }

            let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: true)
            let id = notificationID(medicationId: medication.id, index: index)
            let request = UNNotificationRequest(identifier: id, content: content, trigger: trigger)
            UNUserNotificationCenter.current().add(request, withCompletionHandler: nil)
        }
    }

    // MARK: - Cancel

    func cancelPending(for medicationId: UUID) {
        let ids = (0..<2).map { notificationID(medicationId: medicationId, index: $0) }
        UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: ids)
    }

    func removeDelivered(for medicationId: UUID) {
        let ids = (0..<2).map { notificationID(medicationId: medicationId, index: $0) }
        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: ids)
    }

    // MARK: - Helpers

    private func notificationID(medicationId: UUID, index: Int) -> String {
        "\(medicationId.uuidString)-\(index)"
    }

    private func reminderTimes(for med: Medication) -> [Date] {
        let primary = med.effectiveReminderTime
        if med.frequency == .twiceDaily {
            let second = Calendar.current.date(byAdding: .hour, value: 8, to: primary) ?? primary
            return [primary, second]
        }
        return [primary]
    }

    private func appDisplayName(language: String) -> String {
        language == "zh-Hans" ? "药搭子" : "PillPal"
    }

    private func randomBody(style: String, language: String, medName: String) -> String {
        let pool = messagePool(style: style, language: language)
        let template = pool.randomElement() ?? "%@"
        return template.replacingOccurrences(of: "%@", with: medName)
    }

    // MARK: - Message Pools

    private func messagePool(style: String, language: String) -> [String] {
        switch language {
        case "zh-Hans":
            switch style {
            case "sassy":
                return [
                    "再不吃 %@，吞吞就要靠光合作用了",
                    "%@ 还没吃！吞吞在敲碗啦",
                    "你的 %@ 发来消息：已读不回算什么？",
                    "吞吞用卡姿兰大眼睛盯着你的 %@",
                    "快去喂吞吞！%@ 在等你呢",
                ]
            case "gentle":
                return [
                    "该吃 %@ 了，吞吞陪着你~",
                    "温柔提醒：%@ 到时间了",
                    "一颗 %@，今天的自律达成",
                    "健康是最温柔的自律，别忘了 %@",
                ]
            default:
                return ["投喂时间到！%@ 等你呢", "提醒：该吃 %@ 了"]
            }

        case "fr":
            switch style {
            case "sassy":
                return [
                    "%@ t'attend. Tonny est impatient.",
                    "Tonny te fixe avec ses grands yeux. Où est %@ ?",
                    "%@ : Tonny fait la tête depuis tout à l'heure.",
                    "Allez, %@ ne va pas se prendre tout seul !",
                ]
            case "gentle":
                return [
                    "Petit rappel tout doux : c'est l'heure de %@.",
                    "Tonny te rappelle doucement : pense à %@.",
                    "Un peu d'amour pour toi — n'oublie pas %@.",
                ]
            default:
                return ["C'est l'heure de %@ !", "Rappel : %@"]
            }

        default: // "en"
            switch style {
            case "sassy":
                return [
                    "Your %@ is giving you the silent treatment rn.",
                    "Tunny is staring at you. Take your %@.",
                    "%@ won't take itself, bestie.",
                    "Guilt trip activated. Where's your %@?",
                    "Your wellness KPI is not gonna hit itself. Take %@.",
                ]
            case "gentle":
                return [
                    "Time for %@. Tunny's rooting for you 🌱",
                    "A gentle nudge — don't forget %@.",
                    "One small act of self-love: take your %@.",
                    "Hey, %@ is waiting. Tunny believes in you.",
                ]
            default:
                return ["Time to take %@!", "Reminder: %@"]
            }
        }
    }
}
