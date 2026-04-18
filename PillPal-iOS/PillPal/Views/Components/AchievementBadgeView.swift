import SwiftUI

struct AchievementBadgeView: View {
    let achievement: Achievement
    let unlocked: Bool
    var showLabel: Bool = true

    @Environment(ThemeManager.self) private var theme

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                // Outer medal ring with tier gradient
                Circle()
                    .fill(
                        unlocked
                            ? LinearGradient(
                                colors: tierRingColors,
                                startPoint: .topLeading, endPoint: .bottomTrailing
                              )
                            : LinearGradient(colors: [theme.surfaceColor], startPoint: .top, endPoint: .bottom)
                    )
                    .frame(width: 64, height: 64)

                // Inner circle (cut-out look)
                Circle()
                    .fill(
                        unlocked
                            ? achievement.color.opacity(0.15)
                            : theme.surfaceColor
                    )
                    .frame(width: 54, height: 54)

                // Glossy highlight on medal rim
                if unlocked {
                    Circle()
                        .fill(Color.white.opacity(0.35))
                        .frame(width: 16, height: 8)
                        .offset(x: -14, y: -22)
                        .blur(radius: 2)
                }

                // Achievement icon
                Image(systemName: achievement.icon)
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundColor(unlocked ? achievement.color : theme.mutedColor.opacity(0.35))

                // Tier seal
                if unlocked {
                    Image(systemName: "checkmark.seal.fill")
                        .font(.system(size: 18, weight: .bold))
                        .foregroundColor(tierSealColor)
                        .background(Circle().fill(theme.cardColor).padding(-3))
                        .offset(x: 22, y: -22)
                        .transition(.scale.combined(with: .opacity))
                }
            }
            .opacity(unlocked ? 1 : 0.35)
            .grayscale(unlocked ? 0 : 1)
            .shadow(color: unlocked ? achievement.color.opacity(0.3) : .clear, radius: 8, y: 3)

            if showLabel {
                VStack(spacing: 2) {
                    Text(achievement.localizedName)
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .foregroundColor(theme.textColor)
                        .lineLimit(1)

                    Text(achievement.localizedDesc)
                        .font(.system(size: 9, design: .rounded))
                        .foregroundColor(theme.mutedColor)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)

                    if unlocked {
                        Text("+\(achievement.xpReward) XP")
                            .font(.system(size: 8, weight: .heavy, design: .rounded))
                            .foregroundColor(Color(hex: "#FBBF24"))
                    }
                }
            }
        }
    }

    private var tierRingColors: [Color] {
        switch achievement.tier {
        case .gold:   return [Color(hex: "#FFD700"), Color(hex: "#FFA500"), Color(hex: "#FFD700")]
        case .silver: return [Color(hex: "#E0E0E0"), Color(hex: "#A8A8A8"), Color(hex: "#E0E0E0")]
        case .bronze: return [Color(hex: "#CD7F32"), Color(hex: "#A0522D"), Color(hex: "#CD7F32")]
        }
    }

    private var tierSealColor: Color {
        switch achievement.tier {
        case .gold:   return Color(hex: "#FFD700")
        case .silver: return Color(hex: "#C0C0C0")
        case .bronze: return Color(hex: "#CD7F32")
        }
    }
}
