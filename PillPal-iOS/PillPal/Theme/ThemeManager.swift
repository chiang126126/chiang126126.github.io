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
    // Care: Gizalab-style — deep purple + yellow + pastel cards
    // Pro: deep plum + neon
    var accentColor: Color { isPro ? Color(hex: "#A78BFA") : Color(hex: "#6B4EE6") }
    var accentSecondary: Color { isPro ? Color(hex: "#FFD83A") : Color(hex: "#FFD83A") }
    var bgColor: Color { isPro ? Color(hex: "#110A1F") : .white }
    var bgSecondary: Color { isPro ? Color(hex: "#1B0F2E") : Color(hex: "#FAF8FE") }
    var cardColor: Color { isPro ? Color(hex: "#1E1533") : .white }
    var surfaceColor: Color { isPro ? Color(hex: "#2A1F44") : Color(hex: "#F5F2FA") }
    var borderColor: Color { isPro ? Color(hex: "#3B2A5E") : Color(hex: "#F0EBF5") }
    var textColor: Color { isPro ? .white : Color(hex: "#222222") }
    var mutedColor: Color { isPro ? Color(hex: "#A78BFA").opacity(0.7) : Color(hex: "#9A95A8") }

    // Hero area (purple top section)
    var heroColor: Color { isPro ? Color(hex: "#2A1F44") : Color(hex: "#6B4EE6") }
    var heroTextColor: Color { .white }

    var warmYellow: Color { Color(hex: "#FFD83A") }
    var warmPeach: Color { Color(hex: "#FFC4A0") }
    var warmMint: Color { Color(hex: "#C8E8CE") }

    var neonOrange: Color { isPro ? Color(hex: "#FB923C") : Color(hex: "#FF9F70") }
    var neonPink: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#FF7A8C") }
    var neonPurple: Color { isPro ? Color(hex: "#A855F7") : Color(hex: "#6B4EE6") }

    var successColor: Color { isPro ? Color(hex: "#10B981") : Color(hex: "#5BC47E") }
    var dangerColor: Color { Color(hex: "#EF4444") }

    // Pastel card variants — matching reference palette
    var pastelLavender: Color { isPro ? Color(hex: "#E9D5FF") : Color(hex: "#E8E0FF") }
    var pastelPink: Color { isPro ? Color(hex: "#FBCFE8") : Color(hex: "#FFD0DC") }
    var pastelMint: Color { Color(hex: "#D4F0D8") }
    var pastelSky: Color { isPro ? Color(hex: "#DBEAFE") : Color(hex: "#C8E0F5") }
    var pastelCoral: Color { Color(hex: "#FFE0D0") }
    var pastelYellow: Color { Color(hex: "#FFEEAA") }
    var pastelCream: Color { Color(hex: "#FFF7EC") }

    // Darker ink colors for text on pastel cards
    var inkMint: Color { Color(hex: "#2C7A4B") }
    var inkSky: Color { Color(hex: "#2C5F8A") }
    var inkLavender: Color { Color(hex: "#5A3EC0") }
    var inkPink: Color { Color(hex: "#C02C4E") }
    var inkCoral: Color { Color(hex: "#C45A2A") }

    // MARK: - Gradients
    // Accent gradient — solid purple (for progress bars, chart fills)
    var accentGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F472B6")], startPoint: .topLeading, endPoint: .bottomTrailing)
            : LinearGradient(colors: [Color(hex: "#7A5EE8"), Color(hex: "#6B4EE6")], startPoint: .leading, endPoint: .trailing)
    }

    // Button gradient — subtle purple for CTA buttons
    var buttonGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F472B6")], startPoint: .leading, endPoint: .trailing)
            : LinearGradient(colors: [Color(hex: "#7A5EE8"), Color(hex: "#6B4EE6")], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    // Hero gradient — deep purple for hero sections
    var heroGradient: LinearGradient {
        isPro
            ? LinearGradient(
                colors: [Color(hex: "#2A1F44"), Color(hex: "#3B2A5E"), Color(hex: "#1E1533")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
            : LinearGradient(
                colors: [Color(hex: "#7A5EE8"), Color(hex: "#6B4EE6"), Color(hex: "#5D3FD3")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
    }

    var bgGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#110A1F"), Color(hex: "#1B0F2E")], startPoint: .top, endPoint: .bottom)
            : LinearGradient(colors: [.white, Color(hex: "#FAF8FE")], startPoint: .top, endPoint: .bottom)
    }

    // MARK: - Font sizes (Care mode uses larger)
    var bodySize: CGFloat { isCare ? 17 : 15 }
    var titleSize: CGFloat { isCare ? 28 : 24 }
    var captionSize: CGFloat { isCare ? 14 : 12 }
    var buttonSize: CGFloat { isCare ? 52 : 44 }

    // MARK: - Shadows
    func softShadow(color: Color? = nil, radius: CGFloat = 10, y: CGFloat = 4) -> (Color, CGFloat, CGFloat, CGFloat) {
        let c = color ?? (isPro ? Color.black.opacity(0.4) : Color.black.opacity(0.06))
        return (c, radius, 0, y)
    }
}

// MARK: - Clean Card Modifier
extension View {
    func card3D(_ theme: ThemeManager, radius: CGFloat = 22) -> some View {
        self
            .background {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(theme.cardColor)
                    .shadow(
                        color: theme.isPro ? Color.black.opacity(0.25) : Color(hex: "#6B4EE6").opacity(0.06),
                        radius: theme.isPro ? 12 : 8,
                        y: theme.isPro ? 6 : 3
                    )
            }
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .stroke(
                        theme.isPro ? theme.borderColor : theme.borderColor.opacity(0.5),
                        lineWidth: theme.isPro ? 1 : 0.5
                    )
                    .allowsHitTesting(false)
            }
    }

    // Pastel-colored card (for variety like the reference's mint/sky/lavender cards)
    func pastelCard(_ theme: ThemeManager, tint: Color, radius: CGFloat = 22) -> some View {
        self
            .background {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(theme.isPro ? theme.cardColor : tint)
                    .shadow(
                        color: theme.isPro ? Color.black.opacity(0.25) : Color.black.opacity(0.04),
                        radius: theme.isPro ? 12 : 6,
                        y: theme.isPro ? 6 : 2
                    )
            }
    }
}
