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
                    .fill(Color.white)
                    .frame(width: 40, height: 40)
                    .shadow(color: Color.black.opacity(0.06), radius: 4, y: 2)

                Image(systemName: isOnFire ? "flame.fill" : "flame")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundColor(isOnFire ? theme.neonOrange : (isHot ? theme.neonPink : theme.mutedColor))
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
        .pastelCard(theme, tint: theme.pastelCoral, radius: 20)
    }
}
