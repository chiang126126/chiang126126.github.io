import SwiftUI

// MARK: - App Theme Mode
enum AppMode: String, Codable {
    case pro, care
}

// MARK: - Theme Manager
@Observable
final class ThemeManager {
    var mode: AppMode {
        didSet { save() }
    }

    init() {
        if let raw = UserDefaults.standard.string(forKey: "pillpal-mode"),
           let saved = AppMode(rawValue: raw) {
            self.mode = saved
        } else {
            self.mode = .care
        }
    }

    private func save() {
        UserDefaults.standard.set(mode.rawValue, forKey: "pillpal-mode")
    }

    var isPro: Bool { mode == .pro }
    var isCare: Bool { mode == .care }

    func toggle() {
        mode = isPro ? .care : .pro
    }

    // MARK: - Colors
    // Care: dopamine pastels — bright lime, lavender pink, pure white bg
    // Pro: deep plum + neon — cyberpunk but still playful
    var accentColor: Color { isPro ? Color(hex: "#A78BFA") : Color(hex: "#5EAA3A") }
    var accentSecondary: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#E8A8F0") }
    var bgColor: Color { isPro ? Color(hex: "#110A1F") : .white }
    var bgSecondary: Color { isPro ? Color(hex: "#1B0F2E") : Color(hex: "#FBF5FF") }
    var cardColor: Color { isPro ? Color(hex: "#1E1533") : .white }
    var surfaceColor: Color { isPro ? Color(hex: "#2A1F44") : Color(hex: "#F8F4FF") }
    var borderColor: Color { isPro ? Color(hex: "#3B2A5E") : Color(hex: "#EEEAF5") }
    var textColor: Color { isPro ? .white : Color(hex: "#1A1A1A") }
    var mutedColor: Color { isPro ? Color(hex: "#A78BFA").opacity(0.7) : Color(hex: "#9B95A8") }

    var warmYellow: Color { Color(hex: "#FFE066") }
    var warmPeach: Color { Color(hex: "#FFB89A") }
    var warmMint: Color { Color(hex: "#A8F0C8") }

    // Legacy aliases for Pro mode components
    var neonOrange: Color { isPro ? Color(hex: "#FB923C") : Color(hex: "#FFB89A") }
    var neonPink: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#E8A8F0") }
    var neonPurple: Color { isPro ? Color(hex: "#A855F7") : Color(hex: "#5EAA3A") }

    var successColor: Color { isPro ? Color(hex: "#10B981") : Color(hex: "#5EAA3A") }
    var dangerColor: Color { Color(hex: "#EF4444") }

    // Pastels for cards / chips — bright dopamine candy colors
    var pastelLavender: Color { isPro ? Color(hex: "#E9D5FF") : Color(hex: "#F0D8FF") }
    var pastelPink: Color { isPro ? Color(hex: "#FBCFE8") : Color(hex: "#FFD0F0") }
    var pastelCream: Color { Color(hex: "#FFF7EC") }
    var pastelMint: Color { Color(hex: "#C8FFD8") }
    var pastelSky: Color { isPro ? Color(hex: "#DBEAFE") : Color(hex: "#C8EEFF") }

    // MARK: - Gradients
    var accentGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F472B6")], startPoint: .topLeading, endPoint: .bottomTrailing)
            : LinearGradient(colors: [Color(hex: "#F0B0E0"), Color(hex: "#C8E040")], startPoint: .leading, endPoint: .trailing)
    }

    var heroGradient: LinearGradient {
        isPro
            ? LinearGradient(
                colors: [Color(hex: "#2A1F44"), Color(hex: "#3B2A5E"), Color(hex: "#1E1533")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
            : LinearGradient(
                colors: [.white, Color(hex: "#FBF5FF"), Color(hex: "#FFFBF0")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
    }

    var bgGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#110A1F"), Color(hex: "#1B0F2E")], startPoint: .top, endPoint: .bottom)
            : LinearGradient(colors: [.white, Color(hex: "#FBF5FF"), Color(hex: "#FFFBF0")], startPoint: .top, endPoint: .bottom)
    }

    // MARK: - Font sizes (Care mode uses larger)
    var bodySize: CGFloat { isCare ? 17 : 15 }
    var titleSize: CGFloat { isCare ? 28 : 24 }
    var captionSize: CGFloat { isCare ? 14 : 12 }
    var buttonSize: CGFloat { isCare ? 52 : 44 }

    // MARK: - Shadows
    func softShadow(color: Color? = nil, radius: CGFloat = 10, y: CGFloat = 4) -> (Color, CGFloat, CGFloat, CGFloat) {
        let c = color ?? (isPro ? Color.black.opacity(0.4) : Color(hex: "#C8C0D8").opacity(0.15))
        return (c, radius, 0, y)
    }
}

// MARK: - 3D Card Modifier
extension View {
    func card3D(_ theme: ThemeManager, radius: CGFloat = 22) -> some View {
        self
            .background {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: theme.isPro
                                ? [theme.cardColor, theme.cardColor]
                                : [.white, Color(hex: "#FEFCFF")],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .shadow(color: Color(hex: "#C8C0D8").opacity(theme.isPro ? 0 : 0.10), radius: 1, y: 0.5)
                    .shadow(color: Color(hex: "#C8C0D8").opacity(theme.isPro ? 0.25 : 0.14), radius: 12, y: 6)
                    .shadow(color: Color(hex: "#C8C0D8").opacity(theme.isPro ? 0.12 : 0.06), radius: 28, y: 14)
            }
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: theme.isPro
                                ? [Color.clear, Color.clear, Color.clear]
                                : [Color.white.opacity(0.7), Color.clear, Color.clear],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .allowsHitTesting(false)
            }
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .stroke(
                        LinearGradient(
                            colors: theme.isPro
                                ? [theme.borderColor, theme.borderColor]
                                : [Color.white, Color(hex: "#E8E0F0").opacity(0.5)],
                            startPoint: .top, endPoint: .bottom
                        ),
                        lineWidth: theme.isPro ? 1 : 1.5
                    )
                    .allowsHitTesting(false)
            }
    }
}
