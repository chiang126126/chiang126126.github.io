import SwiftUI

struct SettingsView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @State private var showClearConfirm = false
    @State private var showLangPicker = false

    private let languages: [(code: String, label: String, flag: String)] = [
        ("en", "English", "🇺🇸"),
        ("zh-Hans", "中文", "🇨🇳"),
        ("fr", "Français", "🇫🇷"),
    ]

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                Text("settings_title")
                    .font(.system(size: theme.titleSize, weight: .bold))
                    .foregroundColor(theme.textColor)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 8)

                // Premium banner
                premiumBanner

                // Appearance section
                settingsSection("appearance_title") {
                    HStack(spacing: 10) {
                        modeCard(.pro)
                        modeCard(.care)
                    }
                    .padding(14)
                }

                // Language
                settingsSection("language_title") {
                    if showLangPicker {
                        VStack(spacing: 4) {
                            ForEach(languages, id: \.code) { lang in
                                Button {
                                    // In production: change app language
                                    showLangPicker = false
                                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                } label: {
                                    HStack(spacing: 10) {
                                        Text(lang.flag).font(.system(size: 20))
                                        Text(lang.label)
                                            .font(.system(size: theme.bodySize, weight: .medium))
                                            .foregroundColor(theme.textColor)
                                        Spacer()
                                    }
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 10)
                                    .background(RoundedRectangle(cornerRadius: 10).fill(theme.surfaceColor.opacity(0.5)))
                                }
                            }
                        }
                        .padding(10)
                    } else {
                        settingsRow(icon: "globe", title: "language_title", value: "🇺🇸 English") {
                            showLangPicker = true
                        }
                    }
                }

                // Reminder style
                settingsSection("notifications_title") {
                    VStack(spacing: 8) {
                        Text("reminder_style")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(theme.textColor)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        HStack(spacing: 8) {
                            reminderChip("🔥", "style_sassy", "sassy")
                            reminderChip("🌸", "style_gentle", "gentle")
                            reminderChip("📋", "style_neutral", "neutral")
                        }
                    }
                    .padding(14)
                }

                // Data
                settingsSection("data_title") {
                    settingsRow(icon: "square.and.arrow.down", title: "export_data") {
                        exportData()
                    }
                    Divider().overlay(theme.borderColor)
                    settingsRow(icon: "trash", title: showClearConfirm ? "clear_confirm" : "clear_data", danger: true) {
                        if showClearConfirm {
                            store.clearAllData()
                            showClearConfirm = false
                            UINotificationFeedbackGenerator().notificationOccurred(.warning)
                        } else {
                            showClearConfirm = true
                            UIImpactFeedbackGenerator(style: .light).impactOccurred()
                            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { showClearConfirm = false }
                        }
                    }
                }

                // About
                settingsSection("about_title") {
                    settingsRow(icon: "info.circle", title: "version_label", value: "1.0.0 MVP") {}
                    Divider().overlay(theme.borderColor)
                    settingsRow(icon: "shield", title: "privacy_label") {}
                    Divider().overlay(theme.borderColor)
                    settingsRow(icon: "doc.text", title: "terms_label") {}
                }

                Text("made_with_love")
                    .font(.system(size: 11))
                    .foregroundColor(theme.mutedColor)
                    .padding(.top, 8)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgColor.ignoresSafeArea())
    }

    // MARK: - Premium Banner
    private var premiumBanner: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: "crown.fill")
                    .font(.system(size: 22))
                    .foregroundColor(theme.isPro ? theme.neonPurple : Color(hex: "#F59E0B"))
                    .frame(width: 48, height: 48)
                    .background((theme.isPro ? theme.neonPurple : Color(hex: "#F59E0B")).opacity(0.15), in: RoundedRectangle(cornerRadius: 14))

                VStack(alignment: .leading, spacing: 2) {
                    Text("upgrade_title")
                        .font(.system(size: theme.bodySize, weight: .bold))
                        .foregroundColor(theme.textColor)
                    Text("upgrade_desc")
                        .font(.system(size: theme.captionSize))
                        .foregroundColor(theme.mutedColor)
                }
            }

            HStack(spacing: 8) {
                Text("price_monthly")
                    .font(.system(size: 11))
                    .foregroundColor(theme.textColor)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(theme.surfaceColor, in: Capsule())

                Text("price_yearly")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(theme.accentColor)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(theme.accentColor.opacity(0.1), in: Capsule())

                Text("✨")
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(
                    LinearGradient(
                        colors: [theme.accentColor.opacity(0.08), theme.neonPurple.opacity(0.08)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    )
                )
                .overlay {
                    RoundedRectangle(cornerRadius: 16)
                        .stroke(theme.accentColor.opacity(0.2), lineWidth: 1)
                }
        }
    }

    // MARK: - Mode Card
    private func modeCard(_ m: AppMode) -> some View {
        let isSelected = theme.mode == m
        let isPro = m == .pro

        return Button {
            withAnimation(.spring(response: 0.3)) { theme.mode = m }
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Image(systemName: isPro ? "moon.fill" : "sun.max.fill")
                        .font(.system(size: 14))
                        .foregroundColor(isSelected ? (isPro ? Color(hex: "#22D3EE") : Color(hex: "#F97316")) : .gray)

                    Text(isPro ? "mode_pro" : "mode_care")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(isSelected ? (isPro ? .white : Color(hex: "#44403C")) : .gray)
                }

                Text(isPro ? "mode_pro_desc" : "mode_care_desc")
                    .font(.system(size: 9))
                    .foregroundColor(.gray)

                HStack(spacing: 3) {
                    let colors = isPro ? ["#22D3EE", "#39FF14", "#FF6B35"] : ["#F97316", "#F59E0B", "#FBBF24"]
                    ForEach(colors, id: \.self) { c in
                        Circle().fill(Color(hex: c)).frame(width: 12, height: 12)
                    }
                }
                .padding(.top, 2)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: 12)
                    .fill(isPro ? Color(hex: "#0A0A0A") : Color(hex: "#FDF6E3"))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(
                                isSelected ? (isPro ? Color(hex: "#22D3EE") : Color(hex: "#F97316")) : .clear,
                                lineWidth: 2
                            )
                    }
            }
        }
        .buttonStyle(.plain)
    }

    // MARK: - Reminder Chip
    private func reminderChip(_ emoji: String, _ key: LocalizedStringKey, _ style: String) -> some View {
        Button {
            store.setReminderStyle(style)
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        } label: {
            VStack(spacing: 2) {
                Text(emoji)
                Text(key).font(.system(size: 10, weight: .medium))
            }
            .foregroundColor(store.reminderStyle == style ? theme.accentColor : theme.mutedColor)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .background {
                RoundedRectangle(cornerRadius: 10)
                    .fill(store.reminderStyle == style ? theme.accentColor.opacity(0.1) : .clear)
                    .overlay {
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(store.reminderStyle == style ? theme.accentColor : theme.borderColor, lineWidth: 1)
                    }
            }
        }
    }

    // MARK: - Helpers
    private func settingsSection(_ titleKey: LocalizedStringKey, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(titleKey)
                .font(.system(size: 11, weight: .medium))
                .textCase(.uppercase)
                .tracking(1)
                .foregroundColor(theme.mutedColor)
                .padding(.leading, 4)

            VStack(spacing: 0) {
                content()
            }
            .background {
                RoundedRectangle(cornerRadius: 16)
                    .fill(theme.cardColor)
                    .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
            }
        }
    }

    private func settingsRow(icon: String, title: LocalizedStringKey, value: String? = nil, danger: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: icon)
                    .font(.system(size: 15))
                    .foregroundColor(danger ? theme.dangerColor : theme.mutedColor)
                    .frame(width: 30, height: 30)
                    .background(
                        (danger ? theme.dangerColor : theme.accentColor).opacity(0.1),
                        in: RoundedRectangle(cornerRadius: 8)
                    )

                Text(title)
                    .font(.system(size: theme.bodySize))
                    .foregroundColor(danger ? theme.dangerColor : theme.textColor)

                Spacer()

                if let value {
                    Text(value)
                        .font(.system(size: theme.captionSize))
                        .foregroundColor(theme.mutedColor)
                }

                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(theme.mutedColor)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
        }
        .buttonStyle(.plain)
    }

    private func exportData() {
        // Simple JSON export
        guard let data = try? JSONEncoder().encode(store.medications) else { return }
        guard let json = String(data: data, encoding: .utf8) else { return }
        let av = UIActivityViewController(activityItems: [json], applicationActivities: nil)
        if let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
           let vc = scene.windows.first?.rootViewController {
            vc.present(av, animated: true)
        }
    }
}
