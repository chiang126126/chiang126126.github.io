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
                    .font(.system(size: theme.titleSize, weight: .bold, design: .rounded))
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
        .background(theme.bgGradient.ignoresSafeArea())
        .sheet(isPresented: $showAddSheet) {
            AddMedicationView()
        }
    }

    // MARK: - Choose
    private var chooseView: some View {
        VStack(spacing: 16) {
            // Mascot with scanning eyes
            VStack(spacing: 8) {
                MascotView(mood: .happy, size: 110, showBackground: true)

                Text("scan_desc")
                    .font(.system(size: theme.captionSize, weight: .medium, design: .rounded))
                    .foregroundColor(theme.mutedColor)
                    .multilineTextAlignment(.center)
            }
            .padding(.vertical, 8)

            // Scan button
            Button {
                startScan()
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        Circle()
                            .fill(theme.accentColor.opacity(0.15))
                            .frame(width: 52, height: 52)
                        Image(systemName: "camera.fill")
                            .font(.system(size: 24))
                            .foregroundColor(theme.accentColor)
                    }
                    .phaseAnimator([false, true]) { content, phase in
                        content.scaleEffect(phase ? 1.06 : 1)
                    } animation: { _ in .easeInOut(duration: 2).repeatForever(autoreverses: true) }

                    VStack(alignment: .leading, spacing: 2) {
                        Text("scan_button")
                            .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                            .foregroundColor(theme.textColor)
                        Text("scan_demo_note")
                            .font(.system(size: 11, design: .rounded))
                            .foregroundColor(theme.mutedColor)
                    }

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(theme.mutedColor)
                }
                .padding(16)
                .background {
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .fill(theme.cardColor)
                        .overlay {
                            RoundedRectangle(cornerRadius: 20, style: .continuous)
                                .stroke(theme.accentColor.opacity(0.3), style: StrokeStyle(lineWidth: 1.5, dash: [8]))
                        }
                        .shadow(color: theme.accentColor.opacity(0.08), radius: 12, y: 4)
                }
            }
            .buttonStyle(.plain)

            // Manual add button
            Button {
                showAddSheet = true
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        Circle()
                            .fill(theme.surfaceColor)
                            .frame(width: 44, height: 44)
                        Image(systemName: "keyboard")
                            .font(.system(size: 18))
                            .foregroundColor(theme.mutedColor)
                    }

                    Text("manual_button")
                        .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.textColor)

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(theme.mutedColor)
                }
                .padding(14)
                .card3D(theme)
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - Scanning Animation (吞吞 reading the label)
    private var scanningAnimation: some View {
        VStack(spacing: 20) {
            // Scan frame with 吞吞 peeking
            ZStack {
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .fill(
                        theme.isPro
                        ? LinearGradient(colors: [Color(hex: "#1A1A1A"), Color(hex: "#0A0A0A")], startPoint: .topLeading, endPoint: .bottomTrailing)
                        : LinearGradient(colors: [Color(hex: "#F4F2E7"), Color(hex: "#EFEDE2")], startPoint: .topLeading, endPoint: .bottomTrailing)
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 24, style: .continuous)
                            .stroke(theme.accentColor.opacity(0.6), lineWidth: 2)
                    }
                    .frame(width: 240, height: 240)

                // Scan line
                Rectangle()
                    .fill(
                        LinearGradient(
                            colors: [.clear, theme.accentColor, .clear],
                            startPoint: .leading, endPoint: .trailing
                        )
                    )
                    .frame(width: 200, height: 2)
                    .shadow(color: theme.accentColor, radius: 8)
                    .phaseAnimator([false, true]) { content, phase in
                        content.offset(y: phase ? 90 : -90)
                    } animation: { _ in .easeInOut(duration: 1.5).repeatForever(autoreverses: true) }

                // Corner brackets
                ForEach(0..<4, id: \.self) { corner in
                    let x: CGFloat = corner % 2 == 0 ? -90 : 90
                    let y: CGFloat = corner < 2 ? -90 : 90
                    CornerBracket(corner: corner)
                        .stroke(theme.accentColor.opacity(0.5), lineWidth: 2)
                        .frame(width: 20, height: 20)
                        .offset(x: x, y: y)
                }

                // Pill icon in center
                Image(systemName: "pill.fill")
                    .font(.system(size: 40))
                    .foregroundColor(theme.accentColor.opacity(0.3))
                    .rotationEffect(.degrees(45))
                    .phaseAnimator([false, true]) { content, phase in
                        content.opacity(phase ? 0.15 : 0.4)
                    } animation: { _ in .easeInOut(duration: 1.2).repeatForever(autoreverses: true) }
            }

            // 吞吞 below the scan frame, "reading" the label
            VStack(spacing: 6) {
                MascotView(mood: .perfect, size: 80, showBackground: false)

                Text("scan_processing")
                    .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                    .foregroundColor(theme.textColor)
                    .phaseAnimator([false, true]) { content, phase in
                        content.opacity(phase ? 0.5 : 1)
                    } animation: { _ in .easeInOut(duration: 1).repeatForever(autoreverses: true) }
            }
        }
        .padding(.vertical, 20)
    }

    // MARK: - Result
    private var resultView: some View {
        VStack(spacing: 16) {
            if let result = scanResult {
                // 吞吞 happy with success
                VStack(spacing: 6) {
                    MascotView(mood: .celebrating, size: 90, showBackground: true)

                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(theme.successColor)
                        Text("scan_found")
                            .font(.system(size: theme.bodySize, weight: .semibold, design: .rounded))
                            .foregroundColor(theme.successColor)
                    }
                }
                .padding(.bottom, 4)

                // Result card
                VStack(alignment: .leading, spacing: 12) {
                    resultRow("name_label", value: result.name)
                    resultRow("dosage_label", value: result.dosage)
                    resultRow("freq_label", value: LocalizedStringKey(result.frequency.localizationKey))
                    resultRow("time_label", value: LocalizedStringKey(result.timeOfDay.localizationKey))
                    resultRow("food_label_short", value: LocalizedStringKey(result.foodRelation.localizationKey))
                }
                .padding(16)
                .card3D(theme)

                // Actions
                HStack(spacing: 12) {
                    Button {
                        startScan()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "arrow.counterclockwise")
                            Text("scan_retry")
                        }
                        .font(.system(size: 15, weight: .semibold, design: .rounded))
                        .foregroundColor(theme.textColor)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background {
                            RoundedRectangle(cornerRadius: 16, style: .continuous)
                                .fill(theme.surfaceColor)
                                .overlay {
                                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                                        .stroke(theme.borderColor, lineWidth: 1)
                                }
                        }
                    }

                    Button {
                        confirmScan()
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "sparkles")
                            Text("scan_confirm")
                        }
                        .font(.system(size: 15, weight: .semibold, design: .rounded))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(theme.accentGradient, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    }
                }
            }
        }
    }

    private func resultRow(_ key: LocalizedStringKey, value: String) -> some View {
        HStack {
            Text(key)
                .font(.system(size: theme.captionSize, design: .rounded))
                .foregroundColor(theme.mutedColor)
                .frame(width: 80, alignment: .leading)
            Text(value)
                .font(.system(size: theme.bodySize, weight: .medium, design: .rounded))
                .foregroundColor(theme.textColor)
        }
    }

    private func resultRow(_ key: LocalizedStringKey, value: LocalizedStringKey) -> some View {
        HStack {
            Text(key)
                .font(.system(size: theme.captionSize, design: .rounded))
                .foregroundColor(theme.mutedColor)
                .frame(width: 80, alignment: .leading)
            Text(value)
                .font(.system(size: theme.bodySize, weight: .medium, design: .rounded))
                .foregroundColor(theme.textColor)
        }
    }

    // MARK: - Actions
    private func startScan() {
        mode = .scanning
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()

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
    let corner: Int

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
