import SwiftUI

struct DailyMissionCard: View {
    let missions: [DailyMission]

    @Environment(ThemeManager.self) private var theme

    private var completedCount: Int {
        missions.filter(\.isCompleted).count
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header
            HStack {
                ZStack {
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(theme.accentColor.opacity(0.15))
                        .frame(width: 34, height: 34)
                    Image(systemName: "checklist")
                        .font(.system(size: theme.isCare ? 18 : 16, weight: .bold))
                        .foregroundColor(theme.accentColor)
                }

                Text(LocalizedStringKey("daily_quests"))
                    .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                    .foregroundColor(theme.textColor)

                Spacer()

                Text("\(completedCount)/\(missions.count)")
                    .font(.system(size: theme.captionSize, weight: .semibold, design: .rounded))
                    .foregroundColor(completedCount == missions.count ? theme.successColor : theme.mutedColor)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background {
                        Capsule()
                            .fill(
                                completedCount == missions.count
                                    ? theme.successColor.opacity(0.15)
                                    : theme.surfaceColor
                            )
                    }
            }

            // Mission rows
            VStack(spacing: 6) {
                ForEach(missions) { mission in
                    MissionRow(mission: mission)
                }
            }
        }
        .padding(16)
        .background {
            RoundedRectangle(cornerRadius: 20)
                .fill(theme.cardColor)
                .overlay {
                    RoundedRectangle(cornerRadius: 20)
                        .stroke(theme.borderColor, lineWidth: 1)
                }
        }
    }
}

// MARK: - Mission Row

private struct MissionRow: View {
    let mission: DailyMission

    @Environment(ThemeManager.self) private var theme
    @State private var pulseScale: CGFloat = 1.0

    var body: some View {
        HStack(spacing: 10) {
            // Icon
            Image(systemName: mission.icon)
                .font(.system(size: theme.isCare ? 18 : 14))
                .foregroundColor(mission.isCompleted ? theme.mutedColor : theme.accentColor)
                .frame(width: 28, height: 28)
                .background {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(
                            mission.isCompleted
                                ? theme.surfaceColor
                                : theme.accentColor.opacity(0.1)
                        )
                }

            // Mission name
            Text(mission.localizedTitle)
                .font(.system(size: theme.bodySize, weight: .medium))
                .foregroundColor(mission.isCompleted ? theme.mutedColor : theme.textColor)
                .strikethrough(mission.isCompleted, color: theme.mutedColor)
                .lineLimit(1)

            Spacer()

            // XP reward badge
            Text("+\(mission.xpReward) XP")
                .font(.system(size: theme.captionSize, weight: .bold, design: .rounded))
                .foregroundColor(mission.isCompleted ? theme.mutedColor : theme.neonOrange)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background {
                    Capsule()
                        .fill(
                            mission.isCompleted
                                ? theme.surfaceColor
                                : theme.neonOrange.opacity(0.12)
                        )
                }

            // Checkmark / empty circle
            ZStack {
                if mission.isCompleted {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 20))
                        .foregroundColor(theme.successColor)
                        .transition(.scale.combined(with: .opacity))
                } else {
                    Circle()
                        .stroke(theme.borderColor, lineWidth: 1.5)
                        .frame(width: 20, height: 20)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background {
            RoundedRectangle(cornerRadius: 12)
                .fill(theme.surfaceColor.opacity(mission.isCompleted ? 0.5 : 1))
        }
        .scaleEffect(pulseScale)
        .onAppear {
            guard !mission.isCompleted else { return }
            withAnimation(
                .easeInOut(duration: 2.0)
                .repeatForever(autoreverses: true)
            ) {
                pulseScale = 1.015
            }
        }
    }
}
