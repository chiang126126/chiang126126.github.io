import SwiftUI

struct LevelUpOverlay: View {
    let level: Int
    let onDismiss: () -> Void

    @Environment(ThemeManager.self) private var theme
    @State private var showContent = false
    @State private var mascotScale: CGFloat = 0.1
    @State private var particles: [LevelUpParticle] = []
    @State private var rainbowBurst = false
    @State private var confettiBurst: [ConfettiPiece] = []

    private var gameLevel: GameLevel {
        GameLevel.all.first(where: { $0.level == level }) ?? GameLevel.all[0]
    }

    private let rainbowColors: [Color] = [
        Color(hex: "#FFD83A"), Color(hex: "#6B4EE6"), Color(hex: "#5BC47E"),
        Color(hex: "#FF7A8C"), Color(hex: "#FF9F70"), Color(hex: "#A78BFA")
    ]

    var body: some View {
        ZStack {
            Color.black.opacity(0.75)
                .ignoresSafeArea()
                .onTapGesture { dismiss() }

            // Rainbow ring burst
            if rainbowBurst {
                ForEach(0..<6, id: \.self) { i in
                    Circle()
                        .stroke(rainbowColors[i].opacity(0.4), lineWidth: 3)
                        .frame(width: 80 + CGFloat(i) * 50, height: 80 + CGFloat(i) * 50)
                        .scaleEffect(rainbowBurst ? 1.8 : 0.3)
                        .opacity(rainbowBurst ? 0 : 0.8)
                        .animation(
                            .easeOut(duration: 1.5).delay(Double(i) * 0.08),
                            value: rainbowBurst
                        )
                }
            }

            // Floating confetti
            ForEach(confettiBurst) { piece in
                RoundedRectangle(cornerRadius: 2)
                    .fill(piece.color)
                    .frame(width: piece.width, height: piece.height)
                    .rotationEffect(.degrees(piece.rotation))
                    .offset(x: piece.x, y: piece.y)
                    .opacity(piece.opacity)
            }

            // Glow particles
            ForEach(particles) { particle in
                Circle()
                    .fill(particle.color.opacity(particle.opacity))
                    .frame(width: particle.size, height: particle.size)
                    .blur(radius: 1)
                    .offset(x: particle.x, y: particle.y)
            }

            VStack(spacing: 16) {
                Spacer()

                // 吞吞 celebrating (replaces medal)
                ZStack {
                    // Glow ring behind mascot
                    Circle()
                        .fill(
                            RadialGradient(
                                colors: [gameLevel.color.opacity(0.5), gameLevel.color.opacity(0)],
                                center: .center, startRadius: 30, endRadius: 100
                            )
                        )
                        .frame(width: 200, height: 200)
                        .blur(radius: 8)

                    MascotView(mood: .celebrating, size: 130, showBackground: false)
                }
                .scaleEffect(mascotScale)

                // Level badge
                HStack(spacing: 8) {
                    Image(systemName: gameLevel.sfSymbol)
                        .font(.system(size: 18, weight: .bold))
                    Text("Lv.\(level)")
                        .font(.system(size: 18, weight: .heavy, design: .rounded))
                }
                .foregroundColor(.white)
                .padding(.horizontal, 18)
                .padding(.vertical, 8)
                .background(
                    Capsule().fill(
                        LinearGradient(
                            colors: [gameLevel.color, gameLevel.color.opacity(0.7)],
                            startPoint: .leading, endPoint: .trailing
                        )
                    )
                    .shadow(color: gameLevel.color.opacity(0.5), radius: 12)
                )
                .opacity(showContent ? 1 : 0)
                .scaleEffect(showContent ? 1 : 0.5)

                // Title text
                Text("level_up_title")
                    .font(.system(size: theme.titleSize + 6, weight: .black, design: .rounded))
                    .foregroundStyle(
                        LinearGradient(
                            colors: rainbowColors,
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .shadow(color: gameLevel.color.opacity(0.5), radius: 12)
                    .opacity(showContent ? 1 : 0)
                    .offset(y: showContent ? 0 : 20)

                // Evolution title
                Text(LocalizedStringKey(gameLevel.titleKey))
                    .font(.system(size: theme.bodySize + 2, weight: .bold, design: .rounded))
                    .foregroundColor(.white.opacity(0.8))
                    .opacity(showContent ? 1 : 0)
                    .offset(y: showContent ? 0 : 10)

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
            spawnConfetti()

            withAnimation(.spring(response: 0.6, dampingFraction: 0.5)) {
                mascotScale = 1.0
            }

            withAnimation(.spring(response: 0.6, dampingFraction: 0.8).delay(0.3)) {
                showContent = true
            }

            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                rainbowBurst = true
            }

            animateParticles()
            animateConfetti()

            UINotificationFeedbackGenerator().notificationOccurred(.success)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
            }
        }
    }

    // MARK: - Helpers

    private func dismiss() {
        withAnimation(.easeOut(duration: 0.25)) {
            showContent = false
            mascotScale = 0.1
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            onDismiss()
        }
    }

    private func spawnParticles() {
        particles = (0..<10).map { _ in
            LevelUpParticle(
                x: CGFloat.random(in: -160...160),
                y: CGFloat.random(in: 60...350),
                size: CGFloat.random(in: 6...20),
                color: rainbowColors.randomElement() ?? gameLevel.color,
                opacity: Double.random(in: 0.3...0.7)
            )
        }
    }

    private func spawnConfetti() {
        confettiBurst = (0..<20).map { i in
            let angle = Double(i) * (360.0 / 20.0) * .pi / 180
            return ConfettiPiece(
                targetX: cos(angle) * CGFloat.random(in: 80...180),
                targetY: sin(angle) * CGFloat.random(in: 80...180),
                width: CGFloat.random(in: 6...12),
                height: CGFloat.random(in: 3...6),
                color: rainbowColors[i % rainbowColors.count],
                rotation: Double.random(in: 0...360)
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

    private func animateConfetti() {
        withAnimation(.easeOut(duration: 1.2)) {
            confettiBurst = confettiBurst.map { p in
                var q = p
                q.x = q.targetX
                q.y = q.targetY - 50
                q.rotation += Double.random(in: 180...540)
                q.opacity = 0
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

private struct ConfettiPiece: Identifiable {
    let id = UUID()
    let targetX: CGFloat
    let targetY: CGFloat
    var x: CGFloat = 0
    var y: CGFloat = 0
    var width: CGFloat
    var height: CGFloat
    var color: Color
    var rotation: Double
    var opacity: Double = 1
}
