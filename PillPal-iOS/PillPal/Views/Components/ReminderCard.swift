import SwiftUI

struct ReminderCard: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @State private var message: String = ""
    @State private var messageId = UUID()

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // Icon
            Image(systemName: "bubble.left.fill")
                .font(.system(size: 16))
                .foregroundColor(theme.accentColor)
                .frame(width: 36, height: 36)
                .background(theme.accentColor.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))

            // Message
            Text("\"\(message)\"")
                .font(.system(size: theme.bodySize, weight: .medium))
                .foregroundColor(theme.textColor)
                .lineSpacing(4)
                .id(messageId)
                .transition(.asymmetric(
                    insertion: .move(edge: .trailing).combined(with: .opacity),
                    removal: .move(edge: .leading).combined(with: .opacity)
                ))
                .frame(maxWidth: .infinity, alignment: .leading)

            // Refresh
            Button {
                withAnimation(.spring(response: 0.3)) {
                    refreshMessage()
                }
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 12))
                    .foregroundColor(theme.mutedColor)
                    .padding(6)
                    .background(theme.surfaceColor, in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(16)
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(theme.cardColor)
                .overlay {
                    RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1)
                }
                .overlay(alignment: .topTrailing) {
                    Circle()
                        .fill(theme.accentGradient.opacity(0.1))
                        .frame(width: 80, height: 80)
                        .offset(x: 20, y: -20)
                        .clipped()
                }
        }
        .onAppear { refreshMessage() }
    }

    private func refreshMessage() {
        let messages = reminderMessages(style: store.reminderStyle)
        message = messages.randomElement() ?? ""
        messageId = UUID()
    }

    private func reminderMessages(style: String) -> [String] {
        switch style {
        case "sassy":
            return [
                NSLocalizedString("sassy_1", comment: ""),
                NSLocalizedString("sassy_2", comment: ""),
                NSLocalizedString("sassy_3", comment: ""),
                NSLocalizedString("sassy_4", comment: ""),
                NSLocalizedString("sassy_5", comment: ""),
                NSLocalizedString("sassy_6", comment: ""),
                NSLocalizedString("sassy_7", comment: ""),
                NSLocalizedString("sassy_8", comment: ""),
            ]
        case "gentle":
            return [
                NSLocalizedString("gentle_1", comment: ""),
                NSLocalizedString("gentle_2", comment: ""),
                NSLocalizedString("gentle_3", comment: ""),
                NSLocalizedString("gentle_4", comment: ""),
                NSLocalizedString("gentle_5", comment: ""),
            ]
        default:
            return [NSLocalizedString("neutral_1", comment: "")]
        }
    }
}
