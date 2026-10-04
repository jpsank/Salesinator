- **Closing a tab from the "more tabs" list now removes it from the list at once.** When many tabs were open and some were hidden behind the overflow dropdown,
  closing one from the dropdown closed the tab but left its row listed until the dropdown was reopened. The row now disappears as you close it, and the dropdown
  closes when it empties. (Dockview builds that list once when it opens; the terminal's tab header now takes the closed row out itself.)
