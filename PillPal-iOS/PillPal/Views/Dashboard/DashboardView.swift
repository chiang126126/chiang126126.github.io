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
            VStack(spacing: 0) {
                // Purple hero section with decorations
                heroSection
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 40)
                    .background {
                        ZStack {
                            theme.heroGradient
                            // Floating decorative circles
                            Circle()
                                .fill(Color.white.opacity(0.07))
                                .frame(width: 140, height: 140)
                                .offset(x: -120, y: -20)
                            Circle()
                                .fill(Color.white.opacity(0.05))
                                .frame(width: 200, height: 200)
                                .offset(x: 140, y: 80)
                            Circle()
                                .fill(Color.white.opacity(0.06))
                                .frame(width: 80, height: 80)
                                .offset(x: 60, y: -50)
                            Circle()
                                .fill(Color.white.opacity(0.04))
                                .frame(width: 60, height: 60)
                                .offset(x: -60, y: 100)
                            // Floating pill shapes
                            Capsule()
                                .fill(Color.white.opacity(0.05))
                                .frame(width: 40, height: 18)
                                .rotationEffect(.degrees(30))
                                .offset(x: -130, y: 60)
                            Capsule()
                                .fill(Color.white.opacity(0.04))
                                .frame(width: 30, height: 14)
                                .rotationEffect(.degrees(-25))
                                .offset(x: 120, y: -30)
                            // Star sparkles
                            Image(systemName: "sparkle")
                                .font(.system(size: 14))
                                .foregroundColor(Color.white.opacity(0.15))
                                .offset(x: -80, y: -40)
                            Image(systemName: "sparkle")
                                .font(.system(size: 10))
                                .foregroundColor(Color.white.opacity(0.12))
                                .offset(x: 100, y: 20)
                        }
                    }

                // Content sheet
                VStack(spacing: 14) {
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
                .padding(.top, 24)
                .padding(.bottom, 100)
                .frame(maxWidth: .infinity)
                .background(
                    UnevenRoundedRectangle(
                        topLeadingRadius: 32,
                        bottomLeadingRadius: 0,
                        bottomTrailingRadius: 0,
                        topTrailingRadius: 32,
                        style: .continuous
                    )
                    .fill(theme.bgColor)
                    .offset(y: -24)
                )
                .offset(y: -24)
            }
        }
        .background(theme.heroColor.ignoresSafeArea(edges: .top))
        .background(theme.bgColor.ignoresSafeArea(edges: .bottom))
        .onAppear { refreshMessage() }
    }

    // MARK: - Hero Section
    private var heroSection: some View {
        VStack(spacing: 12) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Image(systemName: "hand.wave.fill")
                            .font(.system(size: theme.captionSize))
                            .foregroundColor(theme.warmYellow)
                        Text(greeting)
                            .font(.system(size: theme.captionSize, weight: .medium, design: .rounded))
                            .foregroundColor(.white.opacity(0.8))
                    }
                    Text("app_name")
                        .font(.system(size: theme.titleSize, weight: .heavy, design: .rounded))
                        .foregroundColor(.white)
                }

                Spacer()

                // Level badge (yellow)
                HStack(spacing: 4) {
                    Image(systemName: "star.fill")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(Color(hex: "#8B6914"))
                    Text("Lv.\(store.currentLevel.level)")
                        .font(.system(size: 13, weight: .heavy, design: .rounded))
                        .foregroundColor(Color(hex: "#8B6914"))
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(theme.warmYellow, in: Capsule())

                // XP badge
                HStack(spacing: 4) {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(theme.warmYellow)
                    Text("\(store.totalXP)")
                        .font(.system(size: 13, weight: .heavy, design: .rounded))
                        .foregroundColor(.white)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.white.opacity(0.2), in: Capsule())
            }

            mascotHeroCard
        }
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
                            .background(theme.surfaceColor, in: Circle())
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background {
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .fill(Color.white)
                        .shadow(color: Color.black.opacity(0.08), radius: 8, y: 3)
                }

                BubbleTail()
                    .fill(Color.white)
                    .frame(width: 14, height: 8)
            }

            // Mascot
            MascotView(mood: currentMood, size: 130, showBackground: true)
                .animation(.spring(response: 0.4), value: feedingAnimation)

            // Status capsule (yellow)
            HStack(spacing: 8) {
                Image(systemName: "sparkles")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(Color(hex: "#222222"))
                Text(LocalizedStringKey(currentMood.statusKey))
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .foregroundColor(Color(hex: "#222222"))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 7)
            .background(theme.warmYellow, in: Capsule())
            .shadow(color: theme.warmYellow.opacity(0.4), radius: 8, y: 2)
        }
    }

    // MARK: - Today Progress (mint green card)
    private var todayProgressCard: some View {
        let schedule = store.todaySchedule()
        let done = schedule.filter { store.isTakenToday($0.id) || store.isSkippedToday($0.id) }.count
        let total = max(schedule.count, 1)
        let progress = Double(done) / Double(total)

        return HStack(spacing: 12) {
            ZStack {
                Circle()
                    .stroke(theme.successColor.opacity(0.2), lineWidth: 4)
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
                            .foregroundColor(theme.warmYellow)
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
        .pastelCard(theme, tint: theme.cardMint, radius: 24)
    }

    // MARK: - Feed List (status-colored cards)
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
                let cardBg = taken ? theme.cardMint : (skipped ? theme.cardPeach : theme.cardLavender)

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
                        .background(theme.successColor.opacity(0.2), in: Capsule())
                        .transition(.scale.combined(with: .opacity))
                    } else if skipped {
                        Text("feed_skip")
                            .font(.system(size: 11, weight: .medium, design: .rounded))
                            .foregroundColor(theme.mutedColor)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(theme.mutedColor.opacity(0.15), in: Capsule())
                    } else {
                        HStack(spacing: 3) {
                            Image(systemName: "hand.tap.fill")
                                .font(.system(size: 10))
                            Text("feed_now")
                        }
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .foregroundColor(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(theme.buttonGradient, in: Capsule())
                        .shadow(color: theme.accentColor.opacity(0.3), radius: 6, y: 2)
                    }
                }
                .padding(14)
                .pastelCard(theme, tint: cardBg, radius: 24)
                .opacity(taken ? 0.75 : 1)
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
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(theme.cardLavender.opacity(0.5))
                .overlay {
                    RoundedRectangle(cornerRadius: 28, style: .continuous)
                        .strokeBorder(theme.accentColor.opacity(0.2), style: StrokeStyle(lineWidth: 1.5, dash: [8]))
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
