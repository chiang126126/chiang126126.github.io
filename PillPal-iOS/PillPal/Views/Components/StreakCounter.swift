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
                                ? [Color(hex: "#E8B0E0"), Color(hex: "#F8D0E8")]
                                : isHot
                                    ? [Color(hex: "#FFE066"), Color(hex: "#E8B0E0")]
                                    : [theme.pastelLavender, theme.pastelPink],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .frame(width: 40, height: 40)
                    .shadow(color: isOnFire ? Color(hex: "#E8B0E0").opacity(0.4) : .clear, radius: 8)

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
        .card3D(theme)
        .shadow(color: isOnFire ? Color(hex: "#E8B0E0").opacity(0.18) : .clear, radius: 12, y: 4)
    }
}
