import SwiftUI

struct StreakCounter: View {
    let streak: Int
    @Environment(ThemeManager.self) private var theme

    private var isOnFire: Bool { streak >= 7 }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "flame.fill")
                .font(.system(size: theme.isCare ? 24 : 18))
                .foregroundColor(isOnFire ? theme.neonOrange : theme.mutedColor)
                .symbolEffect(.bounce, options: .repeat(.periodic(delay: 2.0)), isActive: isOnFire)

            VStack(alignment: .leading, spacing: 0) {
                Text("\(streak)")
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .foregroundColor(theme.neonOrange)
                    .contentTransition(.numericText())

                Text("streak_days")
                    .font(.system(size: theme.captionSize))
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
                        .stroke(theme.borderColor, lineWidth: 1)
                }
        }
    }
}
