import SwiftUI

// MARK: - App Theme Mode
enum AppMode: String, Codable {
    case normal, care
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
            self.mode = .normal
        }
    }

    private func save() {
        UserDefaults.standard.set(mode.rawValue, forKey: "pillpal-mode")
    }

    var isNormal: Bool { mode == .normal }
    var isCare: Bool { mode == .care }

    func toggle() {
        mode = isNormal ? .care : .normal
    }

    // MARK: - Colors (unified palette — same for both modes)
    var accentColor: Color { Color(hex: "#7B61FF") }
    var accentSecondary: Color { Color(hex: "#FFD83A") }
    var bgColor: Color { Color(hex: "#F8F5FF") }
    var bgSecondary: Color { Color(hex: "#F0EDFA") }
    var cardColor: Color { .white }
    var surfaceColor: Color { Color(hex: "#F0EDFA") }
    var borderColor: Color { Color(hex: "#EEEAF5") }
    var textColor: Color { Color(hex: "#1A1A2E") }
    var mutedColor: Color { Color(hex: "#8E8EA0") }

    var heroColor: Color { Color(hex: "#7B61FF") }
    var heroTextColor: Color { .white }

    var warmYellow: Color { Color(hex: "#FFD83A") }
    var warmPeach: Color { Color(hex: "#FFC4A0") }
    var warmMint: Color { Color(hex: "#C8E8CE") }

    var neonOrange: Color { Color(hex: "#FF9F70") }
    var neonPink: Color { Color(hex: "#FF7A8C") }
    var neonPurple: Color { Color(hex: "#7B61FF") }

    var successColor: Color { Color(hex: "#34D399") }
    var dangerColor: Color { Color(hex: "#EF4444") }

    // MARK: - Soft Card Tints
    var cardMint: Color { Color(hex: "#DDF8E8") }
    var cardPeach: Color { Color(hex: "#FFE2D2") }
    var cardYellow: Color { Color(hex: "#FFF1A8") }
    var cardLavender: Color { Color(hex: "#EEE8FF") }
    var cardSky: Color { Color(hex: "#E8F2FF") }
    var cardPink: Color { Color(hex: "#FFE8F1") }
    var cardDangerLight: Color { Color(hex: "#FFE8E8") }

    // Legacy names
    var pastelLavender: Color { cardLavender }
    var pastelPink: Color { cardPink }
    var pastelMint: Color { cardMint }
    var pastelSky: Color { cardSky }
    var pastelCoral: Color { cardPeach }
    var pastelYellow: Color { cardYellow }
    var pastelCream: Color { Color(hex: "#FFF7EC") }

    var inkMint: Color { Color(hex: "#2C7A4B") }
    var inkSky: Color { Color(hex: "#2C5F8A") }
    var inkLavender: Color { Color(hex: "#5A3EC0") }
    var inkPink: Color { Color(hex: "#C02C4E") }
    var inkCoral: Color { Color(hex: "#C45A2A") }

    // MARK: - Gradients
    var accentGradient: LinearGradient {
        LinearGradient(colors: [Color(hex: "#8B6FFF"), Color(hex: "#7B61FF")], startPoint: .leading, endPoint: .trailing)
    }

    var buttonGradient: LinearGradient {
        LinearGradient(colors: [Color(hex: "#8B6FFF"), Color(hex: "#7B61FF")], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    var bannerGradient: LinearGradient {
        LinearGradient(colors: [Color(hex: "#8B6FFF"), Color(hex: "#6C4FF6")], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    var heroGradient: LinearGradient {
        LinearGradient(colors: [Color(hex: "#8B6FFF"), Color(hex: "#7B61FF"), Color(hex: "#6C4FF6")], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    var bgGradient: LinearGradient {
        LinearGradient(colors: [Color(hex: "#F8F5FF"), .white], startPoint: .top, endPoint: .bottom)
    }

    // MARK: - Font sizes
    // Normal: standard; Care: larger for elderly users
    var bodySize: CGFloat { isCare ? 19 : 15 }
    var titleSize: CGFloat { isCare ? 32 : 24 }
    var captionSize: CGFloat { isCare ? 16 : 12 }
    var buttonSize: CGFloat { isCare ? 56 : 44 }

    // MARK: - Shadows
    func softShadow(color: Color? = nil, radius: CGFloat = 10, y: CGFloat = 4) -> (Color, CGFloat, CGFloat, CGFloat) {
        let c = color ?? Color.black.opacity(0.05)
        return (c, radius, 0, y)
    }
}

// MARK: - Card Modifiers
extension View {
    func card3D(_ theme: ThemeManager, radius: CGFloat = 24) -> some View {
        self
            .background {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(theme.cardColor)
                    .shadow(color: Color(hex: "#7B61FF").opacity(0.06), radius: 10, y: 4)
            }
    }

    func pastelCard(_ theme: ThemeManager, tint: Color, radius: CGFloat = 24) -> some View {
        self
            .background {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(tint)
                    .shadow(color: Color.black.opacity(0.04), radius: 8, y: 3)
            }
    }
}
