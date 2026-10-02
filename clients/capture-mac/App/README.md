# App

The menu-bar app around `../Core`: reading which apps use audio (`ProcessAudioProbe`), capturing the call app's output
(`AppAudioCapture`, ScreenCaptureKit) and the microphone (`MicCapture`), the WebSocket to the ingest (`IngestClient`), one
call's capture (`CaptureSession`), settings with the API key in the Keychain (`Settings`, `SettingsWindow`), the notice
(`Notifier`), the menu and flow (`AppDelegate`), and the command-line checks (`DevCommands`).
