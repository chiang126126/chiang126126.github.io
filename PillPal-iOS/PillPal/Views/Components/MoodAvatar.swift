import SwiftUI

struct MoodAvatar: View {
    let adherence: Double
    var size: MoodSize = .medium

    @Environment(ThemeManager.self) private var theme

    enum MoodSize {
        case small, medium, large
        var dimension: CGFloat {
            switch self {
            case .small: return 40
            case .medium: return 64
            case .large: return 96
            }
        }
        var fontSize: CGFloat {
            switch self {
            case .small: return 24
            case .medium: return 36
            case .large: return 56
            }
        }
    }

    private var mood: (emoji: String, label: LocalizedStringKey, color: Color) {
        if adherence >= 100 { return ("😎", "mood_perfect", Color(hex: "#10B981")) }
        if adherence >= 80 { return ("😊", "mood_great", Color(hex: "#22D3EE")) }
        if adherence >= 50 { return ("😐", "mood_ok", Color(hex: "#EAB308")) }
        if adherence >= 20 { return ("😤", "mood_come_on", Color(hex: "#F97316")) }
        return ("🙄", "mood_really", Color(hex: "#EF4444"))
    }

    var body: some View {
        VStack(spacing: 4) {
            Text(mood.emoji)
                .font(.system(size: size.fontSize))
                .frame(width: size.dimension, height: size.dimension)
                .background {
                    Circle()
                        .fill(
                            RadialGradient(
                                colors: [mood.color.opacity(theme.isPro ? 0.15 : 0.2), .clear],
                                center: .center,
                                startRadius: 0,
                                endRadius: size.dimension / 2
                            )
                        )
                        .shadow(color: theme.isPro ? mood.color.opacity(0.2) : .clear, radius: 10)
                }
                .phaseAnimator(adherence < 20 ? [false, true] : [false]) { content, phase in
                    content.rotationEffect(.degrees(phase ? 5 : -5))
                } animation: { _ in .easeInOut(duration: 0.3).repeatCount(3) }

            if size != .small {
                Text(mood.label)
                    .font(.system(size: theme.captionSize, weight: .medium))
                    .foregroundColor(theme.mutedColor)
            }
        }
    }
}
