import SwiftUI

struct ReminderCard: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @State private var messageKey: String = "neutral_1"
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
                    .foregroundColor(theme.textColor)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
                    .background(theme.accentColor.opacity(0.15))

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
                Group {
                    (Text("\u{201C} ") + Text(LocalizedStringKey(messageKey)) + Text(" \u{201D}"))
                        .font(.system(size: theme.bodySize - 1, weight: .medium, design: .rounded))
                        .foregroundColor(theme.textColor)
                        .lineSpacing(3)
                }
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
        .card3D(theme, radius: 20)
        .onAppear { refreshMessage() }
    }

    private func refreshMessage() {
        let keys = reminderKeys(style: store.reminderStyle)
        messageKey = keys.randomElement() ?? "neutral_1"
        messageId = UUID()
    }

    private func reminderKeys(style: String) -> [String] {
        switch style {
        case "sassy":
            return (1...8).map { "sassy_\($0)" }
        case "gentle":
            return (1...5).map { "gentle_\($0)" }
        default:
            return ["neutral_1"]
        }
    }
}
