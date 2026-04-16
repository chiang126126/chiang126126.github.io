import SwiftUI

struct StreakCounter: View {
    let streak: Int
    @Environment(ThemeManager.self) private var theme

    private var isOnFire: Bool { streak >= 7 }

    var body: some View {
        HStack(spacing: 8) {
            ZStack {
                if isOnFire {
                    Text(Emoji.fire)
                        .font(.system(size: theme.isCare ? 22 : 18))
                        .phaseAnimator([false, true]) { content, phase in
                            content.scaleEffect(phase ? 1.2 : 1)
                        } animation: { _ in .easeInOut(duration: 0.8).repeatForever(autoreverses: true) }
                } else {
                    Image(systemName: "flame.fill")
                        .font(.system(size: theme.isCare ? 22 : 18))
                        .foregroundColor(theme.mutedColor)
                }
            }

            VStack(alignment: .leading, spacing: 0) {
                Text("\(streak)")
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .foregroundColor(theme.neonOrange)
                    .contentTransition(.numericText())

                Text("streak_days")
                    .font(.system(size: theme.captionSize, design: .rounded))
                    .foregroundColor(theme.mutedColor)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(theme.cardColor)
                .overlay {
                    RoundedRectangle(cornerRadius: 16)
                        .stroke(isOnFire ? theme.neonOrange.opacity(0.3) : theme.borderColor, lineWidth: 1)
                }
                .shadow(color: isOnFire ? theme.neonOrange.opacity(0.15) : .clear, radius: 8)
        }
    }
}
