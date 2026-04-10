# PillPal (药搭子) — Xcode 项目搭建 + App Store 上架完整指南

> 目标平台: iOS 17.0+  |  开发语言: Swift 5.9+ / SwiftUI  |  Xcode 15+

---

## 第一部分：Xcode 创建项目

### 步骤 1 — 新建 Xcode 项目

1. 打开 Xcode → **File → New → Project**
2. 选择模板: **iOS → App** → Next
3. 填写项目信息:
   - **Product Name**: `PillPal`
   - **Team**: 选择你的 Apple Developer 账号（需要先在 Xcode → Settings → Accounts 登录）
   - **Organization Identifier**: `com.yourname`（例如 `com.pillpal`）
   - **Bundle Identifier**: 自动生成为 `com.pillpal.PillPal`
   - **Interface**: **SwiftUI**
   - **Language**: **Swift**
   - **Storage**: **None**（我们用 UserDefaults）
   - 不勾选 Include Tests（MVP 阶段可以先不加）
4. 选择保存位置 → **Create**

### 步骤 2 — 导入源代码

1. 在 Xcode 左侧 Project Navigator 中，右键 `PillPal` 文件夹
2. 选择 **Add Files to "PillPal"...**
3. 导航到本仓库的 `PillPal-iOS/PillPal/` 目录
4. **按以下顺序** 逐个添加文件夹（勾选 "Create groups"，不要选 "Create folder references"）:

```
Models/        → Medication.swift
ViewModels/    → MedicationStore.swift
Theme/         → ThemeManager.swift
Views/         → 所有子文件夹和 .swift 文件
Localization/  → en.lproj, zh-Hans.lproj, fr.lproj
```

5. 替换自动生成的文件:
   - 删除 Xcode 自动生成的 `ContentView.swift` 和 `PillPalApp.swift`
   - 将本项目的同名文件拖入替代

### 步骤 3 — 配置本地化

1. 选中项目根节点（最上方蓝色图标）→ **Project** (不是 Target) → **Info** 标签
2. 找到 **Localizations** 区域，点击 **+** 按钮:
   - 添加 **Chinese, Simplified (zh-Hans)**
   - 添加 **French (fr)**
3. Xcode 会询问要本地化哪些文件 → 全选 → Finish
4. 确认 `Localizable.strings` 在 File Inspector（右侧面板）中显示了三种语言

### 步骤 4 — 配置项目设置

选中项目 → **Target: PillPal** → **General** 标签:

| 设置项 | 值 |
|---|---|
| Display Name | `PillPal` |
| Bundle Identifier | `com.yourname.PillPal` |
| Version | `1.0.0` |
| Build | `1` |
| Minimum Deployments | iOS 17.0 |
| Device Orientation | 仅勾选 Portrait |
| Status Bar Style | Light Content |

### 步骤 5 — App Icons

1. 选中 `Assets.xcassets` → 点击 `AppIcon`
2. 准备一张 **1024x1024** 的 PNG 图标（不含透明通道）
3. 设计建议：深色背景 + 渐变药丸图标（与 Pro Mode 风格一致）
4. 将图片拖入 App Icon 的 1024pt slot（Xcode 15+ 会自动生成所有尺寸）

### 步骤 6 — 添加 Info.plist 权限声明

在 **Target → Info → Custom iOS Target Properties** 中添加:

| Key | Value |
|---|---|
| `NSCameraUsageDescription` | `PillPal needs camera access to scan medication labels` |
| `NSPhotoLibraryUsageDescription` | `PillPal needs photo access to import medication labels` |

### 步骤 7 — 编译运行

1. 在顶部选择模拟器（推荐 iPhone 15 Pro）
2. 按 **Cmd + R** 运行
3. 检查所有页面是否正常显示

---

## 第二部分：真机调试

### 步骤 1 — 连接 iPhone

1. 用 USB-C / Lightning 数据线连接 iPhone 到 Mac
2. iPhone 上弹出"信任此电脑" → 点信任
3. Xcode 顶部设备选择器 → 选择你的 iPhone

### 步骤 2 — 开发者模式

- iOS 16+: iPhone → 设置 → 隐私与安全 → 开发者模式 → 开启 → 重启
- 首次安装后需要在 iPhone 上: 设置 → 通用 → VPN与设备管理 → 信任开发者证书

### 步骤 3 — 自动签名

1. Xcode → Target → Signing & Capabilities
2. 勾选 **Automatically manage signing**
3. Team 选择你的 Apple ID
4. 如果 Bundle ID 冲突，修改为唯一值（如加上你的名字缩写）

### 步骤 4 — 运行到真机

按 **Cmd + R**，等待编译、安装。第一次可能需要 30-60 秒。

---

## 第三部分：上架 App Store

### 前置条件

| 需要 | 说明 |
|---|---|
| Apple Developer Program | $99/年，在 developer.apple.com 注册 |
| Mac + Xcode 15+ | 已安装并登录开发者账号 |
| App Store Connect 账号 | 用你的 Apple ID 登录 appstoreconnect.apple.com |

### 步骤 1 — 在 App Store Connect 创建 App

