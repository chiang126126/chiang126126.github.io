import SwiftUI

struct AchievementBadgeView: View {
    let achievement: Achievement
    let unlocked: Bool
    var showLabel: Bool = true

    @Environment(ThemeManager.self) private var theme

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                // Tier-colored border for unlocked
                RoundedRectangle(cornerRadius: 16)
                    .fill(unlocked ? achievement.color.opacity(0.12) : theme.surfaceColor)
                    .overlay {
                        RoundedRectangle(cornerRadius: 16)
                            .stroke(
                                unlocked ? achievement.tier.borderGradient : LinearGradient(colors: [.clear], startPoint: .top, endPoint: .bottom),
                                lineWidth: 2.5
                            )
                    }
                    .frame(width: 60, height: 60)

                Image(systemName: achievement.icon)
                    .font(.system(size: 24))
                    .foregroundColor(unlocked ? achievement.color : theme.mutedColor.opacity(0.4))

                // Tier badge
                if unlocked {
                    Image(systemName: "checkmark.seal.fill")
                        .font(.system(size: 18))
                        .foregroundColor(achievement.tier == .gold ? Color(hex: "#FFD700") : achievement.tier == .silver ? Color(hex: "#C0C0C0") : Color(hex: "#CD7F32"))
                        .background(Circle().fill(theme.cardColor).padding(-2))
                        .offset(x: 24, y: -24)
                        .transition(.scale.combined(with: .opacity))
                }
            }
            .opacity(unlocked ? 1 : 0.35)
            .grayscale(unlocked ? 0 : 1)
            .shadow(color: unlocked ? achievement.color.opacity(0.2) : .clear, radius: 6)

            if showLabel {
                VStack(spacing: 2) {
                    // Use NSLocalizedString for reliable key lookup
                    Text(achievement.localizedName)
                        .font(.system(size: 11, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.textColor)
                        .lineLimit(1)

                    Text(achievement.localizedDesc)
                        .font(.system(size: 9, design: .rounded))
                        .foregroundColor(theme.mutedColor)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)

                    if unlocked {
                        Text("+\(achievement.xpReward) XP")
                            .font(.system(size: 8, weight: .bold, design: .rounded))
                            .foregroundColor(theme.neonOrange)
                    }
                }
            }
        }
    }
}
