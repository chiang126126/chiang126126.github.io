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
                        .font(.system(size: theme.titleSize, weight: .bold))
                        .foregroundColor(theme.textColor)
                    Spacer()
                    MoodAvatar(adherence: Double(store.overallAdherence), size: .small)
                }
                .padding(.top, 8)

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
                                    .font(.system(size: theme.captionSize))
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
                                        .font(.system(size: theme.captionSize))
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

                // Weekly chart
                VStack(alignment: .leading, spacing: 10) {
                    Text("this_week")
                        .font(.system(size: theme.bodySize, weight: .semibold))
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
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 6) {
                        Image(systemName: "trophy.fill")
                            .foregroundColor(theme.accentColor)
                        Text("achievements_title")
                            .font(.system(size: theme.bodySize, weight: .semibold))
                            .foregroundColor(theme.textColor)
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
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgColor.ignoresSafeArea())
    }
}
