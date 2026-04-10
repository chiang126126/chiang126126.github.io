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
            self.mode = .pro
        }
    }

    private func save() {
        UserDefaults.standard.set(mode.rawValue, forKey: "pillpal-mode")
    }

    // Convenience
    var isPro: Bool { mode == .pro }
    var isCare: Bool { mode == .care }

    func toggle() {
        mode = isPro ? .care : .pro
    }

    // MARK: - Colors
    var accentColor: Color { isPro ? Color(hex: "#22D3EE") : Color(hex: "#F97316") }
    var accentSecondary: Color { isPro ? Color(hex: "#39FF14") : Color(hex: "#F59E0B") }
    var bgColor: Color { isPro ? Color(hex: "#0A0A0A") : Color(hex: "#FDF6E3") }
    var cardColor: Color { isPro ? Color(hex: "#141414") : .white }
    var surfaceColor: Color { isPro ? Color(hex: "#1E1E1E") : Color(hex: "#FFF8EE") }
    var borderColor: Color { isPro ? Color(hex: "#2A2A2A") : Color(hex: "#E7E0D5") }
    var textColor: Color { isPro ? .white : Color(hex: "#44403C") }
    var mutedColor: Color { isPro ? Color.gray : Color(hex: "#78716C") }

    var neonOrange: Color { Color(hex: "#FF6B35") }
    var neonPink: Color { Color(hex: "#FF2D78") }
    var neonPurple: Color { Color(hex: "#A855F7") }
    var successColor: Color { Color(hex: "#10B981") }
    var dangerColor: Color { Color(hex: "#EF4444") }

    // MARK: - Gradients
    var accentGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#22D3EE"), Color(hex: "#39FF14")], startPoint: .topLeading, endPoint: .bottomTrailing)
            : LinearGradient(colors: [Color(hex: "#F97316"), Color(hex: "#F59E0B")], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    // MARK: - Font sizes (Care mode uses larger)
    var bodySize: CGFloat { isCare ? 18 : 15 }
    var titleSize: CGFloat { isCare ? 28 : 24 }
    var captionSize: CGFloat { isCare ? 14 : 12 }
    var buttonSize: CGFloat { isCare ? 52 : 44 }
}
