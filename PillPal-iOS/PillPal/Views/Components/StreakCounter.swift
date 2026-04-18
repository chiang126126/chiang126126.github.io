import SwiftUI

struct StreakCounter: View {
    let streak: Int
    @Environment(ThemeManager.self) private var theme

    private var isOnFire: Bool { streak >= 7 }
    private var isHot: Bool { streak >= 3 }

    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: isOnFire
                                ? [Color(hex: "#FB923C"), Color(hex: "#F472B6")]
                                : isHot
                                    ? [Color(hex: "#FBBF24"), Color(hex: "#FB923C")]
                                    : [theme.pastelLavender, theme.pastelPink],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .frame(width: 40, height: 40)
                    .shadow(color: isOnFire ? Color(hex: "#FB923C").opacity(0.4) : .clear, radius: 8)

                Image(systemName: isOnFire ? "flame.fill" : "flame")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundColor(.white)
                    .phaseAnimator([false, true]) { content, phase in
                        content.scaleEffect(phase ? 1.12 : 1)
                    } animation: { _ in .easeInOut(duration: 0.9).repeatForever(autoreverses: true) }
            }

            VStack(alignment: .leading, spacing: 0) {
                Text("\(streak)")
                    .font(.system(size: 22, weight: .heavy, design: .rounded))
                    .foregroundColor(theme.textColor)
                    .contentTransition(.numericText())

                Text("streak_days")
                    .font(.system(size: theme.captionSize, weight: .medium, design: .rounded))
                    .foregroundColor(theme.mutedColor)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(theme.cardColor)
                .overlay {
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .stroke(theme.borderColor, lineWidth: 1)
                }
                .shadow(color: isOnFire ? Color(hex: "#FB923C").opacity(0.18) : Color.black.opacity(0.05), radius: 12, y: 4)
        }
    }
}
