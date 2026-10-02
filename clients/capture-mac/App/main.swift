import AppKit

// Command-line checks (--probe, --play …) run and exit; otherwise this is the menu-bar app.
if DevCommands.run(Array(CommandLine.arguments.dropFirst())) { exit(0) }

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
