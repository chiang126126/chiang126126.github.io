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
                heroSection

                XPProgressView(
                    currentXP: store.totalXP,
                    currentLevel: store.currentLevel,
                    progress: store.xpProgress,
                    xpToNext: store.xpToNext
                )

                HStack(spacing: 10) {
                    StreakCounter(streak: store.streak)
                    statusCard
                }

                DailyMissionCard(missions: store.dailyMissions())

                if store.todayRemaining > 0 {
                    ReminderCard()
                }

                if store.todaySchedule().isEmpty {
                    emptyState
                } else {
                    todayMedsList
                }

                if !store.todaySchedule().isEmpty {
                    weeklySection
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgGradient.ignoresSafeArea())
    }

    // MARK: - Hero
    private var heroSection: some View {
        ZStack(alignment: .topLeading) {
            // Pastel blob background
            RoundedRectangle(cornerRadius: 32, style: .continuous)
                .fill(theme.heroGradient)
                .overlay(alignment: .topTrailing) {
                    Circle()
                        .fill(theme.pastelPink.opacity(0.6))
                        .frame(width: 120, height: 120)
                        .blur(radius: 18)
                        .offset(x: 30, y: -30)
                }
                .overlay(alignment: .bottomLeading) {
                    Circle()
                        .fill(theme.pastelSky.opacity(0.7))
                        .frame(width: 90, height: 90)
                        .blur(radius: 16)
                        .offset(x: -20, y: 20)
                }
                .overlay {
                    RoundedRectangle(cornerRadius: 32, style: .continuous)
                        .stroke(Color.white.opacity(theme.isPro ? 0.08 : 0.6), lineWidth: 1)
                }

            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 6) {
                        Image(systemName: "hand.wave.fill")
                            .font(.system(size: theme.bodySize - 1))
                            .foregroundColor(theme.accentColor)
                        Text(greeting)
                            .font(.system(size: theme.bodySize, weight: .medium, design: .rounded))
                            .foregroundColor(theme.textColor.opacity(0.7))
                    }

                    Text("app_name")
                        .font(.system(size: theme.titleSize + 2, weight: .heavy, design: .rounded))
                        .foregroundStyle(theme.accentGradient)

                    Text("app_tagline")
                        .font(.system(size: theme.captionSize + 1, weight: .medium, design: .rounded))
                        .foregroundColor(theme.textColor.opacity(0.65))
                        .lineLimit(2)
                }

                Spacer(minLength: 0)

                MascotView(
                    mood: MascotMood.forAdherence(store.todayAdherence),
                    size: 84,
                    showBackground: true
                )
            }
            .padding(20)
        }
        .padding(.top, 4)
    }

    // MARK: - Status Card
    private var statusCard: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Image(systemName: "calendar")
                    .font(.system(size: theme.captionSize))
                    .foregroundColor(theme.accentColor)
                Text("today_schedule")
                    .font(.system(size: theme.captionSize, weight: .medium, design: .rounded))
                    .foregroundColor(theme.mutedColor)
            }

            Group {
                if store.todayRemaining == 0 {
                    HStack(spacing: 4) {
                        Text("all_done")
                        Image(systemName: "sparkles")
                            .foregroundColor(theme.accentSecondary)
                    }
                } else {
                    Text("remaining \(store.todayRemaining)")
                }
            }
            .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
            .foregroundColor(theme.textColor)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(theme.cardColor)
                .overlay { RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(theme.borderColor, lineWidth: 1) }
                .shadow(color: Color.black.opacity(0.05), radius: 10, y: 4)
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

    // MARK: - Weekly Chart
    private var weeklySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "chart.bar.fill")
                    .foregroundColor(theme.accentColor)
                Text("weekly_progress")
                    .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                    .foregroundColor(theme.textColor)
            }
            WeeklyChartView(data: store.weeklyStats())
        }
        .padding(16)
        .background {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(theme.cardColor)
                .overlay { RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(theme.borderColor, lineWidth: 1) }
                .shadow(color: Color.black.opacity(0.05), radius: 10, y: 4)
        }
    }
}
