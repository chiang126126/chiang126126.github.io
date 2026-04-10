import SwiftUI

struct WeeklyChartView: View {
    let data: [DayStat]
    @Environment(ThemeManager.self) private var theme
    @State private var appeared = false

    var body: some View {
        HStack(alignment: .bottom, spacing: 6) {
            ForEach(Array(data.enumerated()), id: \.element.id) { index, day in
                VStack(spacing: 4) {
                    // Bar
                    ZStack(alignment: .bottom) {
                        RoundedRectangle(cornerRadius: 6)
                            .fill(theme.surfaceColor)
                            .frame(height: 60)

                        RoundedRectangle(cornerRadius: 6)
                            .fill(
                                day.pct >= 100
                                    ? theme.accentGradient
                                    : LinearGradient(
                                        colors: [theme.accentColor.opacity(0.4)],
                                        startPoint: .bottom, endPoint: .top
                                      )
                            )
                            .frame(height: appeared ? CGFloat(max(day.pct, 0)) / 100 * 60 : 0)
                            .animation(
                                .spring(response: 0.6).delay(Double(index) * 0.08),
                                value: appeared
                            )
                    }
                    .frame(maxWidth: 28)

                    // Day label
                    Text(day.dayLabel)
                        .font(.system(size: theme.isCare ? 11 : 9, weight: day.pct >= 100 ? .bold : .regular))
                        .foregroundColor(day.pct >= 100 ? theme.accentColor : theme.mutedColor)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(height: 80)
        .onAppear { appeared = true }
    }
}
