import SwiftUI

struct ContentView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme

    var body: some View {
        Group {
            if !store.onboardingDone {
                OnboardingView()
            } else {
                MainTabView()
            }
        }
        .preferredColorScheme(theme.isPro ? .dark : .light)
    }
}

struct MainTabView: View {
    @Environment(ThemeManager.self) private var theme
    @State private var selectedTab = 0

    var body: some View {
        ZStack(alignment: .bottom) {
            TabView(selection: $selectedTab) {
                DashboardView()
                    .tag(0)
                MedicationsListView()
                    .tag(1)
                ScanView()
                    .tag(2)
                StatsView()
                    .tag(3)
                SettingsView()
                    .tag(4)
            }
            .tabViewStyle(.automatic)
            // Hide default tab bar
            .toolbar(.hidden, for: .tabBar)

            // Custom bottom nav bar
            CustomTabBar(selectedTab: $selectedTab)
        }
        .ignoresSafeArea(.keyboard)
    }
}

// MARK: - Custom Tab Bar
struct CustomTabBar: View {
    @Binding var selectedTab: Int
    @Environment(ThemeManager.self) private var theme

    private let tabs: [(icon: String, label: LocalizedStringKey)] = [
        ("house.fill", "tab_home"),
        ("pill.fill", "tab_meds"),
        ("camera.viewfinder", "tab_scan"),
        ("chart.bar.fill", "tab_stats"),
        ("gearshape.fill", "tab_settings")
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(0..<tabs.count, id: \.self) { index in
                if index == 2 {
                    // Center scan button
                    Button {
                        withAnimation(.spring(response: 0.3)) {
                            selectedTab = index
                        }
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    } label: {
                        ZStack {
                            Circle()
                                .fill(theme.accentGradient)
                                .frame(width: 56, height: 56)
                                .shadow(color: theme.accentColor.opacity(0.4), radius: 8, y: 4)

                            Image(systemName: tabs[index].icon)
                                .font(.system(size: 22, weight: .bold))
                                .foregroundColor(theme.isPro ? .black : .white)
                        }
                        .offset(y: -16)
                    }
                    .frame(maxWidth: .infinity)
                } else {
                    Button {
                        withAnimation(.spring(response: 0.3)) {
                            selectedTab = index
                        }
                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    } label: {
                        VStack(spacing: 3) {
                            Image(systemName: tabs[index].icon)
                                .font(.system(size: theme.isCare ? 22 : 19))
                                .foregroundColor(selectedTab == index ? theme.accentColor : theme.mutedColor)

                            Text(tabs[index].label)
                                .font(.system(size: theme.isCare ? 11 : 9))
                                .foregroundColor(selectedTab == index ? theme.accentColor : theme.mutedColor)

                            if selectedTab == index {
                                Circle()
                                    .fill(theme.accentColor)
                                    .frame(width: 4, height: 4)
                                    .transition(.scale)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .padding(.top, 8)
        .padding(.bottom, 6)
        .background {
            Rectangle()
                .fill(theme.cardColor.opacity(0.95))
                .overlay(alignment: .top) {
                    Divider().opacity(0.3)
                }
                .background(.ultraThinMaterial)
                .ignoresSafeArea()
        }
    }
}

#Preview {
    ContentView()
        .environment(MedicationStore())
        .environment(ThemeManager())
}
