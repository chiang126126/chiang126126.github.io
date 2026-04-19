import SwiftUI

/// Displays the mascot (吞吞) at a mood derived from today's adherence.
/// Replaces the old Unicode-emoji avatar to guarantee consistent rendering.
struct MoodAvatar: View {
    let adherence: Double
    var size: MoodSize = .medium

    @Environment(ThemeManager.self) private var theme

    enum MoodSize {
        case small, medium, large
        var dimension: CGFloat {
            switch self {
            case .small: return 48
            case .medium: return 88
            case .large: return 140
            }
        }
    }

    private var mood: MascotMood { MascotMood.forAdherence(adherence) }

    private var label: LocalizedStringKey {
        switch mood {
        case .perfect, .celebrating: return "mood_perfect"
        case .happy, .eating:        return "mood_great"
        case .neutral, .sleepy:      return "mood_ok"
        case .eyeroll, .grumpy:      return "mood_really"
        case .sad, .deflated:        return "mood_come_on"
        }
    }

    var body: some View {
        VStack(spacing: 6) {
            MascotView(mood: mood, size: size.dimension, showBackground: size != .small)

            if size != .small {
                Text(label)
                    .font(.system(size: theme.captionSize, weight: .semibold, design: .rounded))
                    .foregroundColor(theme.mutedColor)
            }
        }
    }
}
