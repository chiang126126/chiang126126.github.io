import SwiftUI

@main
struct PillPalApp: App {
    @State private var store = MedicationStore()
    @State private var themeManager = ThemeManager()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(store)
                .environment(themeManager)
                .environment(\.locale, Locale(identifier: store.appLanguage))
                .task {
                    // iOS retains scheduled local notifications across launches.
                    // We just need to ensure permission; individual meds are (re)scheduled
                    // by MedicationStore when the user adds/updates/toggles them.
                    _ = await NotificationManager.shared.requestPermission()
                }
        }
    }
}
