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
                // Header
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 4) {
                            Text(greeting)
                                .font(.system(size: theme.bodySize))
                                .foregroundColor(theme.mutedColor)
                            Text("👋")
                        }
                        Text("app_name")
                            .font(.system(size: theme.titleSize, weight: .bold))
                            .foregroundStyle(theme.accentGradient)
                    }
                    Spacer()
                    MoodAvatar(adherence: store.todayAdherence, size: .small)
                }
                .padding(.top, 8)

                // Streak + Status
                HStack(spacing: 10) {
                    StreakCounter(streak: store.streak)
                    statusCard
                }

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

    // MARK: - Status Card
    private var statusCard: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("today_schedule")
                .font(.system(size: theme.captionSize))
                .foregroundColor(theme.mutedColor)

            Group {
                if store.todayRemaining == 0 {
                    Text("all_done") + Text(" ✨")
                } else {
                    Text("remaining \(store.todayRemaining)")
                }
            }
            .font(.system(size: theme.bodySize - 1, weight: .semibold))
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
            ForEach(Array(store.todaySchedule().enumerated()), id: \.element.id) { index, med in
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
                            .font(.system(size: theme.bodySize, weight: .semibold))
                            .foregroundColor(theme.textColor)
                            .lineLimit(1)

                        HStack(spacing: 4) {
                            Text(med.dosage)
                            Text("·")
                            Text(LocalizedStringKey(med.timeOfDay.localizationKey))
                        }
                        .font(.system(size: theme.captionSize))
                        .foregroundColor(theme.mutedColor)

                        Text(LocalizedStringKey(med.foodRelation.localizationKey))
                            .font(.system(size: theme.captionSize - 1))
                            .foregroundColor(theme.mutedColor.opacity(0.7))
                    }

                    Spacer()

                    // Status
                    if taken {
                        Text("status_taken ✓")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(theme.successColor)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(theme.successColor.opacity(0.1), in: Capsule())
                            .transition(.scale)
                    } else if skipped {
                        Text("status_skipped")
                            .font(.system(size: 11))
                            .foregroundColor(theme.mutedColor)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(theme.mutedColor.opacity(0.1), in: Capsule())
                    } else {
                        Text("take_now")
                            .font(.system(size: 11, weight: .semibold))
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
        VStack(spacing: 12) {
            Text("💊")
                .font(.system(size: 56))
                .phaseAnimator([false, true]) { content, phase in
                    content.offset(y: phase ? -8 : 0)
                } animation: { _ in .easeInOut(duration: 1.5).repeatForever(autoreverses: true) }

            Text("no_meds")
                .font(.system(size: theme.bodySize, weight: .semibold))
                .foregroundColor(theme.textColor)

            Text("add_first")
                .font(.system(size: theme.captionSize))
                .foregroundColor(theme.mutedColor)
                .multilineTextAlignment(.center)
        }
        .padding(.vertical, 60)
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
                Image(systemName: "sparkles")
                    .foregroundColor(theme.accentColor)
                Text("weekly_progress")
                    .font(.system(size: theme.bodySize, weight: .semibold))
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
