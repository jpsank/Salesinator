#!/bin/sh
# Compile Core/ with the self-test and run it. Uses swiftc directly: SwiftPM and XCTest both need a full Xcode, and
# this builds with only the Command Line Tools.
set -eu
cd "$(dirname "$0")"
SDK="$(xcrun --show-sdk-path)"
mkdir -p .build
swiftc -sdk "$SDK" -target arm64-apple-macos13.0 Core/*.swift Tests/main.swift -o .build/selftest
./.build/selftest
