import SwiftUI

struct ScanView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme

    @State private var mode: ScanMode = .choose
    @State private var scanning = false
    @State private var scanResult: ScanResult?
    @State private var showAddSheet = false

    enum ScanMode {
        case choose, scanning, result
    }

    struct ScanResult {
        let name: String
        let dosage: String
        let frequency: Frequency
        let timeOfDay: TimeOfDay
        let foodRelation: FoodRelation
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                Text("add_title")
                    .font(.system(size: theme.titleSize, weight: .bold))
                    .foregroundColor(theme.textColor)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 8)

                switch mode {
                case .choose:
                    chooseView
                case .scanning:
                    scanningAnimation
                case .result:
                    resultView
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgColor.ignoresSafeArea())
        .sheet(isPresented: $showAddSheet) {
            AddMedicationView()
        }
    }

    // MARK: - Choose
    private var chooseView: some View {
        VStack(spacing: 12) {
            // Scan option
            Button {
                startScan()
            } label: {
                VStack(spacing: 16) {
                    Image(systemName: "camera.fill")
                        .font(.system(size: 36))
                        .foregroundColor(theme.accentColor)
                        .frame(width: 80, height: 80)
                        .background(theme.accentColor.opacity(0.1), in: RoundedRectangle(cornerRadius: 20))
                        .phaseAnimator([false, true]) { content, phase in
                            content.scaleEffect(phase ? 1.05 : 1)
                        } animation: { _ in .easeInOut(duration: 2).repeatForever(autoreverses: true) }

                    VStack(spacing: 4) {
                        Text("scan_button")
                            .font(.system(size: theme.bodySize + 1, weight: .semibold))
                            .foregroundColor(theme.textColor)
                        Text("scan_desc")
                            .font(.system(size: theme.captionSize))
                            .foregroundColor(theme.mutedColor)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 32)
                .background {
                    RoundedRectangle(cornerRadius: 24)
                        .strokeBorder(
                            theme.accentColor.opacity(0.3),
                            style: StrokeStyle(lineWidth: 2, dash: [10])
                        )
                }
            }
            .buttonStyle(.plain)

            // Manual option
            Button {
                showAddSheet = true
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "keyboard")
                        .font(.system(size: 22))
                        .foregroundColor(theme.mutedColor)
                        .frame(width: 48, height: 48)
                        .background(theme.surfaceColor, in: RoundedRectangle(cornerRadius: 12))

                    Text("manual_button")
                        .font(.system(size: theme.bodySize, weight: .semibold))
                        .foregroundColor(theme.textColor)

                    Spacer()
                }
                .padding(16)
                .background {
                    RoundedRectangle(cornerRadius: 16)
                        .fill(theme.cardColor)
                        .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
                }
            }
            .buttonStyle(.plain)

            Text("scan_demo_note")
                .font(.system(size: 11))
                .foregroundColor(theme.mutedColor)
                .padding(.top, 8)
        }
    }

    // MARK: - Scanning Animation
    private var scanningAnimation: some View {
        VStack(spacing: 24) {
            // Scan frame
            ZStack {
                RoundedRectangle(cornerRadius: 24)
                    .fill(theme.isPro
                          ? LinearGradient(colors: [Color(hex: "#1A1A1A"), Color(hex: "#0A0A0A")], startPoint: .topLeading, endPoint: .bottomTrailing)
                          : LinearGradient(colors: [Color(hex: "#FFF8EE"), Color(hex: "#FDF6E3")], startPoint: .topLeading, endPoint: .bottomTrailing)
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 24)
                            .stroke(theme.accentColor, lineWidth: 2)
                    }
                    .frame(width: 260, height: 260)

                // Scan line
                Rectangle()
                    .fill(
                        LinearGradient(
                            colors: [.clear, theme.accentColor, .clear],
                            startPoint: .leading, endPoint: .trailing
                        )
                    )
                    .frame(width: 220, height: 2)
                    .shadow(color: theme.accentColor, radius: 8)
                    .phaseAnimator([false, true]) { content, phase in
                        content.offset(y: phase ? 100 : -100)
                    } animation: { _ in .easeInOut(duration: 1.5).repeatForever(autoreverses: true) }

                // Corner brackets
                ForEach(0..<4, id: \.self) { corner in
                    let x: CGFloat = corner % 2 == 0 ? -100 : 100
                    let y: CGFloat = corner < 2 ? -100 : 100
                    CornerBracket(corner: corner)
                        .stroke(theme.accentColor.opacity(0.6), lineWidth: 2)
                        .frame(width: 24, height: 24)
                        .offset(x: x, y: y)
                }

                // Center icon
                Image(systemName: "viewfinder")
                    .font(.system(size: 44))
                    .foregroundColor(theme.accentColor)
                    .opacity(0.6)
                    .phaseAnimator([false, true]) { content, phase in
                        content.opacity(phase ? 0.3 : 0.8)
                    } animation: { _ in .easeInOut(duration: 1.2).repeatForever(autoreverses: true) }
            }

            Text("scan_processing")
                .font(.system(size: theme.bodySize, weight: .semibold))
                .foregroundColor(theme.textColor)
                .phaseAnimator([false, true]) { content, phase in
                    content.opacity(phase ? 0.5 : 1)
                } animation: { _ in .easeInOut(duration: 1.2).repeatForever(autoreverses: true) }
        }
        .padding(.vertical, 40)
    }

    // MARK: - Result
    private var resultView: some View {
        VStack(spacing: 16) {
            if let result = scanResult {
                // Success banner
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(theme.successColor)
                    Text("scan_found")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(theme.successColor)
                }
                .padding(12)
                .frame(maxWidth: .infinity)
                .background(theme.successColor.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))

                // Result card
                VStack(alignment: .leading, spacing: 12) {
                    resultRow("name_label", value: result.name)
                    resultRow("dosage_label", value: result.dosage)
                    resultRow("freq_label", value: LocalizedStringKey(result.frequency.localizationKey))
                    resultRow("time_label", value: LocalizedStringKey(result.timeOfDay.localizationKey))
                    resultRow("food_label_short", value: LocalizedStringKey(result.foodRelation.localizationKey))
                }
                .padding(16)
                .background {
                    RoundedRectangle(cornerRadius: 16)
                        .fill(theme.cardColor)
                        .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
                }

                // Actions
                HStack(spacing: 12) {
                    Button {
                        startScan()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "arrow.counterclockwise")
                            Text("scan_retry")
                        }
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(theme.textColor)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background {
                            RoundedRectangle(cornerRadius: 16)
                                .fill(theme.surfaceColor)
                                .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
                        }
                    }

                    Button {
                        confirmScan()
                    } label: {
                        Text("scan_confirm")
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundColor(.white)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(theme.accentGradient, in: RoundedRectangle(cornerRadius: 16))
                    }
                }
            }
        }
    }

    private func resultRow(_ key: LocalizedStringKey, value: String) -> some View {
        HStack {
            Text(key)
                .font(.system(size: theme.captionSize))
                .foregroundColor(theme.mutedColor)
                .frame(width: 80, alignment: .leading)
            Text(value)
                .font(.system(size: theme.bodySize, weight: .medium))
                .foregroundColor(theme.textColor)
        }
    }

    private func resultRow(_ key: LocalizedStringKey, value: LocalizedStringKey) -> some View {
        HStack {
            Text(key)
                .font(.system(size: theme.captionSize))
                .foregroundColor(theme.mutedColor)
                .frame(width: 80, alignment: .leading)
            Text(value)
                .font(.system(size: theme.bodySize, weight: .medium))
                .foregroundColor(theme.textColor)
        }
    }

    // MARK: - Actions
    private func startScan() {
        mode = .scanning
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()

        // Simulate AI scan
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
            let demo = PillOptions.demoScans.randomElement()!
            scanResult = ScanResult(
                name: demo.name,
                dosage: demo.dosage,
                frequency: demo.frequency,
                timeOfDay: demo.time,
                foodRelation: demo.food
            )
            withAnimation(.spring(response: 0.4)) { mode = .result }
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }

    private func confirmScan() {
        guard let result = scanResult else { return }
        let color = PillOptions.colors.randomElement() ?? "#22D3EE"
        let med = Medication(
            name: result.name,
            dosage: result.dosage,
            frequency: result.frequency,
            timeOfDay: result.timeOfDay,
            foodRelation: result.foodRelation,
            colorHex: color
        )
        store.addMedication(med)
        if !store.achievements.contains(.scanner) {
            store.achievements.append(.scanner)
        }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        withAnimation { mode = .choose }
        scanResult = nil
    }
}

// MARK: - Corner Bracket Shape
struct CornerBracket: Shape {
    let corner: Int // 0=TL, 1=TR, 2=BL, 3=BR

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let w = rect.width, h = rect.height
        switch corner {
        case 0:
            path.move(to: CGPoint(x: 0, y: h))
            path.addLine(to: CGPoint(x: 0, y: 0))
            path.addLine(to: CGPoint(x: w, y: 0))
        case 1:
            path.move(to: CGPoint(x: 0, y: 0))
            path.addLine(to: CGPoint(x: w, y: 0))
            path.addLine(to: CGPoint(x: w, y: h))
        case 2:
            path.move(to: CGPoint(x: 0, y: 0))
            path.addLine(to: CGPoint(x: 0, y: h))
            path.addLine(to: CGPoint(x: w, y: h))
        default:
            path.move(to: CGPoint(x: w, y: 0))
            path.addLine(to: CGPoint(x: w, y: h))
            path.addLine(to: CGPoint(x: 0, y: h))
        }
        return path
    }
}
