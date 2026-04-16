import SwiftUI

struct DashboardView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme

    private var greeting: LocalizedStringKey {
        let h = Calendar.current.component(.hour, from: Date())
        if h < 12 { return "greeting_morning" }
        if h < 18 { return "greeting_afternoon" }
        return "greeting_evening"
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                // Header with level
                headerSection

                // XP Progress
                XPProgressView(
                    currentXP: store.totalXP,
                    currentLevel: store.currentLevel,
                    progress: store.xpProgress,
                    xpToNext: store.xpToNext
                )

                // Streak + Status
                HStack(spacing: 10) {
                    StreakCounter(streak: store.streak)
                    statusCard
                }

                // Daily Missions
                DailyMissionCard(missions: store.dailyMissions())

                // Reminder
                if store.todayRemaining > 0 {
                    ReminderCard()
                }

                // Medications list
                if store.todaySchedule().isEmpty {
                    emptyState
                } else {
                    todayMedsList
                }

                // Weekly chart
                if !store.todaySchedule().isEmpty {
                    weeklySection
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgColor.ignoresSafeArea())
    }

    // MARK: - Header
    private var headerSection: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(greeting)
                        .font(.system(size: theme.bodySize, design: .rounded))
                        .foregroundColor(theme.mutedColor)
                    Text(Emoji.wave)
                        .font(.system(size: theme.bodySize))
                }
                HStack(spacing: 6) {
                    Text("app_name")
                        .font(.system(size: theme.titleSize, weight: .bold, design: .rounded))
                        .foregroundStyle(theme.accentGradient)
                    Text(Emoji.pill)
                        .font(.system(size: theme.titleSize - 4))
                }
            }
            Spacer()
            MoodAvatar(adherence: store.todayAdherence, size: .small)
        }
        .padding(.top, 8)
    }

    // MARK: - Status Card
    private var statusCard: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("today_schedule")
                .font(.system(size: theme.captionSize, design: .rounded))
                .foregroundColor(theme.mutedColor)

            Group {
                if store.todayRemaining == 0 {
                    HStack(spacing: 4) {
                        Text("all_done")
                        Text(Emoji.sparkles)
                    }
                } else {
                    Text("remaining \(store.todayRemaining)")
                }
            }
            .font(.system(size: theme.bodySize - 1, weight: .semibold, design: .rounded))
            .foregroundColor(theme.textColor)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(theme.cardColor)
                .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
        }
    }

    // MARK: - Today's Meds
    private var todayMedsList: some View {
        VStack(spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "list.bullet.clipboard.fill")
                    .foregroundColor(theme.accentColor)
                Text("today_schedule")
                    .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                    .foregroundColor(theme.textColor)
                Spacer()
            }

            ForEach(Array(store.todaySchedule().enumerated()), id: \.element.id) { _, med in
                let taken = store.isTakenToday(med.id)
                let skipped = store.isSkippedToday(med.id)

                HStack(spacing: 14) {
                    BubblePopButton(
                        medication: med,
                        isTaken: taken,
                        isSkipped: skipped,
                        onTake: { store.logDose(med.id, status: .taken) },
                        onSkip: { store.logDose(med.id, status: .skipped) }
                    )

                    VStack(alignment: .leading, spacing: 2) {
                        Text(med.name)
                            .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                            .foregroundColor(theme.textColor)
                            .lineLimit(1)

                        HStack(spacing: 4) {
                            Text(med.dosage)
                            Text("\u{00B7}")
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
                            Text("status_taken")
                        }
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                        .foregroundColor(theme.successColor)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(theme.successColor.opacity(0.12), in: Capsule())
                        .transition(.scale.combined(with: .opacity))
                    } else if skipped {
                        Text("status_skipped")
                            .font(.system(size: 11, design: .rounded))
                            .foregroundColor(theme.mutedColor)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(theme.mutedColor.opacity(0.1), in: Capsule())
                    } else {
                        HStack(spacing: 3) {
                            Image(systemName: "hand.tap.fill")
                                .font(.system(size: 10))
                            Text("take_now")
                        }
                        .font(.system(size: 11, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.accentColor)
                    }
                }
                .padding(14)
                .background {
                    RoundedRectangle(cornerRadius: 16)
                        .fill(theme.cardColor)
                        .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
                }
                .opacity(taken ? 0.6 : 1)
                .animation(.spring(response: 0.3), value: taken)
            }
        }
    }

    // MARK: - Empty State
    private var emptyState: some View {
        VStack(spacing: 14) {
            Image(systemName: "pill.fill")
                .font(.system(size: 48))
                .foregroundStyle(theme.accentGradient)
                .phaseAnimator([false, true]) { content, phase in
                    content.offset(y: phase ? -10 : 0)
                } animation: { _ in .easeInOut(duration: 1.5).repeatForever(autoreverses: true) }

            Text(Emoji.pill)
                .font(.system(size: 36))

            Text("no_meds")
                .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                .foregroundColor(theme.textColor)

            Text("add_first")
                .font(.system(size: theme.captionSize, design: .rounded))
                .foregroundColor(theme.mutedColor)
                .multilineTextAlignment(.center)
        }
        .padding(.vertical, 50)
        .frame(maxWidth: .infinity)
        .background {
            RoundedRectangle(cornerRadius: 24)
                .strokeBorder(theme.borderColor, style: StrokeStyle(lineWidth: 1.5, dash: [8]))
        }
    }

    // MARK: - Weekly Chart
    private var weeklySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "chart.bar.fill")
                    .foregroundColor(theme.accentColor)
                Text("weekly_progress")
                    .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                    .foregroundColor(theme.textColor)
            }
            WeeklyChartView(data: store.weeklyStats())
        }
        .padding(16)
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(theme.cardColor)
                .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
        }
    }
}
