import SwiftUI

struct AchievementBadgeView: View {
    let achievement: Achievement
    let unlocked: Bool
    var showLabel: Bool = true

    @Environment(ThemeManager.self) private var theme

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(
                        unlocked
                            ? LinearGradient(
                                colors: [achievement.color.opacity(0.22), achievement.color.opacity(0.06)],
                                startPoint: .topLeading, endPoint: .bottomTrailing
                              )
                            : LinearGradient(
                                colors: [theme.surfaceColor, theme.surfaceColor],
                                startPoint: .top, endPoint: .bottom
                              )
                    )
                    .frame(width: 68, height: 68)
                    .overlay {
                        RoundedRectangle(cornerRadius: 20, style: .continuous)
                            .stroke(
                                unlocked
                                    ? achievement.color.opacity(0.35)
                                    : theme.borderColor,
                                lineWidth: unlocked ? 2 : 1
                            )
                    }

                if unlocked {
                    Image(systemName: "sparkle")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundColor(achievement.color.opacity(0.7))
                        .offset(x: -24, y: -24)
                    Image(systemName: "sparkle")
                        .font(.system(size: 6, weight: .bold))
                        .foregroundColor(achievement.color.opacity(0.5))
                        .offset(x: 26, y: -20)
                    Image(systemName: "sparkle")
                        .font(.system(size: 7, weight: .bold))
                        .foregroundColor(achievement.color.opacity(0.4))
                        .offset(x: -20, y: 24)
                }

                Image(systemName: achievement.icon)
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundColor(unlocked ? achievement.color : theme.mutedColor.opacity(0.25))

                if !unlocked {
                    Image(systemName: "lock.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(theme.mutedColor.opacity(0.45))
                        .padding(4)
                        .background(theme.cardColor.opacity(0.9), in: Circle())
                        .offset(x: 22, y: 22)
                }
            }
            .opacity(unlocked ? 1 : 0.4)
            .shadow(color: unlocked ? achievement.color.opacity(0.2) : .clear, radius: 10, y: 4)

            if showLabel {
                VStack(spacing: 2) {
                    Text(LocalizedStringKey("achv_\(achievement.rawValue)"))
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .foregroundColor(unlocked ? theme.textColor : theme.mutedColor)
                        .lineLimit(1)

                    Text(LocalizedStringKey("achv_\(achievement.rawValue)_desc"))
                        .font(.system(size: 9, design: .rounded))
                        .foregroundColor(theme.mutedColor)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)

                    if unlocked {
                        Text("+\(achievement.xpReward) XP")
                            .font(.system(size: 8, weight: .heavy, design: .rounded))
                            .foregroundColor(Color(hex: "#F6C85F"))
                    }
                }
            }
        }
    }
}
