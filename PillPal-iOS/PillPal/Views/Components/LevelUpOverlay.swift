import SwiftUI

struct LevelUpOverlay: View {
    let level: Int
    let onDismiss: () -> Void

    @Environment(ThemeManager.self) private var theme
    @State private var showContent = false
    @State private var emojiScale: CGFloat = 0.1
    @State private var particles: [LevelUpParticle] = []

    private var gameLevel: GameLevel {
        GameLevel.all.first(where: { $0.level == level }) ?? GameLevel.all[0]
    }

    var body: some View {
        ZStack {
            // Semi-transparent background
            Color.black.opacity(0.7)
                .ignoresSafeArea()
                .onTapGesture { dismiss() }

            // Floating particles
            ForEach(particles) { particle in
                Circle()
                    .fill(particle.color.opacity(particle.opacity))
                    .frame(width: particle.size, height: particle.size)
                    .blur(radius: 1)
                    .offset(x: particle.x, y: particle.y)
            }

            // Center content
            VStack(spacing: 20) {
                Spacer()

                // Large medal-style badge with bounce
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: [gameLevel.color, gameLevel.color.opacity(0.6)],
                                startPoint: .topLeading, endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 140, height: 140)
                        .shadow(color: gameLevel.color.opacity(0.7), radius: 30)
                    Circle()
                        .strokeBorder(Color.white.opacity(0.45), lineWidth: 4)
                        .frame(width: 130, height: 130)
                    Image(systemName: gameLevel.sfSymbol)
                        .font(.system(size: 60, weight: .bold))
                        .foregroundColor(.white)
                }
                .scaleEffect(emojiScale)

                // "LEVEL UP!" text with gradient
                Text("level_up_title")
                    .font(.system(size: theme.titleSize + 8, weight: .black, design: .rounded))
                    .foregroundStyle(
                        LinearGradient(
                            colors: [gameLevel.color, theme.accentColor, gameLevel.color],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .shadow(color: gameLevel.color.opacity(0.5), radius: 12)
                    .opacity(showContent ? 1 : 0)
                    .offset(y: showContent ? 0 : 20)

                // Level number and title
                VStack(spacing: 6) {
                    Text("Lv.\(level)")
                        .font(.system(size: theme.titleSize, weight: .bold, design: .rounded))
                        .foregroundColor(.white)

                    Text(LocalizedStringKey(gameLevel.titleKey))
                        .font(.system(size: theme.bodySize, weight: .medium))
                        .foregroundColor(.white.opacity(0.75))
                }
                .opacity(showContent ? 1 : 0)
                .offset(y: showContent ? 0 : 15)

                Spacer()

                // Dismiss button
                Button {
                    dismiss()
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "sparkles")
                        Text("level_up_dismiss")
                    }
                        .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                        .foregroundColor(.white)
                        .frame(maxWidth: 220)
                        .padding(.vertical, 14)
                        .background {
                            Capsule()
                                .fill(
                                    LinearGradient(
                                        colors: [gameLevel.color, theme.accentColor],
                                        startPoint: .leading,
                                        endPoint: .trailing
                                    )
                                )
                                .shadow(color: gameLevel.color.opacity(0.5), radius: 12, y: 4)
                        }
                }
                .buttonStyle(.plain)
                .opacity(showContent ? 1 : 0)
                .scaleEffect(showContent ? 1 : 0.8)
                .padding(.bottom, 60)
            }
        }
        .onAppear {
            spawnParticles()

            // Emoji bounce in
            withAnimation(.spring(response: 0.5, dampingFraction: 0.5)) {
                emojiScale = 1.0
            }

            // Content fades in
            withAnimation(.spring(response: 0.6, dampingFraction: 0.8).delay(0.3)) {
                showContent = true
            }

            // Animate particles floating upward
            animateParticles()
        }
    }

    // MARK: - Helpers

    private func dismiss() {
        withAnimation(.easeOut(duration: 0.25)) {
            showContent = false
            emojiScale = 0.1
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            onDismiss()
        }
    }

    private func spawnParticles() {
        particles = (0..<8).map { _ in
            LevelUpParticle(
                x: CGFloat.random(in: -160...160),
                y: CGFloat.random(in: 100...350),
                size: CGFloat.random(in: 8...24),
                color: [gameLevel.color, theme.accentColor, theme.neonPurple, theme.neonOrange].randomElement() ?? gameLevel.color,
                opacity: Double.random(in: 0.3...0.7)
            )
        }
    }

    private func animateParticles() {
        withAnimation(
            .easeInOut(duration: 3.0)
            .repeatForever(autoreverses: true)
        ) {
            particles = particles.map { p in
                var q = p
                q.y = p.y - CGFloat.random(in: 60...140)
                q.opacity = Double.random(in: 0.15...0.55)
                return q
            }
        }
    }
}

// MARK: - Particle Data

private struct LevelUpParticle: Identifiable {
    let id = UUID()
    var x: CGFloat
    var y: CGFloat
    var size: CGFloat
    var color: Color
    var opacity: Double
}
