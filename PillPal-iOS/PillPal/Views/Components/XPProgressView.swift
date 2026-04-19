import SwiftUI

struct XPProgressView: View {
    let currentXP: Int
    let currentLevel: GameLevel
    let progress: Double // 0...1
    let xpToNext: Int

    @Environment(ThemeManager.self) private var theme
    @State private var animatedProgress: Double = 0
    @State private var glowOpacity: Double = 0.4

    private var nextLevel: GameLevel? {
        GameLevel.nextAfter(currentLevel)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Top row: level badge, progress bar, XP text
            HStack(spacing: 12) {
                // Level badge
                HStack(spacing: 6) {
                    ZStack {
                        Circle()
                            .fill(currentLevel.color.opacity(0.18))
                            .frame(width: theme.isCare ? 32 : 26, height: theme.isCare ? 32 : 26)
                        Image(systemName: currentLevel.sfSymbol)
                            .font(.system(size: theme.isCare ? 15 : 12, weight: .bold))
                            .foregroundColor(currentLevel.color)
                    }
                    Text("Lv.\(currentLevel.level)")
                        .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                        .foregroundColor(currentLevel.color)
                }

                // Progress bar
                GeometryReader { geo in
                    let barWidth = geo.size.width

                    ZStack(alignment: .leading) {
                        // Track
                        Capsule()
                            .fill(theme.surfaceColor)
                            .frame(height: theme.isCare ? 14 : 10)
                            .overlay {
                                Capsule()
                                    .stroke(theme.borderColor, lineWidth: 1)
                            }

                        // Fill
                        Capsule()
                            .fill(theme.accentGradient)
                            .frame(
                                width: max(0, barWidth * animatedProgress),
                                height: theme.isCare ? 14 : 10
                            )
                            .shadow(
                                color: theme.accentColor.opacity(glowOpacity),
                                radius: 8,
                                y: 0
                            )
                    }
                }
                .frame(height: theme.isCare ? 14 : 10)

                // XP text
                Text("\(currentXP) / \(xpToNext) XP")
                    .font(.system(size: theme.captionSize, weight: .semibold, design: .rounded))
                    .foregroundColor(theme.mutedColor)
                    .fixedSize()
            }

            // Bottom row: next level hint
            if let next = nextLevel {
                HStack(spacing: 4) {
                    Text("next:")
                        .font(.system(size: theme.captionSize))
                        .foregroundColor(theme.mutedColor)
                    Image(systemName: next.sfSymbol)
                        .font(.system(size: theme.captionSize, weight: .semibold))
                        .foregroundColor(next.color)
                    Text(LocalizedStringKey(next.titleKey))
                        .font(.system(size: theme.captionSize, weight: .medium))
                        .foregroundColor(next.color)
                }
                .padding(.leading, 4)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(theme.cardColor)
                .overlay {
                    RoundedRectangle(cornerRadius: 16)
                        .stroke(theme.borderColor, lineWidth: 1)
                }
        }
        .onAppear {
            // Animate the bar filling
            withAnimation(.spring(response: 0.8, dampingFraction: 0.7)) {
                animatedProgress = min(max(progress, 0), 1)
            }
            // Pulsing glow
            withAnimation(
                .easeInOut(duration: 1.5)
                .repeatForever(autoreverses: true)
            ) {
                glowOpacity = 0.8
            }
        }
        .onChange(of: progress) { _, newValue in
            withAnimation(.spring(response: 0.8, dampingFraction: 0.7)) {
                animatedProgress = min(max(newValue, 0), 1)
            }
        }
    }
}
