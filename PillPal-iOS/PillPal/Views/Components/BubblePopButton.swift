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

    private var done: Bool { isTaken || isSkipped }
    private var size: CGFloat { theme.isCare ? 60 : 48 }

    var body: some View {
        ZStack {
            // Burst particles
            ForEach(particles) { p in
                Circle()
                    .fill(medication.color)
                    .frame(width: 6, height: 6)
                    .offset(x: p.offsetX, y: p.offsetY)
                    .opacity(p.opacity)
            }

            // Main bubble
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
                        Text("—")
                            .font(.system(size: 14))
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
                        .fill(done
                              ? (theme.isPro ? theme.surfaceColor : Color(hex: "#F5F0E8"))
                              : medication.color.opacity(0.12))
                        .overlay {
                            Circle()
                                .stroke(
                                    done ? theme.borderColor : medication.color,
                                    lineWidth: 2
                                )
                        }
                        .shadow(
                            color: (!done && theme.isPro) ? medication.color.opacity(0.25) : .clear,
                            radius: 10
                        )
                }
                .scaleEffect(isPopping ? 0.8 : 1)
                .opacity(done ? 0.6 : 1)
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
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.6), value: isTaken)
        .animation(.spring(response: 0.3), value: isSkipped)
    }

    private func pop() {
        isPopping = true
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()

        // Create particles
        let newParticles = (0..<8).map { i -> ParticleData in
            let angle = Double(i) * 45 * .pi / 180
            return ParticleData(
                targetX: cos(angle) * 40,
                targetY: sin(angle) * 40
            )
        }
        particles = newParticles

        // Animate particles
        withAnimation(.easeOut(duration: 0.5)) {
            particles = particles.map { p in
                var q = p
                q.offsetX = q.targetX
                q.offsetY = q.targetY
                q.opacity = 0
                return q
            }
        }

        // Trigger take after pop
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            onTake()
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
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
}
