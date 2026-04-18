import SwiftUI

struct StatsView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme

    private var stats: [(icon: String, key: LocalizedStringKey, value: String, sub: LocalizedStringKey?, color: Color)] {
        [
            ("chart.line.uptrend.xyaxis", "stat_adherence", "\(store.overallAdherence)%", nil, Color(hex: "#10B981")),
            ("flame.fill", "stat_current_streak", "\(store.streak)", "stat_days", Color(hex: "#FF6B35")),
            ("trophy.fill", "stat_best_streak", "\(store.bestStreak)", "stat_days", Color(hex: "#EAB308")),
            ("checkmark.circle.fill", "stat_total_taken", "\(store.totalTaken)", nil, Color(hex: "#22D3EE")),
        ]
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                // Header
                HStack {
                    Text("stats_title")
                        .font(.system(size: theme.titleSize, weight: .bold, design: .rounded))
                        .foregroundColor(theme.textColor)
                    Spacer()
                    MoodAvatar(adherence: Double(store.overallAdherence), size: .small)
                }
                .padding(.top, 8)

                // Level Progress Card
                levelCard

                // Stats grid
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                    ForEach(Array(stats.enumerated()), id: \.offset) { index, stat in
                        VStack(alignment: .leading, spacing: 8) {
                            HStack(spacing: 6) {
                                Image(systemName: stat.icon)
                                    .font(.system(size: 14))
                                    .foregroundColor(stat.color)
                                    .frame(width: 28, height: 28)
                                    .background(stat.color.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))

                                Text(stat.key)
                                    .font(.system(size: theme.captionSize, design: .rounded))
                                    .foregroundColor(theme.mutedColor)
                                    .lineLimit(1)
                            }

                            HStack(alignment: .firstTextBaseline, spacing: 3) {
                                Text(stat.value)
                                    .font(.system(size: index == 0 ? 36 : 24, weight: .bold, design: .rounded))
                                    .foregroundColor(theme.textColor)
                                    .contentTransition(.numericText())

                                if let sub = stat.sub {
                                    Text(sub)
                                        .font(.system(size: theme.captionSize, design: .rounded))
                                        .foregroundColor(theme.mutedColor)
                                }
                            }
                        }
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background {
                            RoundedRectangle(cornerRadius: 16)
                                .fill(theme.cardColor)
                                .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
                        }
                        .gridCellColumns(index == 0 ? 2 : 1)
                    }
                }

                // XP stats
                xpStatsCard

                // Weekly chart
                VStack(alignment: .leading, spacing: 10) {
                    Text("this_week")
                        .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.textColor)
                    WeeklyChartView(data: store.weeklyStats())
                }
                .padding(16)
                .background {
                    RoundedRectangle(cornerRadius: 16)
                        .fill(theme.cardColor)
                        .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
                }

                // Achievements
                achievementsSection
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgColor.ignoresSafeArea())
    }

    // MARK: - Level Card
    private var levelCard: some View {
        let level = store.currentLevel
        return VStack(spacing: 12) {
            HStack(spacing: 10) {
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: [level.color, level.color.opacity(0.6)],
                                startPoint: .topLeading, endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 50, height: 50)
                        .shadow(color: level.color.opacity(0.4), radius: 6)
                    Image(systemName: level.sfSymbol)
                        .font(.system(size: 22, weight: .bold))
                        .foregroundColor(.white)
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text("Lv.\(level.level)")
                        .font(.system(size: 14, weight: .bold, design: .rounded))
                        .foregroundColor(level.color)
                    Text(level.localizedTitle)
                        .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.textColor)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 2) {
                    Text("\(store.totalXP) XP")
                        .font(.system(size: 18, weight: .bold, design: .rounded))
                        .foregroundStyle(theme.accentGradient)
                    if store.xpToNext > 0 {
                        Text("\(store.xpToNext) XP to next")
                            .font(.system(size: 10, design: .rounded))
                            .foregroundColor(theme.mutedColor)
                    }
                }
            }

            XPProgressView(
                currentXP: store.totalXP,
                currentLevel: store.currentLevel,
                progress: store.xpProgress,
                xpToNext: store.xpToNext
            )
        }
        .padding(16)
        .background {
            RoundedRectangle(cornerRadius: 20)
                .fill(theme.cardColor)
                .overlay {
                    RoundedRectangle(cornerRadius: 20)
                        .stroke(level.color.opacity(0.3), lineWidth: 1.5)
                }
                .shadow(color: level.color.opacity(0.1), radius: 8)
        }
    }

    // MARK: - XP Stats
    private var xpStatsCard: some View {
        HStack(spacing: 0) {
            xpStatItem("target", "xp_per_dose", "+\(XPReward.takeDose)", Color(hex: "#A855F7"))
            Divider().frame(height: 30).overlay(theme.borderColor)
            xpStatItem("star.fill", "xp_daily_bonus", "+\(XPReward.completeAllDaily)", Color(hex: "#F59E0B"))
            Divider().frame(height: 30).overlay(theme.borderColor)
            xpStatItem("flame.fill", "xp_streak_7", "+\(XPReward.streak7)", Color(hex: "#FB923C"))
        }
        .padding(12)
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(theme.cardColor)
                .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
        }
    }

    private func xpStatItem(_ sfSymbol: String, _ key: LocalizedStringKey, _ value: String, _ color: Color) -> some View {
        VStack(spacing: 4) {
            Image(systemName: sfSymbol)
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(color)
            Text(key)
                .font(.system(size: 9, design: .rounded))
                .foregroundColor(theme.mutedColor)
            Text(value)
                .font(.system(size: 12, weight: .bold, design: .rounded))
                .foregroundColor(theme.neonOrange)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Achievements
    private var achievementsSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 6) {
                Image(systemName: "trophy.fill")
                    .foregroundColor(theme.accentColor)
                Text("achievements_title")
                    .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                    .foregroundColor(theme.textColor)
                Spacer()
                Text("\(store.achievements.count)/\(Achievement.allCases.count)")
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .foregroundColor(theme.mutedColor)
            }

            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 3), spacing: 16) {
                ForEach(Achievement.allCases, id: \.self) { achv in
                    AchievementBadgeView(
                        achievement: achv,
                        unlocked: store.achievements.contains(achv)
                    )
                }
            }
        }
        .padding(16)
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(theme.cardColor)
                .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
        }
    }
}
