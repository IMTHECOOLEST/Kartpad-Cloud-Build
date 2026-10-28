// PadMint for Mac: a native window around the same local PadMint page.
// It starts "python3 -m padmint ui" from the app's Resources, shows the page it serves
// (127.0.0.1 with a random token) and stops it on quit. Players' games, tools and builds
// stay in ~/.padmint, as with the ZIP download.
import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var output = ""
    var home: URL?
    let spinner = NSProgressIndicator()
    let status = NSTextField(wrappingLabelWithString: "Starting PadMint…")

    func applicationDidFinishLaunching(_ note: Notification) {
        let config = WKWebViewConfiguration()
        config.userContentController.addUserScript(WKUserScript(
            source: "document.documentElement.classList.add('native')",
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 980, height: 820), configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 980, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "PadMint"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(red: 0.059, green: 0.090, blue: 0.078, alpha: 1)
        window.minSize = NSSize(width: 560, height: 520)
        // A native "starting" view until PadMint's page has loaded, then the page itself.
        let container = NSView()
        container.wantsLayer = true
        container.layer?.backgroundColor = window.backgroundColor.cgColor
        web.translatesAutoresizingMaskIntoConstraints = false
        web.isHidden = true
        // PadMint looks in Downloads for game files; macOS holds it until the player answers
        // its privacy question, which can end up behind other windows.
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { [weak self] in
            guard let self = self, self.web.isHidden, !self.spinner.isHidden else { return }
            self.status.stringValue = "Still starting PadMint…\n\nIf macOS asked PadMint a question, answer it to continue."
        }
        spinner.style = .spinning
        spinner.translatesAutoresizingMaskIntoConstraints = false
        spinner.startAnimation(nil)
        status.textColor = NSColor(red: 0.62, green: 0.70, blue: 0.66, alpha: 1)
        status.alignment = .center
        status.font = .systemFont(ofSize: 14)
        status.isSelectable = true
        status.translatesAutoresizingMaskIntoConstraints = false
        for view in [web!, spinner, status] as [NSView] { container.addSubview(view) }
        NSLayoutConstraint.activate([
            web.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            web.topAnchor.constraint(equalTo: container.topAnchor),
            web.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            spinner.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            spinner.centerYAnchor.constraint(equalTo: container.centerYAnchor, constant: -16),
            status.topAnchor.constraint(equalTo: spinner.bottomAnchor, constant: 14),
            status.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            status.widthAnchor.constraint(lessThanOrEqualTo: container.widthAnchor, constant: -80),
        ])
        window.contentView = container
        window.setFrameAutosaveName("PadMintWindow")
        if !window.setFrameUsingName("PadMintWindow") { window.center() }
        window.makeKeyAndOrderFront(nil)
        buildMenu()
        start()
    }

    func start() {
        let python = URL(fileURLWithPath: "/usr/bin/python3")
        guard let app = Bundle.main.resourceURL?.appendingPathComponent("app") else { return }
        // /usr/bin/python3 is a stub until Apple's command line tools are installed; asking it
        // for its version makes macOS offer to install them.
        let probe = Process()
        probe.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
        probe.arguments = ["-p"]
        probe.standardOutput = FileHandle.nullDevice
        probe.standardError = FileHandle.nullDevice
        try? probe.run()
        probe.waitUntilExit()
        if probe.terminationStatus != 0 {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
            p.arguments = ["--install"]
            try? p.run()
            fail("PadMint needs Apple's free command line tools (they include Python and Git). " +
                 "macOS is offering to install them: click Install, wait until it finishes, then open PadMint again.")
            return
        }
        let process = Process()
        process.executableURL = python
        process.arguments = ["-m", "padmint", "ui", "--no-open"]
        process.currentDirectoryURL = app
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"  // the app bundle stays read-only and signed
        process.environment = env
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.read(text) }
        }
        process.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                guard let self = self, !self.quitting else { return }
                self.fail("PadMint stopped (code \(p.terminationStatus)).\n\n" + self.output.suffix(1500))
            }
        }
        do { try process.run(); server = process } catch { fail("Could not start PadMint: \(error)") }
    }

    func read(_ text: String) {
        output += text
        guard home == nil,
              let range = output.range(of: #"http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_\-]+"#, options: .regularExpression),
              let url = URL(string: String(output[range])) else { return }
        home = url
        web.load(URLRequest(url: url, timeoutInterval: 20))
    }

    // If the page doesn't load, try again a few times, then say so instead of spinning forever.
    var attempts = 0
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        retry(error)
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if webView.isHidden { retry(error) }
    }
    func retry(_ error: Error) {
        guard let home = home, !quitting else { return }
        attempts += 1
        if attempts > 3 {
            fail("PadMint started but its page didn't load (\(error.localizedDescription)).\n\nQuit PadMint and open it again. If it keeps happening, report it at github.com/chrissotraidis/padmint/issues.")
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.web.load(URLRequest(url: home, timeoutInterval: 20)) }
    }

    func fail(_ message: String) {
        web.isHidden = true
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        status.stringValue = message
        status.textColor = NSColor(red: 0.91, green: 0.95, blue: 0.93, alpha: 1)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView.url?.host == "127.0.0.1", webView.isHidden else { return }
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        status.isHidden = true
        webView.isHidden = false
    }

    // The page's own links stay in the window; everything else opens in the web browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let host = url.host, host != "127.0.0.1" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
        completionHandler()
    }

    var quitting = false
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateNow }
        web.evaluateJavaScript("typeof last!=='undefined'&&last&&last.state") { [weak self] result, _ in
            guard let self = self else { return }
            if (result as? String) == "running" {
                let alert = NSAlert()
                alert.messageText = "A copy is still being made"
                alert.informativeText = "Quitting stops it. PadMint keeps the finished work, so the next try is faster."
                alert.addButton(withTitle: "Keep Making")
                alert.addButton(withTitle: "Quit")
                if alert.runModal() == .alertFirstButtonReturn {
                    sender.reply(toApplicationShouldTerminate: false)
                    return
                }
            }
            self.stop()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func stop() {
        quitting = true
        guard let server = server, server.isRunning else { return }
        server.interrupt()  // like Ctrl-C: PadMint cancels a running build and keeps finished work
        let deadline = Date().addingTimeInterval(8)
        while server.isRunning && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.1)) }
        if server.isRunning { server.terminate() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About PadMint", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide PadMint", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit PadMint", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        let viewItem = NSMenuItem()
        main.addItem(viewItem)
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        viewItem.submenu = view
        let windowItem = NSMenuItem()
        main.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = windowMenu
        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu
    }

    @objc func reload() { if let home = home { web.load(URLRequest(url: home)) } }
}

let app = NSApplication.shared
let delegate = AppDelegate()
// Closed by a signal (a logout, "kill"): stop PadMint the same way Quit does, so no server is left behind.
var stopSignals: [DispatchSourceSignal] = []
for number in [SIGTERM, SIGINT, SIGHUP] {
    signal(number, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
    source.setEventHandler { delegate.stop(); exit(0) }
    source.resume()
    stopSignals.append(source)
}
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()

