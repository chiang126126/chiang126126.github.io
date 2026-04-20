import SwiftUI

struct DashboardView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme

    @State private var messageKey: String = "neutral_1"
    @State private var messageId = UUID()
    @State private var feedingAnimation = false

    private var greeting: LocalizedStringKey {
        let h = Calendar.current.component(.hour, from: Date())
        if h < 12 { return "greeting_morning" }
        if h < 18 { return "greeting_afternoon" }
        return "greeting_evening"
    }

    private var currentMood: MascotMood {
        if feedingAnimation { return .eating }
        return MascotMood.forAdherence(store.todayAdherence)
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                headerSection

                mascotHeroCard

                HStack(spacing: 10) {
                    todayProgressCard
                    StreakCounter(streak: store.streak)
                }

                if store.todaySchedule().isEmpty {
                    emptyState
                } else {
                    todayFeedList
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgGradient.ignoresSafeArea())
        .onAppear { refreshMessage() }
    }

    // MARK: - Header
    private var headerSection: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Image(systemName: "hand.wave.fill")
                        .font(.system(size: theme.captionSize))
                        .foregroundColor(theme.accentColor)
                    Text(greeting)
                        .font(.system(size: theme.captionSize, weight: .medium, design: .rounded))
                        .foregroundColor(theme.mutedColor)
                }

                Text("app_name")
                    .font(.system(size: theme.titleSize, weight: .heavy, design: .rounded))
                    .foregroundStyle(theme.accentGradient)
            }

            Spacer()
        }
        .padding(.top, 8)
    }

    // MARK: - Mascot Hero Card
    private var mascotHeroCard: some View {
        VStack(spacing: 8) {
            // Speech bubble
            VStack(spacing: 0) {
                HStack {
                    (Text("\u{201C} ") + Text(LocalizedStringKey(messageKey)) + Text(" \u{201D}"))
                        .font(.system(size: theme.bodySize - 1, weight: .medium, design: .rounded))
                        .foregroundColor(theme.textColor)
                        .multilineTextAlignment(.center)
                        .lineSpacing(3)
                        .id(messageId)
                        .transition(.opacity.combined(with: .scale(scale: 0.95)))

                    Button {
                        withAnimation(.spring(response: 0.3)) { refreshMessage() }
                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(theme.mutedColor)
                            .padding(5)
                            .background(theme.surfaceColor.opacity(0.6), in: Circle())
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .card3D(theme, radius: 16)

                BubbleTail()
                    .fill(theme.cardColor)
                    .frame(width: 14, height: 8)
                    .overlay {
                        BubbleTail()
                            .stroke(theme.borderColor, lineWidth: 1)
                    }
            }

            // Mascot
            MascotView(
                mood: currentMood,
                size: 130,
                showBackground: true
            )
            .animation(.spring(response: 0.4), value: feedingAnimation)

            // Status line
            HStack(spacing: 8) {
                Image(systemName: "bolt.fill")
                    .font(.system(size: 10))
                    .foregroundColor(currentMood.accentColor)
                Text("\(store.totalXP)")
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .foregroundColor(theme.textColor)
                Text("\u{00B7}")
                    .foregroundColor(theme.mutedColor)
                Text(LocalizedStringKey(currentMood.statusKey))
                    .font(.system(size: 13, weight: .medium, design: .rounded))
                    .foregroundColor(currentMood.accentColor)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 6)
            .background(theme.surfaceColor.opacity(0.6), in: Capsule())
        }
        .padding(.vertical, 8)
    }

    // MARK: - Today Progress
    private var todayProgressCard: some View {
        let schedule = store.todaySchedule()
        let done = schedule.filter { store.isTakenToday($0.id) || store.isSkippedToday($0.id) }.count
        let total = max(schedule.count, 1)
        let progress = Double(done) / Double(total)

        return HStack(spacing: 12) {
            ZStack {
                Circle()
                    .stroke(theme.borderColor, lineWidth: 4)
                    .frame(width: 44, height: 44)
                Circle()
                    .trim(from: 0, to: progress)
                    .stroke(theme.accentColor, style: StrokeStyle(lineWidth: 4, lineCap: .round))
                    .frame(width: 44, height: 44)
                    .rotationEffect(.degrees(-90))
                    .animation(.spring(response: 0.6), value: progress)
                Text("\(done)/\(schedule.count)")
                    .font(.system(size: 11, weight: .bold, design: .rounded))
                    .foregroundColor(theme.textColor)
            }

            VStack(alignment: .leading, spacing: 2) {
                Text("today_schedule")
                    .font(.system(size: theme.captionSize, weight: .medium, design: .rounded))
                    .foregroundColor(theme.mutedColor)
                if done == schedule.count && schedule.count > 0 {
                    HStack(spacing: 4) {
                        Text("tummy_full")
                        Image(systemName: "sparkles")
                            .foregroundColor(theme.accentSecondary)
                    }
                    .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                    .foregroundColor(theme.successColor)
                } else {
                    Text("feed_remaining \(store.todayRemaining)")
                        .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                        .foregroundColor(theme.textColor)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .card3D(theme)
    }

    // MARK: - Feed List
    private var todayFeedList: some View {
        VStack(spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "list.bullet.clipboard.fill")
                    .foregroundColor(theme.accentColor)
                Text("feed_list")
                    .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                    .foregroundColor(theme.textColor)
                Spacer()
            }
            .padding(.horizontal, 4)

            ForEach(Array(store.todaySchedule().enumerated()), id: \.element.id) { _, med in
                let taken = store.isTakenToday(med.id)
                let skipped = store.isSkippedToday(med.id)

                HStack(spacing: 14) {
                    BubblePopButton(
                        medication: med,
                        isTaken: taken,
                        isSkipped: skipped,
                        onTake: {
                            store.logDose(med.id, status: .taken)
                            triggerFeedingAnimation()
                        },
                        onSkip: { store.logDose(med.id, status: .skipped) }
                    )

                    VStack(alignment: .leading, spacing: 2) {
                        Text(med.name)
                            .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                            .foregroundColor(theme.textColor)
                            .lineLimit(1)

                        HStack(spacing: 4) {
                            Text(med.dosage)
                            Text("\u{00B7}")
                            Image(systemName: med.timeOfDay.icon)
                                .font(.system(size: theme.captionSize - 1))
                            Text(LocalizedStringKey(med.timeOfDay.localizationKey))
                        }
                        .font(.system(size: theme.captionSize, design: .rounded))
                        .foregroundColor(theme.mutedColor)
                    }

                    Spacer()

                    if taken {
                        HStack(spacing: 3) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 12))
                            Text("status_fed")
                        }
                        .font(.system(size: 11, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.successColor)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(theme.successColor.opacity(0.14), in: Capsule())
                        .transition(.scale.combined(with: .opacity))
                    } else if skipped {
                        Text("feed_skip")
                            .font(.system(size: 11, weight: .medium, design: .rounded))
                            .foregroundColor(theme.mutedColor)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(theme.mutedColor.opacity(0.12), in: Capsule())
                    } else {
                        HStack(spacing: 3) {
                            Image(systemName: "hand.tap.fill")
                                .font(.system(size: 10))
                            Text("feed_now")
                        }
                        .font(.system(size: 11, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.accentColor)
                    }
                }
                .padding(14)
                .card3D(theme)
                .opacity(taken ? 0.7 : 1)
                .animation(.spring(response: 0.3), value: taken)
            }
        }
    }

    // MARK: - Empty State
    private var emptyState: some View {
        VStack(spacing: 14) {
            MascotView(mood: .sleepy, size: 110)

            Text("no_meds")
                .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                .foregroundColor(theme.textColor)

            Text("add_first")
                .font(.system(size: theme.captionSize, design: .rounded))
                .foregroundColor(theme.mutedColor)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 16)
        }
        .padding(.vertical, 40)
        .frame(maxWidth: .infinity)
        .background {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(theme.cardColor.opacity(0.7))
                .overlay {
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .strokeBorder(theme.borderColor, style: StrokeStyle(lineWidth: 1.5, dash: [8]))
                }
        }
    }

    // MARK: - Helpers
    private func refreshMessage() {
        let keys = reminderKeys(style: store.reminderStyle)
        messageKey = keys.randomElement() ?? "neutral_1"
        messageId = UUID()
    }

    private func reminderKeys(style: String) -> [String] {
        switch style {
        case "sassy": return (1...8).map { "sassy_\($0)" }
        case "gentle": return (1...5).map { "gentle_\($0)" }
        default: return ["neutral_1"]
        }
    }

    private func triggerFeedingAnimation() {
        withAnimation(.spring(response: 0.3)) { feedingAnimation = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            withAnimation(.spring(response: 0.4)) { feedingAnimation = false }
        }
    }
}
