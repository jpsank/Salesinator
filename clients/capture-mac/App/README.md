# App

The menu-bar app around `../Core`: reading which apps use audio (`ProcessAudioProbe`), capturing the call app's output
(`AppAudioCapture`, ScreenCaptureKit), finding the call's link in the browsers (`BrowserLinks`) and sending the bot (`BotClient`) and the microphone (`MicCapture`), the WebSocket to the ingest (`IngestClient`), one
call's capture (`CaptureSession`), settings with the API key in the Keychain (`Settings`, `SettingsWindow`), the notice
(`Notifier`), the menu and flow (`AppDelegate`), and the command-line checks (`DevCommands`).
Pairing with a Vexa deployment (the `vexacapture://` link, the confirmation, the exchange) and Open at login are in `AppDelegate`; the link and answer parsing is in `../Core/ConnectLink.swift`.