1. 登录 [App Store Connect](https://appstoreconnect.apple.com)
2. **我的 App** → 点击左上 **+** → **新建 App**
3. 填写:
   - 平台: **iOS**
   - 名称: `PillPal - 药搭子`
   - 主要语言: **English (U.S.)**
   - Bundle ID: 选择你在 Xcode 中配置的 `com.yourname.PillPal`
   - SKU: `pillpal-v1`（任意唯一字符串）
4. 点击 **创建**

### 步骤 2 — 填写 App 信息

在 App Store Connect 中填写以下内容:

#### App 信息 (App Information)
- 类别: **Health & Fitness** (主) / **Medical** (副)
- 内容分级: 填写调查问卷（无暴力、无赌博等 → 获得 4+ 评级）

#### 价格与销售范围
- 价格: **免费**（应用内购买单独设置）
- 销售范围: 选择所有需要上架的国家/地区

#### App 隐私
- 隐私政策 URL: 填写你的 GitHub Pages 链接（如 `https://chiang126126.github.io/privacy.html`）
- 数据类型: 声明收集的数据（健康数据 → 用于 App 功能 → 不与第三方共享）

### 步骤 3 — 准备截图和预览

App Store 需要的截图尺寸:

| 设备 | 尺寸 (px) | 必需 |
|---|---|---|
| iPhone 6.7" (15 Pro Max) | 1290 x 2796 | 是 |
| iPhone 6.5" (11 Pro Max) | 1242 x 2688 | 是 |
| iPad Pro 12.9" | 2048 x 2732 | 如果支持 iPad |

**截图建议 (5-8 张)**:
1. Onboarding 欢迎页（展示品牌）
2. Dashboard 主页（展示核心功能）
3. AI 扫描药瓶页面（核心卖点）
4. BubblePop 打卡交互（趣味性）
5. 毒舌提醒卡片（差异化）
6. Pro Mode vs Care Mode 对比
7. 统计页面（成就系统）
8. 多语言展示

**制作方式**:
- 在模拟器运行 App → Cmd+S 截图
- 使用 [AppMockUp](https://app-mockup.com) 或 Figma 加上设备边框和营销文案

### 步骤 4 — 填写版本信息

在 App Store Connect → 你的 App → iOS App 1.0:

```
App 名称: PillPal - 药搭子
副标题: Your Fun Supplement Tracker
推广文本: Scan bottles with AI. Never miss a dose.

描述 (Description):
PillPal (药搭子) is the fun, vibrant way to track your medications
and supplements. No more forgotten doses!

• SCAN & GO — Point your camera at any bottle label. AI extracts
  the name, dosage, and schedule instantly.
• FUN REMINDERS — Choose sassy ("Your liver is protesting!")
  or gentle notifications. Never boring.
• BUBBLE POP — Satisfying animations when you log each dose.
  Build streaks and earn achievement badges.
• DUAL MODE — Pro Mode (dark cyberpunk) for young professionals,
  Care Mode (warm, large text) for elders.
• TRILINGUAL — English, 中文, Français built-in.

Free for up to 3 medications. Upgrade to Premium for unlimited
tracking, AI health insights, and Family Share.

关键词 (Keywords):
pill,medication,tracker,supplement,vitamin,reminder,health,药物,提醒

技术支持 URL: https://chiang126126.github.io/support.html
营销 URL: (可选)
```

### 步骤 5 — 配置应用内购买 (IAP)

1. App Store Connect → 你的 App → **应用内购买** (In-App Purchases)
2. 点击 **+** → 类型选 **Auto-Renewable Subscription**
3. 创建订阅组: `PillPal Premium`
4. 添加两个订阅:

| 名称 | Product ID | 价格 |
|---|---|---|
| Monthly Premium | `com.yourname.pillpal.premium.monthly` | $4.99 |
| Yearly Premium | `com.yourname.pillpal.premium.yearly` | $29.99 |

5. 为每个订阅填写描述和本地化信息
6. 提交截图（订阅确认界面的截图）

> 注意: MVP 阶段可以先不实现 IAP 代码，只在 Settings 中展示 UI。
> 真正接入 StoreKit 2 需要额外开发。

### 步骤 6 — 构建并上传 Archive

1. Xcode → 顶部设备选择 **Any iOS Device (arm64)**
2. **Product → Archive** (Cmd+Shift+B 先 build 检查无错误)
3. Archive 完成后自动弹出 **Organizer** 窗口
4. 选中刚构建的 Archive → 点击 **Distribute App**
5. 选择 **App Store Connect** → 按照向导一路 Next
6. 上传完成后等待约 15-30 分钟处理

### 步骤 7 — 提交审核

1. 回到 App Store Connect → 你的 App → iOS App 1.0
2. 在 **构建版本** 区域选择刚上传的 Build
3. 填写 **App 审核信息**:
   - 联系人姓名、电话、邮箱
   - 登录信息（如果 App 需要登录 → 本 App 不需要，选"不需要"）
   - 审核备注: "This is a medication tracking app. The AI scan feature
     currently uses demo data for simulation. No real medical advice
     is provided."
4. 点击 **提交以供审核** (Submit for Review)

### 步骤 8 — 审核等待

- 首次审核通常 24-48 小时
- 常见被拒原因和对策:

| 被拒原因 | 解决方案 |
|---|---|
| 4.2 — Minimum Functionality | 确保有完整的 CRUD 功能和真实使用场景 |
| 5.1.1 — Data Collection | 在隐私政策中声明所有收集的数据 |
| 2.3.1 — Hidden Features | 不要有隐藏功能，审核备注中说明 AI 扫描为 Demo |
| 3.1.1 — IAP | 如果有订阅，必须使用 Apple IAP，不能用第三方支付 |
| 5.1.2 — Health Claims | 不要声称有医疗效果，只是"追踪器" |

### 步骤 9 — 审核通过后

1. 选择发布方式: **自动发布** 或 **手动发布**
2. 推荐选手动 → 审核通过后自己选时机点"发布"
3. 发布后约 24 小时全球可搜索下载

---

## 第四部分：后续版本迭代清单

### V1.1 — 真实 AI 扫描
```swift
// 接入 GPT-4o Vision API
// 在 ScanView.swift 中替换 demo 逻辑:
import AVFoundation

// 1. 用 AVCaptureSession 获取相机画面
// 2. 拍照后将图片 base64 编码
// 3. 发送到 OpenAI Vision API
// 4. 解析返回的 JSON → 自动填充表单
```

### V1.2 — 本地推送通知
```swift
import UserNotifications

// 在 PillPalApp.swift 中请求权限:
func requestNotificationPermission() {
    UNUserNotificationCenter.current().requestAuthorization(
        options: [.alert, .sound, .badge]
    ) { granted, _ in
        print("Notification permission: \(granted)")
    }
}

// 为每个药物创建定时提醒:
func scheduleReminder(for med: Medication) {
    let content = UNMutableNotificationContent()
    content.title = "PillPal"
    content.body = getRandomReminder(style: store.reminderStyle)
    content.sound = .default

    var components = DateComponents()
    switch med.timeOfDay {
    case .morning: components.hour = 8
    case .afternoon: components.hour = 13
    case .evening: components.hour = 19
    case .bedtime: components.hour = 22
    }

    let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: true)
    let request = UNNotificationRequest(identifier: med.id.uuidString, content: content, trigger: trigger)
    UNUserNotificationCenter.current().add(request)
}
```

### V2.0 — 家庭共享 (Family Share)
- 接入 CloudKit 或 Firebase Realtime Database
- 允许通过邀请码关联家庭成员
- 子女端显示父母的今日服药状态
- 漏服超过 2 小时自动发送推送给家庭成员

### V2.1 — Widget & Apple Watch
- 使用 WidgetKit 创建桌面小组件（显示今日进度）
- Apple Watch 端可快速打卡

---

## 附录：项目文件结构

```
PillPal-iOS/
└── PillPal/
    ├── PillPalApp.swift                        # App 入口
    ├── ContentView.swift                       # 主视图 + 自定义 TabBar
    │
    ├── Models/
    │   └── Medication.swift                    # 数据模型 (Medication, DoseLog, Achievement, etc.)
    │
    ├── ViewModels/
    │   └── MedicationStore.swift               # 数据管理 (@Observable, UserDefaults 持久化)
    │
    ├── Theme/
    │   └── ThemeManager.swift                  # Pro/Care 双模式主题
    │
    ├── Views/
    │   ├── Onboarding/
    │   │   └── OnboardingView.swift            # 引导页 (3步 + 欢迎页)
    │   ├── Dashboard/
    │   │   └── DashboardView.swift             # 首页 (今日药物 + 周报 + 提醒)
    │   ├── Medications/
    │   │   ├── MedicationsListView.swift        # 药物列表 (展开/折叠/删除)
    │   │   └── AddMedicationView.swift          # 添加药物表单
    │   ├── Scan/
    │   │   └── ScanView.swift                  # AI 扫描 (选择/动画/结果)
    │   ├── Stats/
    │   │   └── StatsView.swift                 # 统计 (数据卡片 + 周图表 + 成就)
    │   ├── Settings/
    │   │   └── SettingsView.swift              # 设置 (主题/语言/提醒/数据/关于)
    │   └── Components/
    │       ├── BubblePopButton.swift            # 气泡弹爆打卡按钮
    │       ├── MoodAvatar.swift                 # 情绪小人 (根据服药率变表情)
    │       ├── StreakCounter.swift               # 连续打卡计数器 (火焰动画)
    │       ├── ReminderCard.swift               # 毒舌/温柔提醒卡片
    │       ├── WeeklyChartView.swift            # 周进度条形图
    │       └── AchievementBadgeView.swift       # 成就徽章
    │
    └── Localization/
        ├── en.lproj/Localizable.strings         # 英文
        ├── zh-Hans.lproj/Localizable.strings    # 中文
        └── fr.lproj/Localizable.strings         # 法文
```

Total: **18 个 Swift 文件** + **3 个本地化文件** = 完整可编译项目
