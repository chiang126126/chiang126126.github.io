import SwiftUI

struct OnboardingView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @State private var step = -1 // -1 = welcome

    private let steps: [(emoji: String, titleKey: String, descKey: String, color: Color)] = [
        ("📸", "onboard_step1_title", "onboard_step1_desc", Color(hex: "#22D3EE")),
        ("🔥", "onboard_step2_title", "onboard_step2_desc", Color(hex: "#FF6B35")),
        ("🏆", "onboard_step3_title", "onboard_step3_desc", Color(hex: "#10B981")),
    ]

    var body: some View {
        ZStack {
            theme.bgColor.ignoresSafeArea()

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
        VStack(spacing: 24) {
            Spacer()

            // Animated pill icon
            Text("💊")
                .font(.system(size: 80))
                .padding(24)
                .background {
                    RoundedRectangle(cornerRadius: 32)
                        .fill(theme.accentGradient)
                        .shadow(color: theme.accentColor.opacity(0.3), radius: 20, y: 10)
                }
                .phaseAnimator([false, true]) { content, phase in
                    content
                        .rotationEffect(.degrees(phase ? 5 : -5))
                        .scaleEffect(phase ? 1.05 : 1)
                } animation: { _ in .easeInOut(duration: 2).repeatForever(autoreverses: true) }

            VStack(spacing: 8) {
                Text("onboard_welcome")
                    .font(.system(size: theme.titleSize + 4, weight: .bold))
                    .foregroundStyle(theme.accentGradient)

                Text("onboard_welcome_sub")
                    .font(.system(size: theme.bodySize))
                    .foregroundColor(theme.mutedColor)
                    .multilineTextAlignment(.center)
            }

            Spacer()

            Button {
                withAnimation { step = 0 }
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            } label: {
                Text("onboard_get_started")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(theme.isPro ? .black : .white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(theme.accentGradient, in: RoundedRectangle(cornerRadius: 16))
                    .shadow(color: theme.accentColor.opacity(0.3), radius: 10, y: 5)
            }

            Button("onboard_skip") {
                store.completeOnboarding()
            }
            .font(.system(size: 14))
            .foregroundColor(theme.mutedColor)
        }
        .padding(32)
    }

    // MARK: - Step View
    private func stepView(_ index: Int) -> some View {
        let s = steps[index]
        return VStack(spacing: 24) {
            Spacer()

            // Illustration circle
            Text(s.emoji)
                .font(.system(size: 72))
                .frame(width: 160, height: 160)
                .background {
                    Circle()
                        .fill(s.color.opacity(0.1))
                        .overlay(Circle().stroke(s.color.opacity(0.2), lineWidth: 2))
                }
                .phaseAnimator([false, true]) { content, phase in
                    content.scaleEffect(phase ? 1.08 : 1)
                } animation: { _ in .easeInOut(duration: 2).repeatForever(autoreverses: true) }

            VStack(spacing: 8) {
                Text(LocalizedStringKey(s.titleKey))
                    .font(.system(size: theme.titleSize, weight: .bold))
                    .foregroundColor(theme.textColor)

                Text(LocalizedStringKey(s.descKey))
                    .font(.system(size: theme.bodySize))
                    .foregroundColor(theme.mutedColor)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Spacer()

            // Step indicators
            HStack(spacing: 6) {
                ForEach(0..<steps.count, id: \.self) { i in
                    Capsule()
                        .fill(i == index ? theme.accentColor : theme.borderColor)
                        .frame(width: i == index ? 28 : 12, height: 5)
                        .animation(.spring, value: index)
                }
            }
            .padding(.bottom, 16)

            Button {
                if index < steps.count - 1 {
                    withAnimation { step = index + 1 }
                } else {
                    store.completeOnboarding()
                }
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            } label: {
                HStack {
                    Text(index == steps.count - 1 ? "onboard_get_started" : "onboard_next")
                        .font(.system(size: 18, weight: .semibold))
                    Image(systemName: "chevron.right")
                        .font(.system(size: 14, weight: .bold))
                }
                .foregroundColor(theme.isPro ? .black : .white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(theme.accentGradient, in: RoundedRectangle(cornerRadius: 16))
            }

            Button("onboard_skip") {
                store.completeOnboarding()
            }
            .font(.system(size: 14))
            .foregroundColor(theme.mutedColor)
        }
        .padding(32)
    }
}
