import SwiftUI

struct ReminderCard: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @State private var message: String = ""
    @State private var messageId = UUID()

    private var dayNumber: String {
        "\(Calendar.current.component(.day, from: Date()))"
    }

    private var weekday: String {
        let f = DateFormatter()
        f.dateFormat = "EEE"
        return f.string(from: Date()).uppercased()
    }

    var body: some View {
        HStack(spacing: 14) {
            // Tear-off calendar badge
            VStack(spacing: 0) {
                Text(weekday)
                    .font(.system(size: 9, weight: .heavy, design: .rounded))
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
                    .background(theme.accentGradient)

                Text(dayNumber)
                    .font(.system(size: 22, weight: .heavy, design: .rounded))
                    .foregroundColor(theme.textColor)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                    .background(theme.surfaceColor)
            }
            .frame(width: 50)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(theme.borderColor, lineWidth: 1)
            }
            .shadow(color: Color.black.opacity(0.06), radius: 4, y: 2)

            // Message bubble with mascot
            VStack(alignment: .leading, spacing: 6) {
                Text("\"\(message)\"")
                    .font(.system(size: theme.bodySize - 1, weight: .medium, design: .rounded))
                    .foregroundColor(theme.textColor)
                    .lineSpacing(3)
                    .id(messageId)
                    .transition(.asymmetric(
                        insertion: .move(edge: .trailing).combined(with: .opacity),
                        removal: .move(edge: .leading).combined(with: .opacity)
                    ))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            VStack(spacing: 6) {
                MascotView(
                    mood: store.todayRemaining > 2 ? .grumpy : .happy,
                    size: 42,
                    showBackground: false
                )

                Button {
                    withAnimation(.spring(response: 0.3)) {
                        refreshMessage()
                    }
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(theme.mutedColor)
                        .padding(5)
                        .background(theme.surfaceColor, in: Circle())
                }
            }
        }
        .padding(14)
        .background {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(theme.cardColor)
                .overlay {
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .stroke(theme.borderColor, lineWidth: 1)
                }
                .shadow(color: Color.black.opacity(0.05), radius: 10, y: 4)
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
            return (1...8).map { NSLocalizedString("sassy_\($0)", comment: "") }
        case "gentle":
            return (1...5).map { NSLocalizedString("gentle_\($0)", comment: "") }
        default:
            return [NSLocalizedString("neutral_1", comment: "")]
        }
    }
}
