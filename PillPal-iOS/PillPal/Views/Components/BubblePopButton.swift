import SwiftUI

struct BubblePopButton: View {
    let medication: Medication
    let isTaken: Bool
    let isSkipped: Bool
    let onTake: () -> Void
    let onSkip: () -> Void

    @Environment(ThemeManager.self) private var theme
    @State private var particles: [ParticleData] = []
    @State private var isPopping = false
    @State private var showXPBadge = false
    @State private var xpOffset: CGFloat = 0
    @State private var gulpPhase = false
    @State private var pillFlying = false

    private var done: Bool { isTaken || isSkipped }
    private var size: CGFloat { theme.isCare ? 60 : 48 }

    private let confettiColors: [Color] = [
        Color(hex: "#FFD76A"), Color(hex: "#FFB89A"), Color(hex: "#BFE8D2"),
        Color(hex: "#FFCCB6"), Color(hex: "#7BC5A0"), Color(hex: "#F6C85F")
    ]

    var body: some View {
        ZStack {
            ForEach(particles) { p in
                RoundedRectangle(cornerRadius: 2)
                    .fill(p.color)
                    .frame(width: p.shapeSize, height: p.shapeSize * 0.6)
                    .rotationEffect(.degrees(p.rotation))
                    .offset(x: p.offsetX, y: p.offsetY)
                    .opacity(p.opacity)
            }

            Button {
                guard !done else { return }
                pop()
            } label: {
                ZStack {
                    if isTaken {
                        Image(systemName: "checkmark")
                            .font(.system(size: theme.isCare ? 24 : 18, weight: .bold))
                            .foregroundColor(theme.successColor)
                            .transition(.scale.combined(with: .opacity))
                    } else if isSkipped {
                        Image(systemName: "minus")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundColor(theme.mutedColor)
                    } else {
                        Image(systemName: medication.iconName)
                            .font(.system(size: theme.isCare ? 24 : 18))
                            .foregroundColor(medication.color)
                            .symbolEffect(.wiggle, options: .repeat(.periodic(delay: 3.0)))
                            .scaleEffect(pillFlying ? 0.3 : 1)
                            .opacity(pillFlying ? 0 : 1)
                    }
                }
                .frame(width: size, height: size)
                .background {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: done
                                    ? [theme.surfaceColor, theme.surfaceColor]
                                    : [medication.color.opacity(0.2), medication.color.opacity(0.06)],
                                startPoint: .topLeading, endPoint: .bottomTrailing
                            )
                        )
                        .overlay {
                            Circle()
                                .fill(
                                    RadialGradient(
                                        colors: done
                                            ? [Color.clear]
                                            : [Color.white.opacity(0.35), Color.clear],
                                        center: UnitPoint(x: 0.35, y: 0.3),
                                        startRadius: 0,
                                        endRadius: size * 0.4
                                    )
                                )
                        }
                        .overlay {
                            Circle()
                                .stroke(
                                    LinearGradient(
                                        colors: done
                                            ? [theme.borderColor, theme.borderColor]
                                            : [Color.white.opacity(0.6), medication.color.opacity(0.35)],
                                        startPoint: .topLeading, endPoint: .bottomTrailing
                                    ),
                                    lineWidth: 2.5
                                )
                        }
                        .shadow(color: done ? .clear : medication.color.opacity(0.15), radius: 2, y: 1)
                        .shadow(color: done ? .clear : medication.color.opacity(0.25), radius: 10, y: 4)
                }
                .scaleEffect(isPopping ? 0.7 : (gulpPhase ? 1.12 : 1))
                .opacity(done ? 0.55 : 1)
            }
            .buttonStyle(.plain)
            .disabled(done)
            .contextMenu {
                if !done {
                    Button {
                        onSkip()
                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    } label: {
                        Label("feed_skip", systemImage: "forward.fill")
                    }
                }
            }

            if showXPBadge {
                HStack(spacing: 2) {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 10))
                    Text("+\(XPReward.takeDose)")
                }
                .font(.system(size: 13, weight: .heavy, design: .rounded))
                .foregroundColor(Color(hex: "#F6C85F"))
                .shadow(color: Color(hex: "#F6C85F").opacity(0.6), radius: 4)
                .offset(y: -size / 2 - 12 + xpOffset)
                .transition(.scale.combined(with: .opacity))
            }
        }
        .animation(.spring(response: 0.35, dampingFraction: 0.55), value: isTaken)
        .animation(.spring(response: 0.3), value: isSkipped)
    }

    private func pop() {
        // Phase 1: pill shrinks (being "eaten")
        withAnimation(.easeIn(duration: 0.15)) {
            pillFlying = true
        }

        // Phase 2: gulp swell
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            withAnimation(.spring(response: 0.2, dampingFraction: 0.4)) {
                gulpPhase = true
            }
        }

        // Phase 3: pop burst
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            isPopping = true
            gulpPhase = false
            UIImpactFeedbackGenerator(style: .heavy).impactOccurred()

            let count = 14
            particles = (0..<count).map { i in
                let angle = Double(i) * (360.0 / Double(count)) * .pi / 180
                let distance = CGFloat.random(in: 35...65)
                return ParticleData(
                    targetX: cos(angle) * distance,
                    targetY: sin(angle) * distance,
                    color: confettiColors[i % confettiColors.count],
                    shapeSize: CGFloat.random(in: 4...9),
                    rotation: Double.random(in: 0...360)
                )
            }

            withAnimation(.easeOut(duration: 0.55)) {
                particles = particles.map { p in
                    var q = p
                    q.offsetX = q.targetX
                    q.offsetY = q.targetY
                    q.opacity = 0
                    q.rotation += Double.random(in: 90...270)
                    return q
                }
            }
        }

        // Phase 4: take dose + success haptic
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            onTake()
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            pillFlying = false
        }

        // XP badge
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            withAnimation(.spring(response: 0.3)) {
                showXPBadge = true
                xpOffset = 0
            }
            withAnimation(.easeOut(duration: 0.8).delay(0.1)) {
                xpOffset = -28
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            withAnimation(.easeOut(duration: 0.3)) {
                showXPBadge = false
                xpOffset = 0
            }
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
            isPopping = false
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
            particles = []
        }
    }
}

private struct ParticleData: Identifiable {
    let id = UUID()
    let targetX: Double
    let targetY: Double
    var offsetX: Double = 0
    var offsetY: Double = 0
    var opacity: Double = 1
    var color: Color
    var shapeSize: CGFloat
    var rotation: Double
}
