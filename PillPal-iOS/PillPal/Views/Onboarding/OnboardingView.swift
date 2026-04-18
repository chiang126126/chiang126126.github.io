import SwiftUI

struct OnboardingView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @State private var step = -1

    private let steps: [(icon: String, color: Color, titleKey: String, descKey: String, accents: [String])] = [
        ("camera.viewfinder", Color(hex: "#A855F7"), "onboard_step1_title", "onboard_step1_desc",
         ["camera.fill", "sparkles"]),
        ("bell.badge.fill", Color(hex: "#F472B6"), "onboard_step2_title", "onboard_step2_desc",
         ["bell.fill", "heart.fill"]),
        ("trophy.fill", Color(hex: "#10B981"), "onboard_step3_title", "onboard_step3_desc",
         ["trophy.fill", "star.fill", "bolt.fill"]),
    ]

    var body: some View {
        ZStack {
            theme.bgGradient.ignoresSafeArea()

            if step == -1 {
                welcomeView
                    .transition(.asymmetric(
                        insertion: .opacity.combined(with: .move(edge: .trailing)),
                        removal: .opacity.combined(with: .move(edge: .leading))
                    ))
            } else {
                stepView(step)
                    .id(step)
                    .transition(.asymmetric(
                        insertion: .opacity.combined(with: .move(edge: .trailing)),
                        removal: .opacity.combined(with: .move(edge: .leading))
                    ))
            }
        }
        .animation(.spring(response: 0.5, dampingFraction: 0.8), value: step)
    }

    // MARK: - Welcome
    private var welcomeView: some View {
        VStack(spacing: 28) {
            Spacer()

            MascotView(mood: .celebrating, size: 160)

            VStack(spacing: 10) {
                Text("onboard_welcome")
                    .font(.system(size: theme.titleSize + 6, weight: .bold, design: .rounded))
                    .foregroundStyle(theme.accentGradient)

                Text("onboard_welcome_sub")
                    .font(.system(size: theme.bodySize, design: .rounded))
                    .foregroundColor(theme.mutedColor)
                    .multilineTextAlignment(.center)

                HStack(spacing: 14) {
                    ForEach(["figure.strengthtraining.traditional", "pills.fill", "flame.fill", "trophy.fill", "sparkles"], id: \.self) { sym in
                        Image(systemName: sym)
                            .font(.system(size: 20, weight: .semibold))
                            .foregroundStyle(theme.accentGradient)
                    }
                }
                .padding(.top, 4)
            }

            Spacer()

            Button {
                withAnimation { step = 0 }
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            } label: {
                HStack(spacing: 8) {
                    Text("onboard_get_started")
                        .font(.system(size: 18, weight: .bold, design: .rounded))
                    Image(systemName: "arrow.right")
                        .font(.system(size: 16, weight: .bold))
                }
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 18)
                .background(theme.accentGradient, in: RoundedRectangle(cornerRadius: 20))
                .shadow(color: theme.accentColor.opacity(0.3), radius: 12, y: 6)
            }

            Button("onboard_skip") { store.completeOnboarding() }
                .font(.system(size: 14, design: .rounded))
                .foregroundColor(theme.mutedColor)
        }
        .padding(32)
    }

    // MARK: - Step View
    private func stepView(_ index: Int) -> some View {
        let s = steps[index]
        return VStack(spacing: 28) {
            Spacer()

            ZStack {
                Circle()
                    .stroke(s.color.opacity(0.2), lineWidth: 3)
                    .frame(width: 180, height: 180)
                    .phaseAnimator([false, true]) { content, phase in
                        content.scaleEffect(phase ? 1.08 : 1)
                    } animation: { _ in .easeInOut(duration: 2).repeatForever(autoreverses: true) }

                Circle()
                    .fill(
                        RadialGradient(
                            colors: [s.color.opacity(0.25), s.color.opacity(0.05)],
                            center: .center, startRadius: 0, endRadius: 80
                        )
                    )
                    .frame(width: 160, height: 160)

                Image(systemName: s.icon)
                    .font(.system(size: 56, weight: .semibold))
                    .foregroundStyle(
                        LinearGradient(colors: [s.color, s.color.opacity(0.7)], startPoint: .top, endPoint: .bottom)
                    )
                    .symbolEffect(.bounce, options: .repeat(.periodic(delay: 3.0)))
            }

            HStack(spacing: 14) {
                ForEach(s.accents, id: \.self) { sym in
                    Image(systemName: sym)
                        .font(.system(size: 22, weight: .bold))
                        .foregroundColor(s.color)
                }
            }

            VStack(spacing: 8) {
                Text(LocalizedStringKey(s.titleKey))
                    .font(.system(size: theme.titleSize + 2, weight: .bold, design: .rounded))
                    .foregroundColor(theme.textColor)

                Text(LocalizedStringKey(s.descKey))
                    .font(.system(size: theme.bodySize, design: .rounded))
                    .foregroundColor(theme.mutedColor)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Spacer()

            HStack(spacing: 8) {
                ForEach(0..<steps.count, id: \.self) { i in
                    Capsule()
                        .fill(i == index ? s.color : theme.borderColor)
                        .frame(width: i == index ? 32 : 12, height: 6)
                        .animation(.spring(response: 0.3), value: index)
                }
            }
            .padding(.bottom, 12)

            Button {
                if index < steps.count - 1 {
                    withAnimation { step = index + 1 }
                } else {
                    store.completeOnboarding()
                }
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            } label: {
                HStack(spacing: 8) {
                    Text(index == steps.count - 1 ? "onboard_get_started" : "onboard_next")
                        .font(.system(size: 18, weight: .bold, design: .rounded))
                    Image(systemName: index == steps.count - 1 ? "sparkles" : "chevron.right")
                        .font(.system(size: 14, weight: .bold))
                }
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 18)
                .background(
                    LinearGradient(colors: [s.color, s.color.opacity(0.8)], startPoint: .leading, endPoint: .trailing),
                    in: RoundedRectangle(cornerRadius: 20)
                )
                .shadow(color: s.color.opacity(0.3), radius: 12, y: 6)
            }

            Button("onboard_skip") { store.completeOnboarding() }
                .font(.system(size: 14, design: .rounded))
                .foregroundColor(theme.mutedColor)
        }
        .padding(32)
    }
}
