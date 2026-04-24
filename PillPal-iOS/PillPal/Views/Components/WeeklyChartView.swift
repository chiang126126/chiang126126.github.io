import SwiftUI

struct WeeklyChartView: View {
    let data: [DayStat]
    @Environment(ThemeManager.self) private var theme
    @State private var appeared = false

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            ForEach(Array(data.enumerated()), id: \.element.id) { index, day in
                VStack(spacing: 6) {
                    // Percentage label above bar
                    Text("\(Int(day.pct))%")
                        .font(.system(size: 8, weight: .bold, design: .rounded))
                        .foregroundColor(day.pct >= 100 ? theme.accentColor : theme.mutedColor)
                        .opacity(appeared ? 1 : 0)
                        .animation(.easeOut.delay(Double(index) * 0.08 + 0.4), value: appeared)

                    ZStack(alignment: .bottom) {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(theme.surfaceColor)
                            .frame(height: 64)

                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(
                                day.pct >= 100
                                    ? theme.accentGradient
                                    : LinearGradient(
                                        colors: [theme.accentColor.opacity(0.55), theme.accentColor.opacity(0.25)],
                                        startPoint: .bottom, endPoint: .top
                                      )
                            )
                            .frame(height: appeared ? CGFloat(max(day.pct, 0)) / 100 * 64 : 0)
                            .animation(
                                .spring(response: 0.6, dampingFraction: 0.7).delay(Double(index) * 0.08),
                                value: appeared
                            )

                        // Perfect day sparkle
                        if day.pct >= 100 {
                            Image(systemName: "sparkle")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundColor(.white)
                                .offset(y: -4)
                                .transition(.scale)
                        }
                    }
                    .frame(maxWidth: 30)

                    Text(day.dayLabel)
                        .font(.system(size: theme.isCare ? 11 : 9, weight: day.pct >= 100 ? .bold : .medium, design: .rounded))
                        .foregroundColor(day.pct >= 100 ? theme.accentColor : theme.mutedColor)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(height: 100)
        .onAppear { appeared = true }
    }
}
