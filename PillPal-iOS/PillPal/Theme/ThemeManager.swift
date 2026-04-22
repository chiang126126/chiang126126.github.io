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
    // Care: warm sage cream + olive-lime/lavender — playful, hopeful, vibrant
    // Pro: deep plum + neon — cyberpunk but still playful
    var accentColor: Color { isPro ? Color(hex: "#A78BFA") : Color(hex: "#7AA030") }
    var accentSecondary: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#DDA8E0") }
    var bgColor: Color { isPro ? Color(hex: "#110A1F") : Color(hex: "#F4F2E7") }
    var bgSecondary: Color { isPro ? Color(hex: "#1B0F2E") : Color(hex: "#EFEDE2") }
    var cardColor: Color { isPro ? Color(hex: "#1E1533") : .white }
    var surfaceColor: Color { isPro ? Color(hex: "#2A1F44") : Color(hex: "#F0EEE3") }
    var borderColor: Color { isPro ? Color(hex: "#3B2A5E") : Color(hex: "#E5E2D3") }
    var textColor: Color { isPro ? .white : Color(hex: "#2B2B2E") }
    var mutedColor: Color { isPro ? Color(hex: "#A78BFA").opacity(0.7) : Color(hex: "#8E8B78") }

    var warmYellow: Color { Color(hex: "#FFD76A") }
    var warmPeach: Color { Color(hex: "#FFB89A") }
    var warmMint: Color { Color(hex: "#BFE8D2") }

    // Legacy aliases for Pro mode components
    var neonOrange: Color { isPro ? Color(hex: "#FB923C") : Color(hex: "#FFB89A") }
    var neonPink: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#DDA8E0") }
    var neonPurple: Color { isPro ? Color(hex: "#A855F7") : Color(hex: "#7AA030") }

    var successColor: Color { isPro ? Color(hex: "#10B981") : Color(hex: "#7BC5A0") }
    var dangerColor: Color { Color(hex: "#EF4444") }

    // Pastels for cards / chips
    var pastelLavender: Color { isPro ? Color(hex: "#E9D5FF") : Color(hex: "#F5E0F5") }
    var pastelPink: Color { isPro ? Color(hex: "#FBCFE8") : Color(hex: "#FFD6EC") }
    var pastelCream: Color { Color(hex: "#FFF7EC") }
    var pastelMint: Color { Color(hex: "#D5F0DC") }
    var pastelSky: Color { isPro ? Color(hex: "#DBEAFE") : Color(hex: "#E0EEFF") }

    // MARK: - Gradients
    var accentGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F472B6")], startPoint: .topLeading, endPoint: .bottomTrailing)
            : LinearGradient(colors: [Color(hex: "#C868A0"), Color(hex: "#78A028")], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    var heroGradient: LinearGradient {
        isPro
            ? LinearGradient(
                colors: [Color(hex: "#2A1F44"), Color(hex: "#3B2A5E"), Color(hex: "#1E1533")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
            : LinearGradient(
                colors: [Color(hex: "#F4F2E7"), Color(hex: "#EFEDE2"), Color(hex: "#FFF7EC")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
    }

    var bgGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#110A1F"), Color(hex: "#1B0F2E")], startPoint: .top, endPoint: .bottom)
            : LinearGradient(colors: [Color(hex: "#F4F2E7"), Color(hex: "#EFEDE2"), Color(hex: "#FFF7EC")], startPoint: .top, endPoint: .bottom)
    }

    // MARK: - Font sizes (Care mode uses larger)
    var bodySize: CGFloat { isCare ? 17 : 15 }
    var titleSize: CGFloat { isCare ? 28 : 24 }
    var captionSize: CGFloat { isCare ? 14 : 12 }
    var buttonSize: CGFloat { isCare ? 52 : 44 }

    // MARK: - Shadows
    func softShadow(color: Color? = nil, radius: CGFloat = 10, y: CGFloat = 4) -> (Color, CGFloat, CGFloat, CGFloat) {
        let c = color ?? (isPro ? Color.black.opacity(0.4) : Color(hex: "#B8B4A0").opacity(0.12))
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
                                : [.white, Color(hex: "#FAFAF4")],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .shadow(color: Color(hex: "#B8B4A0").opacity(theme.isPro ? 0 : 0.08), radius: 0.5, y: 0.5)
                    .shadow(color: Color(hex: "#B8B4A0").opacity(theme.isPro ? 0.25 : 0.15), radius: 10, y: 5)
                    .shadow(color: Color(hex: "#B8B4A0").opacity(theme.isPro ? 0.12 : 0.06), radius: 24, y: 12)
            }
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: theme.isPro
                                ? [Color.clear, Color.clear, Color.clear]
                                : [Color.white.opacity(0.6), Color.clear, Color.clear],
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
                                : [Color.white, Color(hex: "#DDD9C8").opacity(0.5)],
                            startPoint: .top, endPoint: .bottom
                        ),
                        lineWidth: theme.isPro ? 1 : 1.5
                    )
                    .allowsHitTesting(false)
            }
    }
}
