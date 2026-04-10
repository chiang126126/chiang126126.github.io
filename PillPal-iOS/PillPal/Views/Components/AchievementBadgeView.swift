import SwiftUI

struct AchievementBadgeView: View {
    let achievement: Achievement
    let unlocked: Bool
    var showLabel: Bool = true

    @Environment(ThemeManager.self) private var theme

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                RoundedRectangle(cornerRadius: 14)
                    .fill(unlocked ? achievement.color.opacity(0.12) : theme.surfaceColor)
                    .overlay {
                        RoundedRectangle(cornerRadius: 14)
                            .stroke(unlocked ? achievement.color : .clear, lineWidth: 2)
                    }
                    .frame(width: 56, height: 56)

                Image(systemName: achievement.icon)
                    .font(.system(size: 22))
                    .foregroundColor(unlocked ? achievement.color : theme.mutedColor.opacity(0.5))

                // Check badge
                if unlocked {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 16))
                        .foregroundColor(theme.successColor)
                        .background(Circle().fill(theme.cardColor).padding(-1))
                        .offset(x: 22, y: -22)
                        .transition(.scale)
                }
            }
            .opacity(unlocked ? 1 : 0.35)
            .grayscale(unlocked ? 0 : 1)

            if showLabel {
                VStack(spacing: 2) {
                    Text(LocalizedStringKey("achv_\(achievement.rawValue)"))
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(theme.textColor)
                        .lineLimit(1)

                    Text(LocalizedStringKey("achv_\(achievement.rawValue)_desc"))
                        .font(.system(size: 9))
                        .foregroundColor(theme.mutedColor)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                }
            }
        }
    }
}
