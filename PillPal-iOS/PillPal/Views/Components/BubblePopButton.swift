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

    private var done: Bool { isTaken || isSkipped }
    private var size: CGFloat { theme.isCare ? 60 : 48 }

    private let confettiColors: [Color] = [
        Color(hex: "#A78BFA"), Color(hex: "#F472B6"), Color(hex: "#FBBF24"),
        Color(hex: "#34D399"), Color(hex: "#60A5FA"), Color(hex: "#FB923C")
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
                    }
                }
                .frame(width: size, height: size)
                .background {
                    Circle()
                        .fill(
                            done
                            ? theme.surfaceColor
                            : LinearGradient(
                                colors: [medication.color.opacity(0.18), medication.color.opacity(0.08)],
                                startPoint: .topLeading, endPoint: .bottomTrailing
                              )
                        )
                        .overlay {
                            Circle()
                                .stroke(
                                    done
                                    ? theme.borderColor
                                    : LinearGradient(
                                        colors: [medication.color, medication.color.opacity(0.5)],
                                        startPoint: .topLeading, endPoint: .bottomTrailing
                                      ),
                                    lineWidth: 2.5
                                )
                        }
                        .shadow(
                            color: (!done) ? medication.color.opacity(0.3) : .clear,
                            radius: 10, y: 3
                        )
                }
                .scaleEffect(isPopping ? 0.75 : 1)
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
                        Label("skip_dose", systemImage: "forward.fill")
                    }
                }
            }

            if showXPBadge {
                Text("+\(XPReward.takeDose) XP")
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .foregroundColor(Color(hex: "#FBBF24"))
                    .shadow(color: Color(hex: "#FBBF24").opacity(0.6), radius: 4)
                    .offset(y: -size / 2 - 12 + xpOffset)
                    .transition(.scale.combined(with: .opacity))
            }
        }
        .animation(.spring(response: 0.35, dampingFraction: 0.55), value: isTaken)
        .animation(.spring(response: 0.3), value: isSkipped)
    }

    private func pop() {
        isPopping = true

        UIImpactFeedbackGenerator(style: .heavy).impactOccurred()

        let count = 12
        particles = (0..<count).map { i in
            let angle = Double(i) * (360.0 / Double(count)) * .pi / 180
            let distance = CGFloat.random(in: 35...60)
            return ParticleData(
                targetX: cos(angle) * distance,
                targetY: sin(angle) * distance,
                color: confettiColors[i % confettiColors.count],
                shapeSize: CGFloat.random(in: 5...9),
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

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            onTake()
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }

        withAnimation(.spring(response: 0.3)) {
            showXPBadge = true
            xpOffset = 0
        }
        withAnimation(.easeOut(duration: 0.8).delay(0.1)) {
            xpOffset = -28
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) {
            withAnimation(.easeOut(duration: 0.3)) {
                showXPBadge = false
                xpOffset = 0
            }
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            isPopping = false
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
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
