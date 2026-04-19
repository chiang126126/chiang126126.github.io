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
                headerSection

                HStack(spacing: 10) {
                    todayProgressCard
                    StreakCounter(streak: store.streak)
                }

                if store.todayRemaining > 0 {
                    ReminderCard()
                }

                if store.todaySchedule().isEmpty {
                    emptyState
                } else {
                    todayMedsList
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgGradient.ignoresSafeArea())
    }

    // MARK: - Header
    private var headerSection: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 4) {
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

            MascotView(
                mood: MascotMood.forAdherence(store.todayAdherence),
                size: 60,
                showBackground: false
            )
        }
        .padding(.top, 8)
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
                        Text("all_done")
                        Image(systemName: "sparkles")
                            .foregroundColor(theme.accentSecondary)
                    }
                    .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                    .foregroundColor(theme.successColor)
                } else {
                    Text("remaining \(store.todayRemaining)")
                        .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                        .foregroundColor(theme.textColor)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(theme.cardColor)
                .overlay { RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(theme.borderColor, lineWidth: 1) }
        }
    }

    // MARK: - Today's Meds
    private var todayMedsList: some View {
        VStack(spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "list.bullet.clipboard.fill")
                    .foregroundColor(theme.accentColor)
                Text("today_schedule")
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
                        onTake: { store.logDose(med.id, status: .taken) },
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
                            Text("status_taken")
                        }
                        .font(.system(size: 11, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.successColor)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(theme.successColor.opacity(0.14), in: Capsule())
                        .transition(.scale.combined(with: .opacity))
                    } else if skipped {
                        Text("status_skipped")
                            .font(.system(size: 11, weight: .medium, design: .rounded))
                            .foregroundColor(theme.mutedColor)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(theme.mutedColor.opacity(0.12), in: Capsule())
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
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .fill(theme.cardColor)
                        .overlay { RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(theme.borderColor, lineWidth: 1) }
                        .shadow(color: Color.black.opacity(0.04), radius: 8, y: 3)
                }
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
}
